#!/usr/bin/env python3
"""
KFX writer — serializes a comic book directly into a single-container KFX
file (CONT format), replacing the Calibre "KFX Output" plugin step.

The container layout and the final ("fixed-up") fragment forms were derived
by black-box comparison: generating a KPF with kpf_generator, converting it
with the jhowell KFX Output plugin, and diffing both containers fragment by
fragment. Key differences from the prepub KPF form:

  - Structure fragments ($608) are inlined into storylines ($259); their
    $598 self-references become integer EIDs stored under $155.
  - Auxiliary data ($597), eid hash buckets ($610), section pid counts
    ($611) and section position id maps ($609) are not emitted; instead
    the KFX carries position maps ($264/$265), an approximate location
    map ($550), book navigation ($389/$395) and a container entity map
    ($419).
  - Raw media fragment ids gain a "resource/" prefix.
  - book_metadata ($490) is completed with ASIN/asset_id/content_id,
    cde_content_type PDOC, is_sample and override_kindle_font.
  - content_features ($585) gains the SDK.Marker CanonicalFormat feature.

Container physical layout (all Ion payloads start with the BVM):

  "CONT" | u16 version=2 | u32 header_len | u32 info_off | u32 info_len
  entity index table (u32 id_sid, u32 type_sid, u64 off, u64 len) ...
  doc symbol table ($3-annotated, import YJ_symbols v10 max_id 851)
  format capabilities ($593-annotated)
  container info (Ion struct: $409..$595)
  kfxgen info (quirky JSON: unquoted key/value field names)
  entities: "ENTY" | u16 1 | u32 hdr_len | Ion {$410:0,$411:0} | payload
"""

import argparse
import hashlib
import os
import random
import shutil
import string
import struct
import sys
import tempfile

try:
    # Calibre plugin context: the flat plugin zip exposes modules under
    # calibre_plugins.kfx_comic_output.*; alias the module so the shared
    # import list below works in both contexts without duplication.
    from calibre_plugins.kfx_comic_output import kpf_generator as _kpf_mod
    sys.modules.setdefault("kpf_generator", _kpf_mod)
except ImportError:
    pass

from kpf_generator import (
    DEFAULT_GAMMA, TOOL_VERSION, _ION_BVM,
    _int_to_base32, _ion_type_descriptor, gamma_correct_batch,
    group_pages, read_page_info, section_display_box,
    ion_annotation, ion_bool, ion_int, ion_list, ion_string, ion_struct,
    ion_symbol,
    SYM_FORMAT_VERSION, SYM_WIDTH, SYM_HEIGHT, SYM_PAGE_WIDTH,
    SYM_PAGE_HEIGHT, SYM_PAGE_TEMPLATE_TYPE, SYM_SECTION_CONTENT,
    SYM_CHILDREN, SYM_LAYOUT_TYPE, SYM_NODE_TYPE, SYM_FORMAT, SYM_LOCATION,
    SYM_READING_ORDERS, SYM_SECTIONS, SYM_SECTION_ID, SYM_RESOURCE_ID,
    SYM_STORYLINE_ID, SYM_READING_ORDER_NAME, SYM_POSITION_MAP,
    SYM_FIT_TYPE, SYM_EID, SYM_LAYOUT, SYM_METADATA, SYM_VALUE, SYM_KEY,
    SYM_CONTAINER, SYM_LEAF, SYM_PNG, SYM_JPG, SYM_FIXED, SYM_BLOCK,
    SYM_FIT_BOTH, SYM_FIXED_LAYOUT, SYM_DEFAULT, SYM_FIXED_LAYOUT_MODE_RTL,
    SYM_FIXED_LAYOUT_MODE_LTR, SYM_ABSOLUTE, SYM_IMAGE_WIDTH,
    SYM_IMAGE_HEIGHT, SYM_VIRTUAL_PANEL_DIR, SYM_SPREAD_LAYOUT,
    SYM_RIGHT_TO_LEFT_BINDING, SYM_CATEGORIES, SYM_CATEGORY_NAME,
    SYM_POSITION_TYPE, SYM_RIGHT_TO_LEFT_PAGE, SYM_LEFT_TO_RIGHT_PAGE,
    SYM_PAGE_PROGRESSION_DIR, SYM_BINDING, SYM_NAMESPACE,
    SYM_MAJOR_VERSION, SYM_MINOR_VERSION, SYM_PROPERTIES,
    SYM_FEATURES_LIST, SYM_SYS_MAX_ID,
)

# Application tag recorded in the container header (kfxgen_application_version)
APP_VERSION = "kindle-comic-workaround"

# Fragment-type / field symbol ids used only on the KFX side
SYM_ION_SYMBOL_TABLE = 3      # $3   ($ion_symbol_table annotation)
SYM_IMPORT_NAME = 4           # $4
SYM_IMPORT_VERSION = 5        # $5   (also the "version" field key)
SYM_IMPORTS = 6               # $6
SYM_SYMBOLS = 7               # $7
SYM_OFFSET = 143              # $143
SYM_ELEMENT_TYPE_155 = 155    # $155 (EID holder / entity name in $419)
SYM_CONTAINER_FORMAT = 161    # $161
SYM_LOCATION_MAP_ENTRIES = 182  # $182
SYM_POSITION_INDEX = 184      # $184
SYM_METADATA_FRAG = 258       # $258
SYM_STORYLINE_FRAG = 259      # $259
SYM_SECTION_FRAG = 260        # $260
SYM_POSITION_ID_MAP = 264     # $264
SYM_POSITION_EID_MAP = 265    # $265
SYM_NAV_CONTAINERS = 392      # $392
SYM_BOOK_NAVIGATION = 389     # $389
SYM_NAV_395 = 395             # $395
SYM_NAV_UNITS = 247           # $247
SYM_SINGLETON = 348           # $348 (entity id for unnamed fragments)
SYM_EXTERNAL_RESOURCE_FRAG = 164  # $164
SYM_RAW_MEDIA = 417           # $417
SYM_CONTAINER_ENTITY_MAP = 419  # $419
SYM_CONTAINER_ID = 409        # $409
SYM_COMPRESSION_TYPE = 410    # $410
SYM_DRM_SCHEME = 411          # $411
SYM_CHUNK_SIZE = 412          # $412
SYM_INDEX_TAB_OFFSET = 413    # $413
SYM_INDEX_TAB_LENGTH = 414    # $414
SYM_DOC_SYMBOL_OFFSET = 415   # $415
SYM_DOC_SYMBOL_LENGTH = 416   # $416
SYM_ENTITY_IDS = 252          # $252
SYM_ENTITY_DEPS = 253         # $253
SYM_ENTITY_DEP_LIST = 254     # $254
SYM_BOOK_METADATA_FRAG = 490  # $490
SYM_DOCUMENT_DATA_FRAG = 538  # $538
SYM_LOCATION_MAP = 550        # $550
SYM_CONTENT_FEATURES_FRAG = 585  # $585
SYM_FORMAT_CAPABILITIES = 593  # $593
SYM_FC_OFFSET = 594           # $594
SYM_FC_LENGTH = 595           # $595
SYM_KFXGEN_APP_VERSION = 587  # $587 (unused in header; JSON carries it)

# YJ_symbols shared table span: 9 system symbols + 842 shared = 851;
# local symbols start at 852.
YJ_SYMBOLS_MAX_ID = 851
LOCAL_SID_BASE = 852

CONTAINER_VERSION = 2
CHUNK_SIZE = 4096

_ID_ALPHABET = string.ascii_uppercase + string.digits


class _LocalSymbols:
    """Local symbol table: names registered in SID order from 852."""

    def __init__(self):
        self._names: list[str] = []
        self._sids: dict[str, int] = {}

    def sid(self, name: str) -> int:
        if name not in self._sids:
            self._sids[name] = LOCAL_SID_BASE + len(self._names)
            self._names.append(name)
        return self._sids[name]

    @property
    def names(self) -> list[str]:
        return list(self._names)


def _random_id(length: int) -> str:
    return "".join(random.choice(_ID_ALPHABET) for _ in range(length))


def _natural_key(s: str) -> str:
    """Natural sort key (digit runs zero-padded, case-folded).

    Matches the ordering the KFX Output plugin uses when it rebuilds the
    local symbol table, so our SID assignment is identical to plugin
    output for any book size.
    """
    import re
    return "".join("00000000"[len(c):] + c if c.isdigit() else c
                   for c in re.split(r"([0-9]+)", s.lower()))


def ion_decimal_int(val: int) -> bytes:
    """Encode a non-negative integer as an Ion decimal (exponent 0).

    Matches the byte form the KFX Output plugin emits for numeric fields
    it normalizes to decimals ($422/$423 image dimensions, $16 format
    version): VarInt exponent 0 (0x80) + signed-magnitude coefficient.
    The Kindle comic renderer reads these as decimals, so the type (not
    just the value) must match.
    """
    if val == 0:
        return _ion_type_descriptor(5, 0)
    coeff = val.to_bytes((val.bit_length() + 7) // 8, "big")
    if coeff[0] & 0x80:
        coeff = b"\x00" + coeff       # keep sign bit clear (positive)
    payload = b"\x80" + coeff         # VarInt exponent 0
    return _ion_type_descriptor(5, len(payload)) + payload


# ===========================================================================
# Book model
# ===========================================================================

def _build_model(page_info: list[dict], facing_pages: bool,
                 facing_start: str) -> list[dict]:
    """Group pages into sections and assign names plus integer EIDs.

    Names (c/l per section, e/rsrc per image) use the same base-32
    scheme as the KPF generator's IdAllocator so both outputs stay
    recognizably parallel.

    EIDs replicate the exact numbers the KFX Output plugin produces for
    our KPFs (kfxlib assigns them from symbol-creation order while
    parsing): after 3 yj.authoring locals comes d0 (855), the section
    names (856..), the per-image aux ids, then one block per section —
    stride 3 + 4*n_images — with the page structure at +0 and each
    image's container/leaf at +3/+4 (+5/+6 for the second facing page).
    Verified byte-exact against plugin output for single, facing
    single/double and 212-page books.
    """
    groups = group_pages(len(page_info), facing_pages, facing_start)
    sections = []
    img_idx = 0
    # First section block: 852 (local base) + 3 (yj.authoring) + 1 (d0)
    # + one slot per section name + one slot per image aux id.
    eid = LOCAL_SID_BASE + 4 + len(groups) + len(page_info)
    for si, group in enumerate(groups):
        sec = {
            "page_indices": group,
            "is_facing": len(group) == 2,
            "section_name": "c" + _int_to_base32(si),
            "storyline_name": "l" + _int_to_base32(si),
            "struct_eid": eid,
            "images": [],
        }
        for j, _ in enumerate(group):
            sec["images"].append({
                "container_eid": eid + 3 + 2 * j,
                "leaf_eid": eid + 4 + 2 * j,
                "resource_name": "e" + _int_to_base32(img_idx),
                "media_name": "resource/rsrc" + _int_to_base32(img_idx),
            })
            img_idx += 1
        eid += 3 + 4 * len(group)
        sections.append(sec)
    return sections


# ===========================================================================
# Fragment builders (final KFX form; all return bare Ion value bytes)
# ===========================================================================

def _feature(namespace: str, key: str, major: int, minor: int) -> bytes:
    return ion_struct([
        (SYM_NAMESPACE, ion_string(namespace)),
        (SYM_KEY, ion_string(key)),
        (SYM_PROPERTIES, ion_struct([
            (SYM_IMPORT_VERSION, ion_struct([   # $5 = "version"
                (SYM_MAJOR_VERSION, ion_int(major)),
                (SYM_MINOR_VERSION, ion_int(minor)),
            ])),
        ])),
    ])


def _build_content_features(virtual_panels: str) -> bytes:
    features = [_feature("com.amazon.yjconversion", "yj_non_pdf_fixed_layout", 2, 0)]
    if virtual_panels == "off":
        features.append(_feature("com.amazon.yjconversion", "yj_publisher_panels", 2, 0))
    features.append(_feature("SDK.Marker", "CanonicalFormat", 2, 0))
    return ion_struct([(SYM_FEATURES_LIST, ion_list(features))])


def _kv(key: str, value: bytes) -> bytes:
    return ion_struct([(SYM_KEY, ion_string(key)), (SYM_VALUE, value)])


def _build_book_metadata(language: str, virtual_panels: str,
                         cover_resource_name: str, book_id: str,
                         asin: str, asset_id: str) -> bytes:
    panels_value = 0 if virtual_panels != "off" else 1
    cap_category = ion_struct([
        (SYM_CATEGORY_NAME, ion_string("kindle_capability_metadata")),
        (SYM_METADATA, ion_list([
            _kv("yj_publisher_panels", ion_int(panels_value)),
            _kv("yj_fixed_layout", ion_int(1)),
        ])),
    ])
    # Entries sorted by key, matching the plugin's metadata writer
    title_category = ion_struct([
        (SYM_CATEGORY_NAME, ion_string("kindle_title_metadata")),
        (SYM_METADATA, ion_list([
            _kv("ASIN", ion_string(asin)),
            _kv("asset_id", ion_string(asset_id)),
            _kv("book_id", ion_string(book_id)),
            _kv("cde_content_type", ion_string("PDOC")),
            _kv("content_id", ion_string(asin)),
            _kv("cover_image", ion_string(cover_resource_name)),
            _kv("is_sample", ion_bool(False)),
            _kv("language", ion_string(language)),
            _kv("override_kindle_font", ion_bool(False)),
        ])),
    ])
    ebook_category = ion_struct([
        (SYM_CATEGORY_NAME, ion_string("kindle_ebook_metadata")),
        (SYM_METADATA, ion_list([_kv("selection", ion_string("enabled"))])),
    ])
    audit_category = ion_struct([
        (SYM_CATEGORY_NAME, ion_string("kindle_audit_metadata")),
        (SYM_METADATA, ion_list([
            _kv("file_creator", ion_string("KC")),
            _kv("creator_version", ion_string(TOOL_VERSION)),
        ])),
    ])
    return ion_struct([
        (SYM_CATEGORIES, ion_list([
            cap_category, title_category, ebook_category, audit_category,
        ])),
    ])


def _reading_order(syms: _LocalSymbols, sections: list[dict]) -> bytes:
    return ion_struct([
        (SYM_READING_ORDER_NAME, ion_symbol(SYM_DEFAULT)),
        (SYM_SECTIONS, ion_list([
            ion_symbol(syms.sid(s["section_name"])) for s in sections])),
    ])


def _build_metadata(syms: _LocalSymbols, sections: list[dict]) -> bytes:
    return ion_struct([
        (SYM_READING_ORDERS, ion_list([_reading_order(syms, sections)])),
    ])


def _build_document_data(syms: _LocalSymbols, sections: list[dict],
                         max_eid: int, reading_direction: str,
                         virtual_panels: str) -> bytes:
    # Same conventions as the KPF form (see kpf_generator._build_document_data)
    if virtual_panels == "vertical":
        page_dir_sym = SYM_LEFT_TO_RIGHT_PAGE
    else:
        page_dir_sym = SYM_RIGHT_TO_LEFT_PAGE
    if reading_direction == "rtl":
        layout_mode_sym = SYM_FIXED_LAYOUT_MODE_RTL
    else:
        layout_mode_sym = SYM_FIXED_LAYOUT_MODE_LTR
    return ion_struct([
        (SYM_FORMAT_VERSION, ion_decimal_int(16)),
        (SYM_PAGE_PROGRESSION_DIR, ion_symbol(page_dir_sym)),
        (SYM_SYS_MAX_ID, ion_int(max_eid)),
        (SYM_LAYOUT, ion_symbol(layout_mode_sym)),
        (SYM_BINDING, ion_symbol(SYM_RIGHT_TO_LEFT_BINDING)),
        (SYM_READING_ORDERS, ion_list([_reading_order(syms, sections)])),
    ])


def _build_book_navigation() -> bytes:
    return ion_list([ion_struct([
        (SYM_READING_ORDER_NAME, ion_symbol(SYM_DEFAULT)),
        (SYM_NAV_CONTAINERS, ion_list([])),
    ])])


def _build_section(syms: _LocalSymbols, sec: dict, section_width: int,
                   section_height: int, virtual_panels: str) -> bytes:
    layout_sym = SYM_SPREAD_LAYOUT if sec["is_facing"] else SYM_FIXED_LAYOUT
    inline_struct = ion_struct([
        (SYM_ELEMENT_TYPE_155, ion_int(sec["struct_eid"])),
        (SYM_STORYLINE_ID, ion_symbol(syms.sid(sec["storyline_name"]))),
        (SYM_PAGE_WIDTH, ion_int(section_width)),
        (SYM_PAGE_HEIGHT, ion_int(section_height)),
        (SYM_LAYOUT_TYPE, ion_symbol(layout_sym)),
        (SYM_PAGE_TEMPLATE_TYPE, ion_symbol(SYM_FIXED)),
        (SYM_NODE_TYPE, ion_symbol(SYM_CONTAINER)),
    ])
    fields = [
        (SYM_SECTION_ID, ion_symbol(syms.sid(sec["section_name"]))),
        (SYM_SECTION_CONTENT, ion_list([inline_struct])),
    ]
    if virtual_panels != "off":
        fields.append((SYM_VIRTUAL_PANEL_DIR, ion_symbol(SYM_RIGHT_TO_LEFT_BINDING)))
    return ion_struct(fields)


def _inline_leaf(syms: _LocalSymbols, img: dict, disp_w: int,
                 disp_h: int) -> bytes:
    return ion_struct([
        (SYM_ELEMENT_TYPE_155, ion_int(img["leaf_eid"])),
        (SYM_WIDTH, ion_int(disp_w)),
        (SYM_HEIGHT, ion_int(disp_h)),
        (SYM_RESOURCE_ID, ion_symbol(syms.sid(img["resource_name"]))),
        (SYM_POSITION_TYPE, ion_symbol(SYM_ABSOLUTE)),
        (SYM_NODE_TYPE, ion_symbol(SYM_LEAF)),
        (SYM_FIT_TYPE, ion_symbol(SYM_FIT_BOTH)),
    ])


def _inline_container(syms: _LocalSymbols, img: dict, disp_w: int,
                      disp_h: int, is_facing: bool) -> bytes:
    leaf = _inline_leaf(syms, img, disp_w, disp_h)
    if is_facing:
        # Facing containers declare page dimensions ($66/$67) and a
        # fixed-layout page template (see _build_facing_structure_container).
        fields = [
            (SYM_ELEMENT_TYPE_155, ion_int(img["container_eid"])),
            (SYM_POSITION_TYPE, ion_symbol(SYM_ABSOLUTE)),
            (SYM_PAGE_WIDTH, ion_int(disp_w)),
            (SYM_PAGE_HEIGHT, ion_int(disp_h)),
            (SYM_LAYOUT_TYPE, ion_symbol(SYM_FIXED_LAYOUT)),
            (SYM_PAGE_TEMPLATE_TYPE, ion_symbol(SYM_FIXED)),
            (SYM_NODE_TYPE, ion_symbol(SYM_CONTAINER)),
            (SYM_CHILDREN, ion_list([leaf])),
        ]
    else:
        fields = [
            (SYM_ELEMENT_TYPE_155, ion_int(img["container_eid"])),
            (SYM_WIDTH, ion_int(disp_w)),
            (SYM_HEIGHT, ion_int(disp_h)),
            (SYM_POSITION_TYPE, ion_symbol(SYM_ABSOLUTE)),
            (SYM_LAYOUT_TYPE, ion_symbol(SYM_BLOCK)),
            (SYM_NODE_TYPE, ion_symbol(SYM_CONTAINER)),
            (SYM_CHILDREN, ion_list([leaf])),
        ]
    return ion_struct(fields)


def _build_storyline(syms: _LocalSymbols, sec: dict, disp_widths: list[int],
                     disp_heights: list[int]) -> bytes:
    containers = [
        _inline_container(syms, img, disp_widths[i], disp_heights[i],
                          sec["is_facing"])
        for i, img in enumerate(sec["images"])
    ]
    return ion_struct([
        (SYM_STORYLINE_ID, ion_symbol(syms.sid(sec["storyline_name"]))),
        (SYM_CHILDREN, ion_list(containers)),
    ])


def _section_eids(sec: dict) -> list[int]:
    """EIDs of one section in reading/position order."""
    eids = [sec["struct_eid"]]
    for img in sec["images"]:
        eids.append(img["container_eid"])
        eids.append(img["leaf_eid"])
    return eids


def _build_position_id_map(syms: _LocalSymbols, sections: list[dict]) -> bytes:
    # Field order ($181 before $174) mirrors plugin output. The eid list
    # is materialized through an actual set: the plugin builds it from a
    # Python set, so its on-disk order is CPython's int-set iteration
    # order — replicated here for byte fidelity.
    def set_order(eids: list[int]) -> list[int]:
        s = set()
        for e in eids:
            s.add(e)
        return list(s)

    return ion_list([
        ion_struct([
            (SYM_POSITION_MAP, ion_list(
                [ion_int(e) for e in set_order(_section_eids(sec))])),
            (SYM_SECTION_ID, ion_symbol(syms.sid(sec["section_name"]))),
        ])
        for sec in sections
    ])


def _build_position_eid_map(sections: list[dict]) -> bytes:
    entries = []
    pos = 0
    for sec in sections:
        for eid in _section_eids(sec):
            entries.append(ion_struct([
                (SYM_POSITION_INDEX, ion_int(pos)),
                (SYM_EID, ion_int(eid)),
            ]))
            pos += 1
    entries.append(ion_struct([               # terminator sentinel
        (SYM_POSITION_INDEX, ion_int(pos)),
        (SYM_EID, ion_int(0)),
    ]))
    return ion_list(entries)


def _build_location_map(sections: list[dict]) -> bytes:
    # One approximate location per section, anchored at its page structure.
    # Entry field order ($155 before $143) mirrors plugin output.
    return ion_list([ion_struct([
        (SYM_LOCATION_MAP_ENTRIES, ion_list([
            ion_struct([
                (SYM_ELEMENT_TYPE_155, ion_int(sec["struct_eid"])),
                (SYM_OFFSET, ion_int(0)),
            ])
            for sec in sections
        ])),
    ])])


def _build_external_resource(syms: _LocalSymbols, img: dict, fmt: str,
                             width: int, height: int) -> bytes:
    fmt_sym = SYM_JPG if fmt == "jpg" else SYM_PNG
    # Field order mirrors plugin output byte-for-byte
    return ion_struct([
        (SYM_FORMAT, ion_symbol(fmt_sym)),
        (SYM_LOCATION, ion_string(img["media_name"])),
        (SYM_IMAGE_WIDTH, ion_decimal_int(width)),
        (SYM_RESOURCE_ID, ion_symbol(syms.sid(img["resource_name"]))),
        (SYM_IMAGE_HEIGHT, ion_decimal_int(height)),
    ])


def _build_container_entity_map(syms: _LocalSymbols, sections: list[dict],
                                container_id: str) -> bytes:
    # Groups (sections, storylines, resources, media) with plain ASCII
    # order inside each group, mirroring plugin output.
    entity_names = (
        sorted(s["section_name"] for s in sections) +
        sorted(s["storyline_name"] for s in sections) +
        sorted(img["resource_name"] for s in sections for img in s["images"]) +
        sorted(img["media_name"] for s in sections for img in s["images"])
    )
    deps = []
    for sec in sorted(sections, key=lambda s: s["section_name"]):
        deps.append(ion_struct([
            (SYM_ELEMENT_TYPE_155, ion_symbol(syms.sid(sec["section_name"]))),
            (SYM_ENTITY_DEP_LIST, ion_list([
                ion_symbol(syms.sid(img["resource_name"]))
                for img in sec["images"]])),
        ]))
    all_images = [img for s in sections for img in s["images"]]
    for img in sorted(all_images, key=lambda x: x["resource_name"]):
        deps.append(ion_struct([
            (SYM_ELEMENT_TYPE_155, ion_symbol(syms.sid(img["resource_name"]))),
            (SYM_ENTITY_DEP_LIST, ion_list([
                ion_symbol(syms.sid(img["media_name"]))])),
        ]))
    return ion_struct([
        (SYM_ENTITY_IDS, ion_list([ion_struct([
            (SYM_ELEMENT_TYPE_155, ion_string(container_id)),
            (SYM_POSITION_MAP, ion_list([
                ion_symbol(syms.sid(n)) for n in entity_names])),
        ])])),
        (SYM_ENTITY_DEPS, ion_list(deps)),
    ])


def _build_doc_symbol_table(syms: _LocalSymbols) -> bytes:
    import_entry = ion_struct([
        (SYM_IMPORT_NAME, ion_string("YJ_symbols")),
        (SYM_IMPORT_VERSION, ion_int(10)),
        (SYM_SYS_MAX_ID, ion_int(YJ_SYMBOLS_MAX_ID)),
    ])
    return ion_annotation([SYM_ION_SYMBOL_TABLE], ion_struct([
        (SYM_SYS_MAX_ID, ion_int(YJ_SYMBOLS_MAX_ID + len(syms.names))),
        (SYM_IMPORTS, ion_list([import_entry])),
        (SYM_SYMBOLS, ion_list([ion_string(n) for n in syms.names])),
    ]))


# ===========================================================================
# Container serialization
# ===========================================================================

def _enty(payload: bytes) -> bytes:
    """Wrap an entity payload with the ENTY header."""
    info = _ION_BVM + ion_struct([
        (SYM_COMPRESSION_TYPE, ion_int(0)),
        (SYM_DRM_SCHEME, ion_int(0)),
    ])
    return b"ENTY" + struct.pack("<HL", 1, 10 + len(info)) + info + payload


def _kfxgen_info_json(app_version: str, payload_sha1: str,
                      container_id: str) -> bytes:
    # Quirky JSON with unquoted "key"/"value" field names (matches the
    # format Amazon's tools and the KFX Output plugin write and expect).
    return ("[{key:\"kfxgen_package_version\",value:\"\"},"
            "{key:\"kfxgen_application_version\",value:\"%s\"},"
            "{key:\"kfxgen_payload_sha1\",value:\"%s\"},"
            "{key:\"kfxgen_acr\",value:\"%s\"}]"
            % (app_version, payload_sha1, container_id)).encode("ascii")


def _serialize_container(entities: list[tuple[int, int, bytes]],
                         doc_symbols: bytes, format_capabilities: bytes,
                         container_id: str) -> bytes:
    """Assemble the CONT container.

    entities: (id_sid, type_sid, payload) where payload already includes
    the BVM for Ion fragments (raw media stays raw).
    """
    entity_table = bytearray()
    entity_data = bytearray()
    for id_sid, type_sid, payload in entities:
        wrapped = _enty(payload)
        entity_table += struct.pack("<LLQQ", id_sid, type_sid,
                                    len(entity_data), len(wrapped))
        entity_data += wrapped

    header = bytearray(18)                      # fixed header, patched below
    index_tab_offset = len(header)
    header += entity_table
    doc_sym_offset = len(header)
    header += doc_symbols
    fc_offset = len(header)
    header += format_capabilities

    container_info = ion_struct([
        (SYM_CONTAINER_ID, ion_string(container_id)),
        (SYM_COMPRESSION_TYPE, ion_int(0)),
        (SYM_DRM_SCHEME, ion_int(0)),
        (SYM_INDEX_TAB_OFFSET, ion_int(index_tab_offset)),
        (SYM_INDEX_TAB_LENGTH, ion_int(len(entity_table))),
        (SYM_DOC_SYMBOL_OFFSET, ion_int(doc_sym_offset)),
        (SYM_DOC_SYMBOL_LENGTH, ion_int(len(doc_symbols))),
        (SYM_CHUNK_SIZE, ion_int(CHUNK_SIZE)),
        (SYM_FC_OFFSET, ion_int(fc_offset)),
        (SYM_FC_LENGTH, ion_int(len(format_capabilities))),
    ])
    container_info = _ION_BVM + container_info

    info_offset = len(header)
    header += container_info
    payload_sha1 = hashlib.sha1(bytes(entity_data)).hexdigest()
    header += _kfxgen_info_json(APP_VERSION, payload_sha1, container_id)

    struct.pack_into("<4sHL", header, 0, b"CONT", CONTAINER_VERSION, len(header))
    struct.pack_into("<LL", header, 10, info_offset, len(container_info))

    return bytes(header) + bytes(entity_data)


# ===========================================================================
# Main generator
# ===========================================================================

def generate_kfx(image_paths: list[str], output_path: str, title: str = "",
                 author: str = "", reading_direction: str = "rtl",
                 language: str = "en-US", virtual_panels: str = "off",
                 facing_pages: bool = False, facing_start: str = "single",
                 gamma: float = DEFAULT_GAMMA,
                 book_id: str | None = None, asin: str | None = None,
                 container_id: str | None = None) -> None:
    """Generate a single-container KFX file from a list of images.

    Mirrors generate_kpf's interface. title/author are accepted for
    interface parity; like the plugin conversion path, they are not
    embedded (Calibre or the device library supplies display metadata).
    The book_id/asin/container_id overrides exist for reproducible tests.
    """
    if not image_paths:
        raise ValueError("At least one image is required")

    if gamma and abs(gamma - 1.0) > 1e-3:
        gamma_dir = tempfile.mkdtemp(prefix="kfx-gamma-")
        try:
            corrected = gamma_correct_batch(image_paths, gamma, gamma_dir)
            _generate_kfx_impl(corrected, output_path, reading_direction,
                               language, virtual_panels, facing_pages,
                               facing_start, book_id, asin, container_id)
        finally:
            shutil.rmtree(gamma_dir, ignore_errors=True)
    else:
        _generate_kfx_impl(image_paths, output_path, reading_direction,
                           language, virtual_panels, facing_pages,
                           facing_start, book_id, asin, container_id)


def _generate_kfx_impl(image_paths: list[str], output_path: str,
                       reading_direction: str, language: str,
                       virtual_panels: str, facing_pages: bool,
                       facing_start: str, book_id: str | None,
                       asin: str | None, container_id: str | None) -> None:
    import uuid

    page_info = read_page_info(image_paths)
    sections = _build_model(page_info, facing_pages, facing_start)
    num_images = sum(len(s["images"]) for s in sections)

    book_id = book_id or ("P_" + uuid.uuid4().hex[:21])
    asin = asin or _random_id(32)
    container_id = container_id or ("CR!" + _random_id(28))

    # Register local symbols in natural-sort order — the exact SID
    # layout of plugin-produced containers (see _natural_key).
    all_names = ([s["section_name"] for s in sections] +
                 [s["storyline_name"] for s in sections] +
                 [img["resource_name"] for s in sections for img in s["images"]] +
                 [img["media_name"] for s in sections for img in s["images"]])
    syms = _LocalSymbols()
    for name in sorted(all_names, key=_natural_key):
        syms.sid(name)

    # Same id-count formula as the KPF generator's IdAllocator total:
    # d0 + (c,t,l) per section + (i,i,e,rsrc,d) per image.
    max_eid = 1 + 3 * len(sections) + 5 * num_images

    cover_resource_name = sections[0]["images"][0]["resource_name"]

    # --- Build entities in the same order the plugin emits them ---
    entities: list[tuple[int, int, bytes]] = []

    def add(id_sid: int, type_sid: int, value: bytes, raw: bool = False):
        entities.append((id_sid, type_sid, value if raw else _ION_BVM + value))

    add(SYM_SINGLETON, SYM_CONTENT_FEATURES_FRAG,
        _build_content_features(virtual_panels))
    add(SYM_SINGLETON, SYM_BOOK_METADATA_FRAG,
        _build_book_metadata(language, virtual_panels, cover_resource_name,
                             book_id, asin, container_id))
    add(SYM_SINGLETON, SYM_METADATA_FRAG, _build_metadata(syms, sections))
    add(SYM_SINGLETON, SYM_DOCUMENT_DATA_FRAG,
        _build_document_data(syms, sections, max_eid, reading_direction,
                             virtual_panels))
    add(SYM_SINGLETON, SYM_BOOK_NAVIGATION, _build_book_navigation())

    # Within each per-name entity block the plugin emits fragments in
    # plain ASCII order of their ids (the order it reads them back from
    # the KPF's SQLite), not reading order — mirror that.
    for sec in sections:
        disp_w, disp_h, sec_w, sec_h = section_display_box(
            page_info, sec["page_indices"], sec["is_facing"])
        sec["disp_box"] = (disp_w, disp_h, sec_w, sec_h)

    for sec in sorted(sections, key=lambda s: s["section_name"]):
        _, _, sec_w, sec_h = sec["disp_box"]
        add(syms.sid(sec["section_name"]), SYM_SECTION_FRAG,
            _build_section(syms, sec, sec_w, sec_h, virtual_panels))

    for sec in sorted(sections, key=lambda s: s["storyline_name"]):
        disp_w, disp_h, _, _ = sec["disp_box"]
        add(syms.sid(sec["storyline_name"]), SYM_STORYLINE_FRAG,
            _build_storyline(syms, sec, disp_w, disp_h))

    add(SYM_SINGLETON, SYM_POSITION_ID_MAP, _build_position_id_map(syms, sections))
    add(SYM_SINGLETON, SYM_POSITION_EID_MAP, _build_position_eid_map(sections))
    add(SYM_SINGLETON, SYM_LOCATION_MAP, _build_location_map(sections))
    add(SYM_SINGLETON, SYM_NAV_395,
        ion_struct([(SYM_NAV_UNITS, ion_list([]))]))

    all_images = [(img, page_info[sec["page_indices"][i]])
                  for sec in sections
                  for i, img in enumerate(sec["images"])]

    for img, pi in sorted(all_images, key=lambda x: x[0]["resource_name"]):
        add(syms.sid(img["resource_name"]), SYM_EXTERNAL_RESOURCE_FRAG,
            _build_external_resource(syms, img, pi["format"],
                                     pi["width"], pi["height"]))

    for img, pi in sorted(all_images, key=lambda x: x[0]["media_name"]):
        with open(pi["path"], "rb") as f:
            add(syms.sid(img["media_name"]), SYM_RAW_MEDIA, f.read(),
                raw=True)

    add(SYM_SINGLETON, SYM_CONTAINER_ENTITY_MAP,
        _build_container_entity_map(syms, sections, container_id))

    doc_symbols = _ION_BVM + _build_doc_symbol_table(syms)
    format_capabilities = _ION_BVM + ion_annotation(
        [SYM_FORMAT_CAPABILITIES], ion_list([]))

    data = _serialize_container(entities, doc_symbols, format_capabilities,
                                container_id)
    with open(output_path, "wb") as f:
        f.write(data)


# ===========================================================================
# CLI
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a KFX file directly from images (no Kindle "
                    "Previewer, no Calibre plugins).")
    parser.add_argument("images", nargs="+", help="Page images in reading order")
    parser.add_argument("-o", "--output", required=True, help="Output .kfx path")
    parser.add_argument("--direction", choices=["rtl", "ltr"], default="rtl")
    parser.add_argument("--language", default="en-US")
    parser.add_argument("--virtual-panels",
                        choices=["off", "horizontal", "vertical"], default="off")
    parser.add_argument("--facing-pages", action="store_true")
    parser.add_argument("--facing-start", choices=["single", "double"],
                        default="single")
    parser.add_argument("--gamma", type=float, default=DEFAULT_GAMMA,
                        help="Display-gamma compensation (1.0 disables)")
    args = parser.parse_args()

    generate_kfx(args.images, args.output,
                 reading_direction=args.direction, language=args.language,
                 virtual_panels=args.virtual_panels,
                 facing_pages=args.facing_pages,
                 facing_start=args.facing_start, gamma=args.gamma)
    print("KFX written to %s (%d bytes)"
          % (args.output, os.path.getsize(args.output)))


if __name__ == "__main__":
    main()
