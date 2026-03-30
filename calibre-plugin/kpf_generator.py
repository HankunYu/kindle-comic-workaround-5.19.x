"""
KPF (Kindle Publishing Format) Generator

Generates valid KPF files from a list of images for fixed-layout comic books.
Based on reverse-engineering of the Kindle Create KPF format.

Dependencies: Pillow (PIL) for reading image dimensions; standard library only otherwise.
"""

import hashlib
import io
import json
import os
import sqlite3
import struct
import time
import uuid
import zipfile

from PIL import Image


# ===========================================================================
# Base-32 ID generator (Kindle's custom alphabet, excluding I/L/O/Q)
# ===========================================================================

_BASE32_CHARS = "0123456789ABCDEFGHJKMNPRSTUVWXYZ"
_BASE = len(_BASE32_CHARS)  # 32


def _int_to_base32(n: int) -> str:
    """Convert a non-negative integer to the custom base-32 string."""
    if n == 0:
        return _BASE32_CHARS[0]
    digits = []
    while n > 0:
        digits.append(_BASE32_CHARS[n % _BASE])
        n //= _BASE
    return "".join(reversed(digits))


class IdAllocator:
    """Sequential ID allocator using the Kindle base-32 scheme."""

    def __init__(self):
        self._counters: dict[str, int] = {}

    def next_id(self, prefix: str) -> str:
        """Return the next ID for the given prefix (e.g. 'c', 'l', 'i', 'e', 'd', 'rsrc')."""
        idx = self._counters.get(prefix, 0)
        self._counters[prefix] = idx + 1
        return prefix + _int_to_base32(idx)


# ===========================================================================
# Minimal Amazon Ion binary encoder
# ===========================================================================

_ION_BVM = b"\xe0\x01\x00\xea"


def _varuint_encode(value: int) -> bytes:
    """Encode an unsigned integer as Ion VarUInt (MSB=1 means last byte)."""
    if value < 0:
        raise ValueError("VarUInt cannot encode negative values")
    # Split into 7-bit groups, most significant first
    groups = []
    if value == 0:
        groups.append(0)
    else:
        v = value
        while v > 0:
            groups.append(v & 0x7F)
            v >>= 7
        groups.reverse()
    # Set MSB=1 on last byte
    result = bytearray()
    for i, g in enumerate(groups):
        if i == len(groups) - 1:
            result.append(g | 0x80)
        else:
            result.append(g)
    return bytes(result)


def _ion_type_descriptor(type_id: int, length: int) -> bytes:
    """Encode an Ion type descriptor byte + optional length VarUInt."""
    if length < 14:
        return bytes([(type_id << 4) | length])
    else:
        return bytes([(type_id << 4) | 14]) + _varuint_encode(length)


def ion_null() -> bytes:
    """Encode Ion null."""
    return b"\x0f"


def ion_bool(val: bool) -> bytes:
    """Encode Ion bool."""
    return bytes([0x11 if val else 0x10])


def ion_int(val: int) -> bytes:
    """Encode Ion positive/negative integer."""
    if val == 0:
        return b"\x20"
    negative = val < 0
    abs_val = abs(val)
    # Encode as big-endian bytes
    data = []
    v = abs_val
    while v > 0:
        data.append(v & 0xFF)
        v >>= 8
    data.reverse()
    type_id = 3 if negative else 2
    return _ion_type_descriptor(type_id, len(data)) + bytes(data)


def ion_float64(val: float) -> bytes:
    """Encode Ion 64-bit float (IEEE 754 double)."""
    return b"\x48" + struct.pack(">d", val)


def ion_symbol(sid: int) -> bytes:
    """Encode an Ion symbol value (type 7) as big-endian uint."""
    if sid == 0:
        return b"\x70"
    data = []
    v = sid
    while v > 0:
        data.append(v & 0xFF)
        v >>= 8
    data.reverse()
    return _ion_type_descriptor(7, len(data)) + bytes(data)


def ion_string(s: str) -> bytes:
    """Encode Ion UTF-8 string."""
    encoded = s.encode("utf-8")
    return _ion_type_descriptor(8, len(encoded)) + encoded


def ion_list(items: list[bytes]) -> bytes:
    """Encode Ion list from pre-encoded items."""
    body = b"".join(items)
    return _ion_type_descriptor(11, len(body)) + body


def ion_sexp(items: list[bytes]) -> bytes:
    """Encode Ion s-expression from pre-encoded items."""
    body = b"".join(items)
    return _ion_type_descriptor(12, len(body)) + body


def ion_struct(fields: list[tuple[int, bytes]]) -> bytes:
    """Encode Ion struct from (field_sid, pre-encoded value) pairs."""
    body = b"".join(_varuint_encode(sid) + val for sid, val in fields)
    return _ion_type_descriptor(13, len(body)) + body


def ion_annotation(annotation_sids: list[int], value: bytes) -> bytes:
    """Wrap a value with Ion annotation(s)."""
    annot_bytes = b"".join(_varuint_encode(sid) for sid in annotation_sids)
    annot_len = _varuint_encode(len(annot_bytes))
    inner = annot_len + annot_bytes + value
    return _ion_type_descriptor(14, len(inner)) + inner


def ion_eid_ref(eid_string: str) -> bytes:
    """Encode $598::\"eid_string\" — an annotated string used as EID reference."""
    return ion_annotation([598], ion_string(eid_string))


# ===========================================================================
# Symbol table constants
# ===========================================================================

# Key symbol IDs from the YJ_symbols shared symbol table (version 10, max_id=842)
# System symbols: $1-$9
# YJ_symbols: $10-$851 (842 symbols)
# Local symbols start at $852

SYM_FORMAT_VERSION = 16       # $16
SYM_WIDTH = 56                # $56
SYM_HEIGHT = 57               # $57
SYM_PAGE_WIDTH = 66           # $66
SYM_PAGE_HEIGHT = 67          # $67
SYM_PAGE_TEMPLATE_TYPE = 140  # $140
SYM_SECTION_CONTENT = 141     # $141
SYM_COUNT = 144               # $144
SYM_CHILDREN = 146            # $146
SYM_LAYOUT_TYPE = 156         # $156
SYM_NODE_TYPE = 159           # $159
SYM_FORMAT = 161              # $161
SYM_EXTERNAL_RESOURCE = 164   # $164 (annotation)
SYM_LOCATION = 165            # $165
SYM_READING_ORDERS = 169      # $169
SYM_SECTIONS = 170            # $170
SYM_SECTION_ID = 174          # $174
SYM_RESOURCE_ID = 175         # $175
SYM_STORYLINE_ID = 176        # $176
SYM_READING_ORDER_NAME = 178  # $178
SYM_POSITION_MAP = 181        # $181
SYM_FIT_TYPE = 183            # $183
SYM_EID = 185                 # $185
SYM_LAYOUT = 192              # $192
SYM_METADATA = 258            # $258 (annotation + metadata_list field)
SYM_STORYLINE = 259           # $259 (annotation)
SYM_SECTION = 260             # $260 (annotation)
SYM_CONTAINER = 270           # $270 (enum value)
SYM_LEAF = 271                # $271 (enum value)
SYM_PNG = 284                 # $284 (enum value)
SYM_JPG = 285                 # $285 (enum value)
SYM_VALUE = 307               # $307
SYM_FIXED = 320               # $320 (enum value)
SYM_BLOCK = 323               # $323 (enum value)
SYM_FIT_BOTH = 324            # $324 (enum value)
SYM_FIXED_LAYOUT = 326        # $326 (enum value)
SYM_DEFAULT = 351             # $351 (enum value)
SYM_FIXED_LAYOUT_MODE_RTL = 375  # $375 (enum value, RTL layout)
SYM_FIXED_LAYOUT_MODE_LTR = 376  # $376 (enum value, LTR layout)
SYM_ABSOLUTE = 377            # $377 (enum value)
SYM_IMAGE_WIDTH = 422         # $422
SYM_IMAGE_HEIGHT = 423        # $423
SYM_VIRTUAL_PANEL_DIR = 434       # $434 (virtual panel direction field)
SYM_RIGHT_TO_LEFT_BINDING = 441   # $441 (enum value for binding)
SYM_LEFT_TO_RIGHT_BINDING = 442   # $442 (enum value for binding)
SYM_BOOK_METADATA = 490       # $490 (annotation)
SYM_CATEGORIES = 491          # $491
SYM_KEY = 492                 # $492
SYM_CATEGORY_NAME = 495       # $495
SYM_DOCUMENT_DATA = 538       # $538 (annotation)
SYM_POSITION_TYPE = 546       # $546
SYM_RIGHT_TO_LEFT_PAGE = 557  # $557 (enum for page direction)
SYM_LEFT_TO_RIGHT_PAGE = 558  # $558 (enum for page direction)
SYM_PAGE_PROGRESSION_DIR = 560  # $560
SYM_BINDING = 581             # $581
SYM_CONTENT_FEATURES = 585    # $585 (annotation)
SYM_NAMESPACE = 586           # $586
SYM_MAJOR_VERSION = 587       # $587
SYM_MINOR_VERSION = 588       # $588
SYM_PROPERTIES = 589          # $589
SYM_FEATURES_LIST = 590       # $590
SYM_AUXILIARY_DATA = 597      # $597 (annotation + aux_data_ref field)
SYM_SELF_REF = 598            # $598 (annotation for EID ref)
SYM_BUCKET_INDEX = 602        # $602
SYM_STRUCTURE = 608           # $608 (annotation)
SYM_SECTION_POSITION_ID_MAP = 609   # $609 (annotation)
SYM_EIDHASH_EID_SECTION_MAP = 610   # $610 (annotation)
SYM_SECTION_PID_COUNT_MAP = 611     # $611 (annotation)
SYM_RESOURCE_LIST_REF = 613   # $613
# max_id field from system symbol table
SYM_SYS_MAX_ID = 8            # $8

# Local symbols (starting at 852 = 9 system + 842 YJ_symbols + 1)
SYM_LOCAL_SOURCE_FILE_NAME = 852          # yj.authoring.source_file_name
SYM_LOCAL_ORIGINAL_RESOURCE = 853         # yj.authoring.original_resource
SYM_LOCAL_PRESERVED_ORIGINAL = 854        # yj.authoring.preserved_original_resource

LOCAL_SYMBOLS = [
    "yj.authoring.source_file_name",
    "yj.authoring.original_resource",
    "yj.authoring.preserved_original_resource",
]

MAX_SYMBOL_ID = 854

TOOL_VERSION = "1.110.0.0"


# ===========================================================================
# Fragment builders — each returns (fragment_id, payload_type, payload_bytes)
# plus associated fragment_properties entries
# ===========================================================================

def _wrap_blob(data: bytes) -> bytes:
    """Prepend Ion BVM header to encoded data."""
    return _ION_BVM + data


def _build_ion_symbol_table() -> bytes:
    """Build the $ion_symbol_table fragment payload."""
    # $ion_symbol_table::{
    #     max_id: 854,
    #     imports: [{name: "YJ_symbols", version: 10, max_id: 842}],
    #     symbols: ["yj.authoring.source_file_name", ...]
    # }
    import_entry = ion_struct([
        (4, ion_string("YJ_symbols")),   # $4 = name
        (5, ion_int(10)),                 # $5 = version
        (8, ion_int(842)),                # $8 = max_id
    ])
    fields = [
        (8, ion_int(MAX_SYMBOL_ID)),                    # max_id
        (6, ion_list([import_entry])),                   # imports
        (7, ion_list([ion_string(s) for s in LOCAL_SYMBOLS])),  # symbols
    ]
    return _wrap_blob(ion_annotation([3], ion_struct(fields)))  # $3 = $ion_symbol_table


def _build_max_id_fragment() -> bytes:
    """Build the max_id fragment payload (simple integer)."""
    return _wrap_blob(ion_int(MAX_SYMBOL_ID))


def _build_book_navigation() -> bytes:
    """Build the book_navigation fragment (empty — just BVM)."""
    return _ION_BVM


def _build_section(section_id: str, struct_eid: str, storyline_id: str,
                   width: int, height: int, virtual_panels: str = "off") -> bytes:
    """Build a section fragment payload.

    $260::{
        $174: section_id,
        $141: [$608::{inline structure definition}],
        $434: $441  (only when virtual_panels != "off")
    }
    """
    inline_struct = ion_annotation([SYM_STRUCTURE], ion_struct([
        (SYM_SELF_REF, ion_eid_ref(struct_eid)),
        (SYM_STORYLINE_ID, ion_eid_ref(storyline_id)),
        (SYM_PAGE_WIDTH, ion_int(width)),
        (SYM_PAGE_HEIGHT, ion_int(height)),
        (SYM_LAYOUT_TYPE, ion_symbol(SYM_FIXED_LAYOUT)),
        (SYM_PAGE_TEMPLATE_TYPE, ion_symbol(SYM_FIXED)),
        (SYM_NODE_TYPE, ion_symbol(SYM_CONTAINER)),
    ]))
    fields = [
        (SYM_SECTION_ID, ion_eid_ref(section_id)),
        (SYM_SECTION_CONTENT, ion_list([inline_struct])),
    ]
    if virtual_panels != "off":
        fields.append((SYM_VIRTUAL_PANEL_DIR, ion_symbol(SYM_RIGHT_TO_LEFT_BINDING)))
    section = ion_annotation([SYM_SECTION], ion_struct(fields))
    return _wrap_blob(section)


def _build_section_position_id_map(section_id: str, struct_eid: str,
                                   container_eid: str, leaf_eid: str) -> bytes:
    """Build a section_position_id_map fragment.

    $609::{
        $174: section_id,
        $181: [
            [int(1), eid_ref(struct_eid)],
            [int(2), eid_ref(container_eid)],
            [int(3), eid_ref(leaf_eid)]
        ]
    }
    """
    # Each position entry is a nested list [index, eid_ref], NOT an s-expression
    spm = ion_annotation([SYM_SECTION_POSITION_ID_MAP], ion_struct([
        (SYM_SECTION_ID, ion_eid_ref(section_id)),
        (SYM_POSITION_MAP, ion_list([
            ion_list([ion_int(1), ion_eid_ref(struct_eid)]),
            ion_list([ion_int(2), ion_eid_ref(container_eid)]),
            ion_list([ion_int(3), ion_eid_ref(leaf_eid)]),
        ])),
    ]))
    return _wrap_blob(spm)


def _build_storyline(storyline_id: str, container_eid: str) -> bytes:
    """Build a storyline fragment.

    $259::{
        $176: storyline_id,
        $146: [container_eid]
    }
    """
    storyline = ion_annotation([SYM_STORYLINE], ion_struct([
        (SYM_STORYLINE_ID, ion_eid_ref(storyline_id)),
        (SYM_CHILDREN, ion_list([ion_eid_ref(container_eid)])),
    ]))
    return _wrap_blob(storyline)


def _build_structure_container(eid: str, width: int, height: int,
                               child_eid: str) -> bytes:
    """Build a container structure node.

    $608::{
        $598: eid, $56: width, $57: height,
        $546: $377, $156: $323, $159: $270,
        $146: [child_eid]
    }
    """
    node = ion_annotation([SYM_STRUCTURE], ion_struct([
        (SYM_SELF_REF, ion_eid_ref(eid)),
        (SYM_WIDTH, ion_int(width)),
        (SYM_HEIGHT, ion_int(height)),
        (SYM_POSITION_TYPE, ion_symbol(SYM_ABSOLUTE)),
        (SYM_LAYOUT_TYPE, ion_symbol(SYM_BLOCK)),
        (SYM_NODE_TYPE, ion_symbol(SYM_CONTAINER)),
        (SYM_CHILDREN, ion_list([ion_eid_ref(child_eid)])),
    ]))
    return _wrap_blob(node)


def _build_structure_leaf(eid: str, width: int, height: int,
                          resource_eid: str) -> bytes:
    """Build a leaf structure node (image reference).

    $608::{
        $598: eid, $56: width, $57: height,
        $175: resource_eid, $546: $377, $159: $271, $183: $324
    }
    """
    node = ion_annotation([SYM_STRUCTURE], ion_struct([
        (SYM_SELF_REF, ion_eid_ref(eid)),
        (SYM_WIDTH, ion_int(width)),
        (SYM_HEIGHT, ion_int(height)),
        (SYM_RESOURCE_ID, ion_eid_ref(resource_eid)),
        (SYM_POSITION_TYPE, ion_symbol(SYM_ABSOLUTE)),
        (SYM_NODE_TYPE, ion_symbol(SYM_LEAF)),
        (SYM_FIT_TYPE, ion_symbol(SYM_FIT_BOTH)),
    ]))
    return _wrap_blob(node)


def _build_external_resource(resource_eid: str, source_filename: str,
                             fmt_sym: int, rsrc_location: str,
                             aux_data_id: str,
                             img_width: int, img_height: int) -> bytes:
    """Build an external_resource fragment.

    $164::{
        $852: source_filename,
        $161: fmt_sym,
        $165: rsrc_location,
        $597: aux_data_id,
        $422: float(img_width), $423: float(img_height),
        $175: resource_eid
    }
    """
    resource = ion_annotation([SYM_EXTERNAL_RESOURCE], ion_struct([
        (SYM_LOCAL_SOURCE_FILE_NAME, ion_string(source_filename)),
        (SYM_FORMAT, ion_symbol(fmt_sym)),
        (SYM_LOCATION, ion_string(rsrc_location)),
        (SYM_AUXILIARY_DATA, ion_eid_ref(aux_data_id)),
        (SYM_IMAGE_WIDTH, ion_float64(float(img_width))),
        (SYM_RESOURCE_ID, ion_eid_ref(resource_eid)),
        (SYM_IMAGE_HEIGHT, ion_float64(float(img_height))),
    ]))
    return _wrap_blob(resource)


def _build_auxiliary_data(aux_id: str, rsrc_location: str, file_size: int,
                          modified_time: int, original_path: str) -> bytes:
    """Build an auxiliary_data fragment for a resource.

    $597::{
        $598: aux_id,
        $258: [
            {$492: "type", $307: "resource"},
            {$492: "resource_stream", $307: rsrc_location},
            {$492: "size", $307: str(file_size)},
            {$492: "modified_time", $307: str(modified_time)},
            {$492: "location", $307: original_path}
        ]
    }
    """
    metadata_items = [
        ion_struct([(SYM_KEY, ion_string("type")),
                    (SYM_VALUE, ion_string("resource"))]),
        ion_struct([(SYM_KEY, ion_string("resource_stream")),
                    (SYM_VALUE, ion_string(rsrc_location))]),
        ion_struct([(SYM_KEY, ion_string("size")),
                    (SYM_VALUE, ion_string(str(file_size)))]),
        ion_struct([(SYM_KEY, ion_string("modified_time")),
                    (SYM_VALUE, ion_string(str(modified_time)))]),
        ion_struct([(SYM_KEY, ion_string("location")),
                    (SYM_VALUE, ion_string(original_path))]),
    ]
    aux = ion_annotation([SYM_AUXILIARY_DATA], ion_struct([
        (SYM_SELF_REF, ion_eid_ref(aux_id)),
        (SYM_METADATA, ion_list(metadata_items)),
    ]))
    return _wrap_blob(aux)


def _build_resource_list_auxiliary_data(aux_id: str,
                                       resource_aux_ids: list[str]) -> bytes:
    """Build the resource list auxiliary_data (the 'd5' equivalent).

    $597::{
        $598: $598::aux_id,
        $258: [
            {$492: "auxData_resource_list", $307: [eid_refs...]}
        ]
    }
    """
    refs_list = ion_list([ion_eid_ref(rid) for rid in resource_aux_ids])
    aux = ion_annotation([SYM_AUXILIARY_DATA], ion_struct([
        (SYM_SELF_REF, ion_eid_ref(aux_id)),
        (SYM_METADATA, ion_list([
            ion_struct([
                (SYM_KEY, ion_string("auxData_resource_list")),
                (SYM_VALUE, refs_list),
            ]),
        ])),
    ]))
    return _wrap_blob(aux)


def _build_document_data(section_ids: list[str], resource_list_aux_id: str,
                         max_eid: int, reading_direction: str,
                         virtual_panels: str = "off") -> bytes:
    """Build the document_data fragment.

    $538::{
        $16: 16.0,
        $560: $557 or $556,
        max_id: max_eid,
        $192: $375,
        $581: $441 or $440,
        $597: {$613: $598::resource_list_aux_id},
        $169: [{$178: $351, $170: [section_refs...]}]
    }
    """
    # Kindle Create always uses RTL symbols for page_dir and binding,
    # and controls LTR/RTL via the layout mode symbol ($375 vs $376).
    # Exception: vertical virtual panels use $558 for page_dir.
    if virtual_panels == "vertical":
        page_dir_sym = SYM_LEFT_TO_RIGHT_PAGE
    else:
        page_dir_sym = SYM_RIGHT_TO_LEFT_PAGE
    binding_sym = SYM_RIGHT_TO_LEFT_BINDING
    if reading_direction == "rtl":
        layout_mode_sym = SYM_FIXED_LAYOUT_MODE_RTL
    else:
        layout_mode_sym = SYM_FIXED_LAYOUT_MODE_LTR

    section_refs = [ion_eid_ref(sid) for sid in section_ids]

    reading_order = ion_struct([
        (SYM_READING_ORDER_NAME, ion_symbol(SYM_DEFAULT)),
        (SYM_SECTIONS, ion_list(section_refs)),
    ])

    aux_ref_struct = ion_struct([
        (SYM_RESOURCE_LIST_REF, ion_eid_ref(resource_list_aux_id)),
    ])

    doc = ion_annotation([SYM_DOCUMENT_DATA], ion_struct([
        (SYM_FORMAT_VERSION, ion_float64(16.0)),
        (SYM_PAGE_PROGRESSION_DIR, ion_symbol(page_dir_sym)),
        (SYM_SYS_MAX_ID, ion_int(max_eid)),
        (SYM_LAYOUT, ion_symbol(layout_mode_sym)),
        (SYM_BINDING, ion_symbol(binding_sym)),
        (SYM_AUXILIARY_DATA, aux_ref_struct),
        (SYM_READING_ORDERS, ion_list([reading_order])),
    ]))
    return _wrap_blob(doc)


def _build_metadata(section_ids: list[str]) -> bytes:
    """Build the metadata fragment.

    $258::{
        $169: [{$178: $351, $170: [section_refs...]}]
    }
    """
    section_refs = [ion_eid_ref(sid) for sid in section_ids]
    reading_order = ion_struct([
        (SYM_READING_ORDER_NAME, ion_symbol(SYM_DEFAULT)),
        (SYM_SECTIONS, ion_list(section_refs)),
    ])
    meta = ion_annotation([SYM_METADATA], ion_struct([
        (SYM_READING_ORDERS, ion_list([reading_order])),
    ]))
    return _wrap_blob(meta)


def _build_book_metadata(language: str, virtual_panels: str = "off") -> bytes:
    """Build the book_metadata fragment.

    $490::{$491: [4 categories...]}
    """
    book_id = "P_" + uuid.uuid4().hex[:21]

    panels_value = 0 if virtual_panels != "off" else 1
    cap_category = ion_struct([
        (SYM_CATEGORY_NAME, ion_string("kindle_capability_metadata")),
        (SYM_METADATA, ion_list([
            ion_struct([(SYM_KEY, ion_string("yj_publisher_panels")),
                        (SYM_VALUE, ion_int(panels_value))]),
            ion_struct([(SYM_KEY, ion_string("yj_fixed_layout")),
                        (SYM_VALUE, ion_int(1))]),
        ])),
    ])

    title_category = ion_struct([
        (SYM_CATEGORY_NAME, ion_string("kindle_title_metadata")),
        (SYM_METADATA, ion_list([
            ion_struct([(SYM_KEY, ion_string("book_id")),
                        (SYM_VALUE, ion_string(book_id))]),
            ion_struct([(SYM_KEY, ion_string("language")),
                        (SYM_VALUE, ion_string(language))]),
        ])),
    ])

    ebook_category = ion_struct([
        (SYM_CATEGORY_NAME, ion_string("kindle_ebook_metadata")),
        (SYM_METADATA, ion_list([
            ion_struct([(SYM_KEY, ion_string("selection")),
                        (SYM_VALUE, ion_string("enabled"))]),
        ])),
    ])

    audit_category = ion_struct([
        (SYM_CATEGORY_NAME, ion_string("kindle_audit_metadata")),
        (SYM_METADATA, ion_list([
            ion_struct([(SYM_KEY, ion_string("file_creator")),
                        (SYM_VALUE, ion_string("KC"))]),
            ion_struct([(SYM_KEY, ion_string("creator_version")),
                        (SYM_VALUE, ion_string(TOOL_VERSION))]),
        ])),
    ])

    bm = ion_annotation([SYM_BOOK_METADATA], ion_struct([
        (SYM_CATEGORIES, ion_list([
            cap_category, title_category, ebook_category, audit_category,
        ])),
    ]))
    return _wrap_blob(bm)


def _build_content_features(virtual_panels: str = "off") -> bytes:
    """Build the content_features fragment.

    $585::{
        $598: $585,
        $590: [feature entries]
    }
    """
    feature_fl = ion_struct([
        (SYM_NAMESPACE, ion_string("com.amazon.yjconversion")),
        (SYM_KEY, ion_string("yj_non_pdf_fixed_layout")),
        (SYM_PROPERTIES, ion_struct([
            (5, ion_struct([  # $5 = version
                (SYM_MAJOR_VERSION, ion_int(2)),
                (SYM_MINOR_VERSION, ion_int(0)),
            ])),
        ])),
    ])

    features = [feature_fl]

    if virtual_panels == "off":
        feature_pp = ion_struct([
            (SYM_NAMESPACE, ion_string("com.amazon.yjconversion")),
            (SYM_KEY, ion_string("yj_publisher_panels")),
            (SYM_PROPERTIES, ion_struct([
                (5, ion_struct([  # $5 = version
                    (SYM_MAJOR_VERSION, ion_int(2)),
                    (SYM_MINOR_VERSION, ion_int(0)),
                ])),
            ])),
        ])
        features.append(feature_pp)

    cf = ion_annotation([SYM_CONTENT_FEATURES], ion_struct([
        (SYM_SELF_REF, ion_symbol(SYM_CONTENT_FEATURES)),
        (SYM_FEATURES_LIST, ion_list(features)),
    ]))
    return _wrap_blob(cf)


def _build_eidhash_bucket(bucket_index: int,
                          entries: list[tuple[str, str]]) -> bytes:
    """Build a yj.eidhash_eid_section_map fragment.

    $610::{
        $602: bucket_index,
        $181: [
            {$185: $598::eid, $174: $598::section_id}, ...
        ]
    }
    """
    entry_structs = [
        ion_struct([
            (SYM_EID, ion_eid_ref(eid)),
            (SYM_SECTION_ID, ion_eid_ref(section_id)),
        ])
        for eid, section_id in entries
    ]
    bucket = ion_annotation([SYM_EIDHASH_EID_SECTION_MAP], ion_struct([
        (SYM_BUCKET_INDEX, ion_int(bucket_index)),
        (SYM_POSITION_MAP, ion_list(entry_structs)),
    ]))
    return _wrap_blob(bucket)


def _build_section_pid_count_map(
        section_counts: list[tuple[str, int]]) -> bytes:
    """Build the yj.section_pid_count_map fragment.

    $611::{
        $181: [
            {$174: $598::section_id, $144: count}, ...
        ]
    }
    """
    entries = [
        ion_struct([
            (SYM_SECTION_ID, ion_eid_ref(sid)),
            (SYM_COUNT, ion_int(count)),
        ])
        for sid, count in section_counts
    ]
    pidmap = ion_annotation([SYM_SECTION_PID_COUNT_MAP], ion_struct([
        (SYM_POSITION_MAP, ion_list(entries)),
    ]))
    return _wrap_blob(pidmap)


# ===========================================================================
# EID hash algorithm
# ===========================================================================

def _eid_hash_bucket(eid: str, num_buckets: int) -> int:
    """Compute the hash bucket index for an EID string."""
    return sum(ord(c) for c in eid) % num_buckets


# ===========================================================================
# Fingerprint wrapper
# ===========================================================================

_FINGERPRINT_OFFSET = 1024
_FINGERPRINT_RECORD_LEN = 1024
_DATA_RECORD_LEN = 1024
_DATA_RECORD_COUNT = 1024
_FINGERPRINT_SIGNATURE = b"\xfa\x50\x0a\x5f"


def _add_fingerprints(data: bytes) -> bytes:
    """Insert fingerprint records into SQLite data.

    Reverse of the removal algorithm: insert a 1024-byte fingerprint record
    at offset 1024, then every 1MB of data thereafter.
    """
    result = bytearray(data)
    offset = _FINGERPRINT_OFFSET
    while offset <= len(result):
        # Build fingerprint record: signature + padding
        fp_record = bytearray(_FINGERPRINT_RECORD_LEN)
        fp_record[0:4] = _FINGERPRINT_SIGNATURE
        # Fill the rest with a simple pattern (version byte + zeros is fine)
        fp_record[4] = 0x01
        fp_record[5] = 0x00
        fp_record[6] = 0x00
        fp_record[7] = 0x40
        result[offset:offset] = fp_record
        offset += _FINGERPRINT_RECORD_LEN + _DATA_RECORD_LEN * _DATA_RECORD_COUNT
    return bytes(result)


# ===========================================================================
# Image format detection
# ===========================================================================

def _detect_image_format(path: str) -> str:
    """Detect image format by reading the file header."""
    with open(path, "rb") as f:
        header = f.read(8)
    if header[:3] == b"\xff\xd8\xff":
        return "jpg"
    elif header[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    else:
        raise ValueError(f"Unsupported image format: {path}")


# ===========================================================================
# Main generator
# ===========================================================================

def generate_kpf(image_paths: list[str], output_path: str, title: str = "",
                 author: str = "", reading_direction: str = "rtl",
                 language: str = "en-US", virtual_panels: str = "off") -> None:
    """Generate a KPF file from a list of images.

    Args:
        image_paths: List of paths to image files (JPEG or PNG).
        output_path: Path for the output .kpf file.
        title: Book title (used in metadata).
        author: Book author (used in metadata).
        reading_direction: "rtl" for right-to-left, "ltr" for left-to-right.
        language: Language code (e.g. "en-US", "ja").
        virtual_panels: "off", "horizontal", or "vertical".
    """
    if not image_paths:
        raise ValueError("At least one image is required")

    num_pages = len(image_paths)
    modified_time = int(time.time())

    # -----------------------------------------------------------------------
    # Phase 1: Read image metadata
    # -----------------------------------------------------------------------
    page_info: list[dict] = []
    for img_path in image_paths:
        abs_path = os.path.abspath(img_path)
        fmt = _detect_image_format(abs_path)
        with Image.open(abs_path) as im:
            w, h = im.size
        file_size = os.path.getsize(abs_path)
        page_info.append({
            "path": abs_path,
            "format": fmt,
            "width": w,
            "height": h,
            "size": file_size,
            "filename": os.path.basename(abs_path),
        })

    # -----------------------------------------------------------------------
    # Phase 2: Allocate IDs
    # -----------------------------------------------------------------------
    ids = IdAllocator()

    # Allocate the resource list auxiliary_data ID first
    resource_list_aux_id = ids.next_id("d")  # d0

    # Per-page IDs
    per_page: list[dict] = []
    for i in range(num_pages):
        section_id = ids.next_id("c")
        struct_eid = ids.next_id("t")
        storyline_id = ids.next_id("l")
        container_eid = ids.next_id("i")
        leaf_eid = ids.next_id("i")
        resource_eid = ids.next_id("e")
        rsrc_id = ids.next_id("rsrc")
        aux_id = ids.next_id("d")
        per_page.append({
            "section_id": section_id,
            "struct_eid": struct_eid,
            "storyline_id": storyline_id,
            "container_eid": container_eid,
            "leaf_eid": leaf_eid,
            "resource_eid": resource_eid,
            "rsrc_id": rsrc_id,
            "aux_id": aux_id,
        })

    # Calculate max_eid for document_data
    # The max EID value is the total number of assigned IDs
    # Count: per page = 8 IDs, plus resource_list_aux_id = 1
    max_eid = 1 + num_pages * 8

    # All section IDs in reading order
    section_ids = [p["section_id"] for p in per_page]

    # All resource auxiliary_data IDs
    resource_aux_ids = [p["aux_id"] for p in per_page]

    # -----------------------------------------------------------------------
    # Phase 3: Build fragments
    # -----------------------------------------------------------------------
    # fragments: list of (id, payload_type, payload_value)
    fragments: list[tuple[str, str, bytes | str]] = []
    # fragment_properties: list of (id, key, value)
    frag_props: list[tuple[str, str, str]] = []
    # gc_reachable: set of fragment IDs
    gc_reachable: set[str] = set()
    # gc_fragment_properties: list of (id, key, value)
    gc_frag_props: list[tuple[str, str, str]] = []

    # --- Global fragments ---

    # $ion_symbol_table
    fragments.append(("$ion_symbol_table", "blob", _build_ion_symbol_table()))
    frag_props.append(("$ion_symbol_table", "element_type", "$ion_symbol_table"))
    gc_reachable.add("$ion_symbol_table")

    # max_id
    fragments.append(("max_id", "blob", _build_max_id_fragment()))
    frag_props.append(("max_id", "element_type", "max_id"))
    gc_reachable.add("max_id")

    # book_navigation (empty)
    fragments.append(("book_navigation", "blob", _build_book_navigation()))
    frag_props.append(("book_navigation", "element_type", "book_navigation"))
    gc_reachable.add("book_navigation")
    gc_frag_props.append(("book_navigation", "child", "book_navigation"))

    # book_metadata
    fragments.append(("book_metadata", "blob", _build_book_metadata(language, virtual_panels)))
    frag_props.append(("book_metadata", "element_type", "book_metadata"))
    gc_reachable.add("book_metadata")

    # content_features
    fragments.append(("content_features", "blob", _build_content_features(virtual_panels)))
    frag_props.append(("content_features", "element_type", "content_features"))
    gc_reachable.add("content_features")

    # document_data
    fragments.append(("document_data", "blob",
                       _build_document_data(section_ids, resource_list_aux_id,
                                            max_eid, reading_direction,
                                            virtual_panels)))
    frag_props.append(("document_data", "element_type", "document_data"))
    frag_props.append(("document_data", "child", resource_list_aux_id))
    gc_frag_props.append(("document_data", "child", resource_list_aux_id))
    gc_reachable.add("document_data")

    # metadata
    fragments.append(("metadata", "blob", _build_metadata(section_ids)))
    frag_props.append(("metadata", "element_type", "metadata"))
    gc_reachable.add("metadata")

    # resource list auxiliary_data
    fragments.append((resource_list_aux_id, "blob",
                       _build_resource_list_auxiliary_data(
                           resource_list_aux_id, resource_aux_ids)))
    frag_props.append((resource_list_aux_id, "element_type", "auxiliary_data"))
    gc_reachable.add(resource_list_aux_id)

    # --- Per-page fragments ---
    # Collect EID -> section mapping for hash buckets
    eid_section_map: list[tuple[str, str]] = []

    for i in range(num_pages):
        p = per_page[i]
        info = page_info[i]
        w, h = info["width"], info["height"]
        fmt_sym = SYM_JPG if info["format"] == "jpg" else SYM_PNG

        sid = p["section_id"]
        t_eid = p["struct_eid"]
        l_id = p["storyline_id"]
        i_container = p["container_eid"]
        i_leaf = p["leaf_eid"]
        e_id = p["resource_eid"]
        rsrc_id = p["rsrc_id"]
        d_id = p["aux_id"]

        # Section
        fragments.append((sid, "blob",
                           _build_section(sid, t_eid, l_id, w, h, virtual_panels)))
        frag_props.append((sid, "element_type", "section"))
        frag_props.append((sid, "child", f"{sid}-ad"))
        frag_props.append((sid, "child", l_id))
        gc_reachable.add(sid)
        gc_reachable.add(f"{sid}-ad")

        # Section position ID map
        spm_id = f"{sid}-spm"
        fragments.append((spm_id, "blob",
                           _build_section_position_id_map(
                               sid, t_eid, i_container, i_leaf)))
        frag_props.append((spm_id, "element_type", "section_position_id_map"))
        gc_reachable.add(spm_id)

        # Storyline
        fragments.append((l_id, "blob",
                           _build_storyline(l_id, i_container)))
        frag_props.append((l_id, "element_type", "storyline"))
        frag_props.append((l_id, "child", i_container))
        frag_props.append((l_id, "child", l_id))
        gc_frag_props.append((l_id, "child", i_container))
        gc_frag_props.append((l_id, "child", l_id))
        gc_reachable.add(l_id)

        # Container structure
        fragments.append((i_container, "blob",
                           _build_structure_container(
                               i_container, w, h, i_leaf)))
        frag_props.append((i_container, "element_type", "structure"))
        frag_props.append((i_container, "child", i_leaf))
        gc_reachable.add(i_container)

        # Leaf structure
        fragments.append((i_leaf, "blob",
                           _build_structure_leaf(i_leaf, w, h, e_id)))
        frag_props.append((i_leaf, "element_type", "structure"))
        frag_props.append((i_leaf, "child", e_id))
        gc_reachable.add(i_leaf)

        # External resource
        fragments.append((e_id, "blob",
                           _build_external_resource(
                               e_id, info["filename"], fmt_sym, rsrc_id,
                               d_id, w, h)))
        frag_props.append((e_id, "element_type", "external_resource"))
        frag_props.append((e_id, "child", d_id))
        frag_props.append((e_id, "child", rsrc_id))
        gc_reachable.add(e_id)

        # bcRawMedia (path type)
        rsrc_path = f"res/{rsrc_id}"
        fragments.append((rsrc_id, "path", rsrc_path))
        frag_props.append((rsrc_id, "element_type", "bcRawMedia"))
        gc_reachable.add(rsrc_id)

        # Auxiliary data
        fragments.append((d_id, "blob",
                           _build_auxiliary_data(
                               d_id, rsrc_id, info["size"],
                               modified_time, info["path"])))
        frag_props.append((d_id, "element_type", "auxiliary_data"))
        gc_reachable.add(d_id)

        # EID -> section mappings (4 per page)
        eid_section_map.append((sid, sid))
        eid_section_map.append((t_eid, sid))
        eid_section_map.append((i_container, sid))
        eid_section_map.append((i_leaf, sid))

    # --- EID hash buckets ---
    total_eids = len(eid_section_map)
    # Determine number of buckets: approximately total_eids / 10, minimum 1
    num_buckets = max(1, (total_eids + 9) // 10)
    # But use a prime-ish number similar to the reference (67 for 688 EIDs)
    # We'll keep it simple: num_buckets = max(1, round(total_eids / 10.3))
    if total_eids > 10:
        num_buckets = max(1, round(total_eids / 10.3))
    else:
        num_buckets = max(1, total_eids)

    buckets: dict[int, list[tuple[str, str]]] = {}
    for eid, section_id in eid_section_map:
        bucket = _eid_hash_bucket(eid, num_buckets)
        if bucket not in buckets:
            buckets[bucket] = []
        buckets[bucket].append((eid, section_id))

    for bucket_idx in range(num_buckets):
        bucket_id = f"eidbucket_{bucket_idx}"
        entries = buckets.get(bucket_idx, [])
        fragments.append((bucket_id, "blob",
                           _build_eidhash_bucket(bucket_idx, entries)))
        frag_props.append((bucket_id, "element_type",
                           "yj.eidhash_eid_section_map"))
        gc_reachable.add(bucket_id)

    # --- Section PID count map ---
    section_pid_counts = [(sid, 3) for sid in section_ids]
    fragments.append(("yj.section_pid_count_map", "blob",
                       _build_section_pid_count_map(section_pid_counts)))
    frag_props.append(("yj.section_pid_count_map", "element_type",
                       "yj.section_pid_count_map"))
    gc_reachable.add("yj.section_pid_count_map")

    # -----------------------------------------------------------------------
    # Phase 4: Build SQLite database
    # -----------------------------------------------------------------------
    db_path = output_path + ".tmp.kdf"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Schema must match reference exactly (KFX Output plugin does string matching)
        cursor.execute("CREATE TABLE capabilities(key char(20), version smallint, primary key (key, version)) without rowid")
        cursor.execute("CREATE TABLE fragments(id char(40), payload_type char(10), payload_value blob, primary key (id))")
        cursor.execute("CREATE TABLE fragment_properties(id char(40), key char(40), value char(40), primary key (id, key, value)) without rowid")
        cursor.execute("CREATE TABLE gc_fragment_properties(id varchar(40), key varchar(40), value varchar(40), primary key (id, key, value)) without rowid")
        cursor.execute("CREATE TABLE gc_reachable(id varchar(40), primary key (id)) without rowid")

        cursor.execute("INSERT INTO capabilities VALUES ('db.schema', 1)")

        for fid, ptype, pval in fragments:
            if ptype == "blob":
                cursor.execute(
                    "INSERT INTO fragments VALUES (?, 'blob', ?)",
                    (fid, pval))
            else:
                cursor.execute(
                    "INSERT INTO fragments VALUES (?, 'path', ?)",
                    (fid, pval.encode("utf-8") if isinstance(pval, str) else pval))

        for fid, key, value in frag_props:
            cursor.execute(
                "INSERT OR IGNORE INTO fragment_properties VALUES (?, ?, ?)",
                (fid, key, value))

        for fid, key, value in gc_frag_props:
            cursor.execute(
                "INSERT OR IGNORE INTO gc_fragment_properties VALUES (?, ?, ?)",
                (fid, key, value))

        for rid in sorted(gc_reachable):
            cursor.execute(
                "INSERT OR IGNORE INTO gc_reachable VALUES (?)", (rid,))

        conn.commit()
        conn.close()

        # Read the database file
        with open(db_path, "rb") as f:
            db_data = f.read()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
        # Also clean up journal file
        journal = db_path + "-journal"
        if os.path.exists(journal):
            os.unlink(journal)

    # Apply fingerprint wrapper
    kdf_data = _add_fingerprints(db_data)

    # -----------------------------------------------------------------------
    # Phase 5: Build KPF ZIP
    # -----------------------------------------------------------------------
    content_hashes: dict[str, str] = {}

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # --- book.kdf ---
        zf.writestr("resources/book.kdf", kdf_data)
        content_hashes["resources/book.kdf"] = hashlib.md5(kdf_data).hexdigest()

        # --- Empty journal file ---
        zf.writestr("resources/book.kdf-journal", b"")
        content_hashes["resources/book.kdf-journal"] = hashlib.md5(b"").hexdigest()

        # --- ManifestFile ---
        manifest = (
            "AmazonYJManifest\n"
            "digital_content_manifest::{\n"
            '  version:1,\n'
            '  storage_type:"localSqlLiteDB",\n'
            '  digital_content_name:"book.kdf"\n'
            "}\n"
        )
        zf.writestr("resources/ManifestFile", manifest)
        content_hashes["resources/ManifestFile"] = hashlib.md5(
            manifest.encode("utf-8")).hexdigest()

        # --- Resource images ---
        for i in range(num_pages):
            rsrc_id = per_page[i]["rsrc_id"]
            rsrc_path = f"resources/res/{rsrc_id}"
            with open(page_info[i]["path"], "rb") as f:
                img_data = f.read()
            zf.writestr(rsrc_path, img_data)
            content_hashes[rsrc_path] = hashlib.md5(img_data).hexdigest()

        # --- Preview thumbnails (book_N.jpg) ---
        for i in range(num_pages):
            book_img_name = f"book_{i + 1}.jpg"
            img_path = page_info[i]["path"]
            # Generate JPEG thumbnail for preview
            with Image.open(img_path) as im:
                if im.mode != "RGB":
                    im = im.convert("RGB")
                thumb_buf = io.BytesIO()
                im.save(thumb_buf, "JPEG", quality=85)
                thumb_data = thumb_buf.getvalue()
            zf.writestr(book_img_name, thumb_data)
            content_hashes[book_img_name] = hashlib.md5(thumb_data).hexdigest()

        # --- action.log ---
        now_str = time.strftime("%a %b %d %H:%M:%S UTC %Y", time.gmtime())
        action_log = (
            f"[{now_str}][INFO] [Action] EE NewBook\n"
            f"[{now_str}][INFO] [Action] EE ZoomPage\n"
            f"[{now_str}][INFO] [Action] E SaveBook - SaveforExport\n"
        )
        zf.writestr("action.log", action_log)
        content_hashes["action.log"] = hashlib.md5(
            action_log.encode("utf-8")).hexdigest()

        # --- book.kcb ---
        reading_dir_val = 2 if reading_direction == "rtl" else 1
        kcb = {
            "book_state": {
                "book_fl_type": 1,
                "book_input_type": 4,
                "book_reading_direction": reading_dir_val,
                "book_reading_option": 2 if virtual_panels != "off" else 1,
                "book_target_type": 3,
                "book_virtual_panelmovement": {"off": 0, "horizontal": 1, "vertical": 2}[virtual_panels],
            },
            "content_hash": content_hashes,
            "metadata": {
                "book_path": "resources",
                "edited_tool_versions": [TOOL_VERSION],
                "format": "yj",
                "global_styling": True,
                "id": str(uuid.uuid4()),
                "platform": "mac",
                "tool_name": "KC",
                "tool_version": TOOL_VERSION,
            },
        }
        kcb_json = json.dumps(kcb, indent=3, sort_keys=False)
        zf.writestr("book.kcb", kcb_json)


# ===========================================================================
# CLI entry point
# ===========================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate a KPF (Kindle Publishing Format) file from images.")
    parser.add_argument("images", nargs="+", help="Image files (JPEG/PNG)")
    parser.add_argument("-o", "--output", required=True,
                        help="Output KPF file path")
    parser.add_argument("--title", default="", help="Book title")
    parser.add_argument("--author", default="", help="Book author")
    parser.add_argument("--direction", default="rtl",
                        choices=["rtl", "ltr"],
                        help="Reading direction (default: rtl)")
    parser.add_argument("--language", default="en-US",
                        help="Language code (default: en-US)")
    parser.add_argument("--virtual-panels", default="off",
                        choices=["off", "horizontal", "vertical"],
                        help="Virtual panel navigation mode (default: off)")
    args = parser.parse_args()

    generate_kpf(
        image_paths=args.images,
        output_path=args.output,
        title=args.title,
        author=args.author,
        reading_direction=args.direction,
        language=args.language,
        virtual_panels=args.virtual_panels,
    )
    print(f"KPF generated: {args.output}")


if __name__ == "__main__":
    main()
