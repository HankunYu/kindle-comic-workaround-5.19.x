#!/usr/bin/env python3
"""
Direct image extraction from MOBI/AZW/AZW3 containers.

A MOBI file is a PalmDB: a record table followed by data records. Comic
MOBIs store each page image as its own record, in reading order,
starting at the "first image index" declared in the MOBI header. This
module pulls those records out directly — no ebook-convert round trip —
which is ~5x faster than converting to EPUB first.

Callers should treat any exception as "this file is unusual" and fall
back to the ebook-convert path.
"""

import os
import struct

# Record payloads that are not page images
_NON_IMAGE_MAGICS = (
    b"FLIS", b"FCIS", b"FDST", b"DATP", b"SRCS", b"PAGE", b"CMET",
    b"AUDI", b"VIDE", b"RESC", b"BOUNDARY", b"CRES", b"CONT", b"kindle:",
    b"\xe9\x8e\r\n",          # end-of-file record
)

_EXTH_AUTHOR = 100
_EXTH_COVER_OFFSET = 201
_EXTH_THUMB_OFFSET = 202
_EXTH_UPDATED_TITLE = 503


class MobiFormatError(ValueError):
    """Raised when the file does not look like a comic MOBI we understand."""


def _image_ext(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] in (b"GIF8",):
        return ".gif"
    return None


def _parse_records(data: bytes) -> list[tuple[int, int]]:
    """Return [(offset, end), ...] for every PDB record."""
    if data[60:68] != b"BOOKMOBI":
        raise MobiFormatError("not a MOBI (BOOKMOBI signature missing)")
    (num_records,) = struct.unpack_from(">H", data, 76)
    offsets = []
    for i in range(num_records):
        (off,) = struct.unpack_from(">I", data, 78 + i * 8)
        offsets.append(off)
    offsets.append(len(data))
    return [(offsets[i], offsets[i + 1]) for i in range(num_records)]


def _parse_header(data: bytes, records: list[tuple[int, int]]):
    """Return (first_image_index, cover_index_or_None, excluded_indices)."""
    r0_start, r0_end = records[0]
    r0 = data[r0_start:r0_end]
    if r0[16:20] != b"MOBI":
        raise MobiFormatError("record 0 has no MOBI header")
    (header_length,) = struct.unpack_from(">I", r0, 20)
    (first_image_index,) = struct.unpack_from(">I", r0, 16 + 92)
    if first_image_index == 0xFFFFFFFF or first_image_index >= len(records):
        raise MobiFormatError("no image records declared")

    cover_index = None
    excluded = set()
    (exth_flags,) = struct.unpack_from(">I", r0, 16 + 112)
    if exth_flags & 0x40:
        exth_start = 16 + header_length
        if r0[exth_start:exth_start + 4] != b"EXTH":
            raise MobiFormatError("EXTH flagged but missing")
        (count,) = struct.unpack_from(">I", r0, exth_start + 8)
        pos = exth_start + 12
        for _ in range(count):
            rec_type, rec_len = struct.unpack_from(">II", r0, pos)
            if rec_type == _EXTH_THUMB_OFFSET and rec_len == 12:
                (thumb_off,) = struct.unpack_from(">I", r0, pos + 8)
                excluded.add(first_image_index + thumb_off)
            elif rec_type == _EXTH_COVER_OFFSET and rec_len == 12:
                (cover_off,) = struct.unpack_from(">I", r0, pos + 8)
                cover_index = first_image_index + cover_off
            pos += rec_len

    return first_image_index, cover_index, excluded


def _page_records(data: bytes, records: list[tuple[int, int]]) -> list[int]:
    """Record indices of the page images, in page order.

    The EXTH-declared cover comes first (comic MOBIs append the cover
    record after the page records), then the remaining image records in
    record order, which is the reading order. The EXTH-declared
    thumbnail record is skipped (a shrunken duplicate of the cover, not
    a page).
    """
    first_image_index, cover_index, excluded = _parse_header(data, records)

    page_indices = []
    for idx in range(first_image_index, len(records)):
        start, end = records[idx]
        payload = data[start:end]
        if idx in excluded or idx == cover_index:
            continue
        if any(payload.startswith(m) for m in _NON_IMAGE_MAGICS):
            continue
        if _image_ext(payload) is None:
            continue
        page_indices.append(idx)

    # Drop a cover record of an unexpected type
    if cover_index is not None:
        start, end = records[cover_index]
        if _image_ext(data[start:end]) is not None:
            page_indices.insert(0, cover_index)

    if not page_indices:
        raise MobiFormatError("no page images found in records")
    return page_indices


def extract_images_from_mobi(mobi_path: str, output_dir: str) -> int:
    """Extract page images from a MOBI/AZW/AZW3 into output_dir.

    Images are written as 0001.jpg / 0002.png / ... in the page order of
    _page_records.

    Returns the number of images written. Raises MobiFormatError (or
    other exceptions) when the file doesn't match expectations — callers
    should fall back to the ebook-convert path.
    """
    with open(mobi_path, "rb") as f:
        data = f.read()

    records = _parse_records(data)
    for count, idx in enumerate(_page_records(data, records), 1):
        start, end = records[idx]
        payload = data[start:end]
        out_path = os.path.join(output_dir,
                                f"{count:04d}{_image_ext(payload)}")
        with open(out_path, "wb") as f:
            f.write(payload)
    return count


def read_mobi_metadata(mobi_path: str) -> dict[str, str]:
    """Read title/author from the MOBI EXTH header (best effort)."""
    with open(mobi_path, "rb") as f:
        data = f.read(1 << 20)
    meta = {"title": "", "author": ""}
    try:
        if data[60:68] != b"BOOKMOBI":
            return meta
        r0_start = struct.unpack_from(">I", data, 78)[0]
        r0 = data[r0_start:]
        if r0[16:20] != b"MOBI":
            return meta
        (header_length,) = struct.unpack_from(">I", r0, 20)
        (encoding,) = struct.unpack_from(">I", r0, 28)
        codec = "utf-8" if encoding == 65001 else "cp1252"
        (exth_flags,) = struct.unpack_from(">I", r0, 16 + 112)
        if not exth_flags & 0x40:
            return meta
        exth_start = 16 + header_length
        if r0[exth_start:exth_start + 4] != b"EXTH":
            return meta
        (count,) = struct.unpack_from(">I", r0, exth_start + 8)
        pos = exth_start + 12
        for _ in range(count):
            rec_type, rec_len = struct.unpack_from(">II", r0, pos)
            payload = r0[pos + 8:pos + rec_len]
            if rec_type == _EXTH_UPDATED_TITLE and not meta["title"]:
                meta["title"] = payload.decode(codec, "replace")
            elif rec_type == _EXTH_AUTHOR and not meta["author"]:
                meta["author"] = payload.decode(codec, "replace")
            pos += rec_len
    except Exception:
        pass
    return meta


# ===========================================================================
# Table of contents (NCX index)
# ===========================================================================
#
# The NCX is an INDX index (record number at record0[0xF4]) whose entries
# carry a label (tag 3, an offset into the CNCX string records), a parent
# entry (tag 21) and a position: a raw-text file position (tag 1) in
# MOBI7, a fragment id + offset pair (tag 6) in KF8. A position is mapped
# to a page by finding the first image referenced from that point of the
# text: <img recindex="NNNNN"> in MOBI7, kindle:embed:XXXX in the KF8
# file (skeleton) containing it.

_COMPRESSION_NONE = 1
_COMPRESSION_PALMDOC = 2
_COMPRESSION_HUFFCDIC = 17480

_NCX_TAG_POS = 1
_NCX_TAG_LABEL = 3
_NCX_TAG_POS_FID = 6
_NCX_TAG_PARENT = 21
_SKEL_TAG_SPAN = 6


def _vwi(data: bytes, pos: int) -> tuple[int, int]:
    """Forward variable-width int (high bit marks the last byte).

    Returns (value, bytes consumed).
    """
    value = 0
    consumed = 0
    while True:
        b = data[pos + consumed]
        consumed += 1
        value = (value << 7) | (b & 0x7F)
        if b & 0x80:
            return value, consumed


def _trailing_size(rec: bytes, flags: int) -> int:
    """Size of the trailing entries appended to a text record."""
    size = len(rec)
    num = 0
    test = flags >> 1
    while test:
        if test & 1:
            # Backward variable-width int; the size includes itself
            value = 0
            shift = 0
            end = size - num
            while end > 0:
                b = rec[end - 1]
                value |= (b & 0x7F) << shift
                shift += 7
                end -= 1
                if b & 0x80 or shift >= 28:
                    break
            num += value
        test >>= 1
    if flags & 1:
        num += (rec[size - num - 1] & 0x3) + 1
    return num


def _palmdoc_decompress(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        c = data[i]
        i += 1
        if 1 <= c <= 8:
            out += data[i:i + c]
            i += c
        elif c < 0x80:
            out.append(c)
        elif c >= 0xC0:
            out.append(0x20)
            out.append(c ^ 0x80)
        else:
            c = (c << 8) | data[i]
            i += 1
            dist = (c >> 3) & 0x7FF
            for _ in range((c & 7) + 3):
                out.append(out[-dist])
    return bytes(out)


class _HuffCdic:
    """HUFF/CDIC text decompressor (Mobipocket "huffdic" compression)."""

    def __init__(self, huff: bytes, cdics: list[bytes]):
        if huff[:8] != b"HUFF\x00\x00\x00\x18":
            raise MobiFormatError("bad HUFF record")
        off1, off2 = struct.unpack_from(">LL", huff, 8)

        def dict1_entry(v):
            codelen, term, maxcode = v & 0x1F, v & 0x80, v >> 8
            return codelen, term, ((maxcode + 1) << (32 - codelen)) - 1

        self._dict1 = [dict1_entry(v)
                       for v in struct.unpack_from(">256L", huff, off1)]
        dict2 = struct.unpack_from(">64L", huff, off2)
        self._mincode = [0] + [v << (32 - n)
                               for n, v in enumerate(dict2[0::2], 1)]
        self._maxcode = [0] + [((v + 1) << (32 - n)) - 1
                               for n, v in enumerate(dict2[1::2], 1)]

        self._dictionary = []
        for cdic in cdics:
            if cdic[:8] != b"CDIC\x00\x00\x00\x10":
                raise MobiFormatError("bad CDIC record")
            phrases, bits = struct.unpack_from(">LL", cdic, 8)
            n = min(1 << bits, phrases - len(self._dictionary))
            for off in struct.unpack_from(">%dH" % n, cdic, 16):
                (blen,) = struct.unpack_from(">H", cdic, 16 + off)
                self._dictionary.append(
                    [cdic[18 + off:18 + off + (blen & 0x7FFF)],
                     bool(blen & 0x8000)])

    def unpack(self, data: bytes) -> bytes:
        bits_left = len(data) * 8
        data += b"\x00" * 8
        pos = 0
        (x,) = struct.unpack_from(">Q", data, pos)
        n = 32
        out = bytearray()
        while True:
            if n <= 0:
                pos += 4
                (x,) = struct.unpack_from(">Q", data, pos)
                n += 32
            code = (x >> n) & 0xFFFFFFFF
            codelen, term, maxcode = self._dict1[code >> 24]
            if not term:
                while code < self._mincode[codelen]:
                    codelen += 1
                maxcode = self._maxcode[codelen]
            n -= codelen
            bits_left -= codelen
            if bits_left < 0:
                return bytes(out)
            entry = self._dictionary[(maxcode - code) >> (32 - codelen)]
            if not entry[1]:
                # Phrases are themselves compressed; expand once and cache
                entry[0] = self.unpack(entry[0])
                entry[1] = True
            out += entry[0]


def _read_text(data: bytes, records: list[tuple[int, int]],
               r0: bytes) -> bytes:
    """Decompress the book's raw markup text."""
    (compression,) = struct.unpack_from(">H", r0, 0)
    (text_count,) = struct.unpack_from(">H", r0, 8)
    (encryption,) = struct.unpack_from(">H", r0, 12)
    (header_length,) = struct.unpack_from(">I", r0, 20)
    if encryption:
        raise MobiFormatError("encrypted text")
    flags = 0
    if header_length >= 0xE4:
        (flags,) = struct.unpack_from(">H", r0, 0xF2)

    if compression == _COMPRESSION_HUFFCDIC:
        huff_first, huff_count = struct.unpack_from(">LL", r0, 112)
        tables = [data[slice(*records[i])]
                  for i in range(huff_first, huff_first + huff_count)]
        decompress = _HuffCdic(tables[0], tables[1:]).unpack
    elif compression == _COMPRESSION_PALMDOC:
        decompress = _palmdoc_decompress
    elif compression == _COMPRESSION_NONE:
        decompress = bytes
    else:
        raise MobiFormatError("unknown text compression %d" % compression)

    parts = []
    for i in range(1, text_count + 1):
        rec = data[slice(*records[i])]
        rec = rec[:len(rec) - _trailing_size(rec, flags)]
        parts.append(decompress(rec))
    return b"".join(parts)


def _read_index(data: bytes, records: list[tuple[int, int]],
                index: int) -> tuple[list[tuple[bytes, dict]], dict]:
    """Read an INDX index.

    Returns ([(key, {tag: [values]}), ...], {cncx offset: bytes}).
    """
    def header(rec: bytes) -> dict:
        if rec[:4] != b"INDX":
            raise MobiFormatError("bad INDX record")
        names = ("len", "nul1", "type", "gen", "start", "count", "code",
                 "lng", "total", "ordt", "ligt", "nligt", "nctoc")
        return dict(zip(names, struct.unpack_from(">13L", rec, 4)))

    rec = data[slice(*records[index])]
    hdr = header(rec)
    if hdr["code"] == 0xFDEA or struct.unpack_from(">L", rec, 0xA4)[0]:
        raise MobiFormatError("ORDT-encoded index keys are not supported")

    # Tag table: (tag, values per entry, mask, end-of-control-byte flag)
    tagx = hdr["len"]
    if rec[tagx:tagx + 4] != b"TAGX":
        raise MobiFormatError("INDX without TAGX")
    tagx_len, control_bytes = struct.unpack_from(">LL", rec, tagx + 4)
    tag_table = [tuple(rec[p:p + 4]) for p in range(tagx + 12, tagx + tagx_len, 4)]

    # CNCX string records follow the index data records
    cncx = {}
    for j in range(hdr["nctoc"]):
        cdata = data[slice(*records[index + hdr["count"] + 1 + j])]
        pos = 0
        while pos < len(cdata) and cdata[pos] != 0:
            length, consumed = _vwi(cdata, pos)
            cncx[j * 0x10000 + pos] = cdata[pos + consumed:pos + consumed + length]
            pos += consumed + length

    entries = []
    for i in range(index + 1, index + 1 + hdr["count"]):
        rec = data[slice(*records[i])]
        h = header(rec)
        idxt = h["start"]
        starts = [struct.unpack_from(">H", rec, idxt + 4 + 2 * j)[0]
                  for j in range(h["count"])] + [idxt]
        for j in range(h["count"]):
            pos = starts[j]
            key = rec[pos + 1:pos + 1 + rec[pos]]
            entries.append((key, _read_tags(rec, pos + 1 + rec[pos],
                                            control_bytes, tag_table)))
    return entries, cncx


def _read_tags(rec: bytes, pos: int, control_bytes: int,
               tag_table: list[tuple[int, int, int, int]]) -> dict:
    """Decode one index entry's tag values."""
    present = []
    cb_index = 0
    data_pos = pos + control_bytes
    for tag, per_entry, mask, end_flag in tag_table:
        if end_flag:
            cb_index += 1
            continue
        value = rec[pos + cb_index] & mask
        if not value:
            continue
        if value == mask and bin(mask).count("1") > 1:
            # Value count unknown: a vwi gives the byte length instead
            nbytes, consumed = _vwi(rec, data_pos)
            data_pos += consumed
            present.append((tag, None, nbytes, per_entry))
        else:
            while not mask & 1:
                mask >>= 1
                value >>= 1
            present.append((tag, value, None, per_entry))

    tags = {}
    for tag, count, nbytes, per_entry in present:
        values = []
        if count is not None:
            for _ in range(count * per_entry):
                v, consumed = _vwi(rec, data_pos)
                data_pos += consumed
                values.append(v)
        else:
            end = data_pos + nbytes
            while data_pos < end:
                v, consumed = _vwi(rec, data_pos)
                data_pos += consumed
                values.append(v)
        tags[tag] = values
    return tags


def _kf8_target_finder(data: bytes, records, r0: bytes, text: bytes):
    """Map a KF8 NCX entry to the image record it points at."""
    import bisect
    import re

    (first_image_index,) = struct.unpack_from(">I", r0, 16 + 92)
    frag_index, skel_index = struct.unpack_from(">LL", r0, 0xF8)
    frags, _ = _read_index(data, records, frag_index)
    skels, _ = _read_index(data, records, skel_index)

    # Fragment key = its insert position in the raw text
    frag_pos = [int(key) for key, _ in frags]
    # Each file spans from its skeleton start to the next skeleton's
    file_starts = [tags[_SKEL_TAG_SPAN][0] for _, tags in skels]
    file_starts.append(len(text))

    # First image referenced by each file and the files after it
    embed = re.compile(rb"kindle:embed:([0-9A-V]{4})")
    file_image = [None] * len(skels)
    following = None
    for i in range(len(skels) - 1, -1, -1):
        m = embed.search(text, file_starts[i], file_starts[i + 1])
        if m:
            following = first_image_index + int(m.group(1), 32) - 1
        file_image[i] = following

    def target(tags: dict) -> int | None:
        if _NCX_TAG_POS_FID in tags:
            fid, off = tags[_NCX_TAG_POS_FID][:2]
            pos = frag_pos[fid] + off
        else:
            pos = tags[_NCX_TAG_POS][0]
        i = bisect.bisect_right(file_starts, pos) - 1
        return file_image[i] if 0 <= i < len(skels) else None

    return target


def _mobi7_target_finder(data: bytes, records, r0: bytes, text: bytes):
    """Map a MOBI7 NCX entry to the image record it points at."""
    import re

    (first_image_index,) = struct.unpack_from(">I", r0, 16 + 92)
    recindex = re.compile(rb"""recindex=["']?(\d+)""")

    def target(tags: dict) -> int | None:
        m = recindex.search(text, tags[_NCX_TAG_POS][0])
        return first_image_index + int(m.group(1)) - 1 if m else None

    return target


def is_page_list(toc: list[dict], page_count: int) -> bool:
    """True when a TOC merely enumerates pages rather than chapters.

    Some sources (e.g. Kmoe MOBIs) fill the NCX with one "page N" entry
    per page. Detected structurally, independent of label wording: a
    flat list whose entries land on more than half of all pages. A real
    chapter TOC points at a small fraction of the pages.
    """
    if any(entry["children"] for entry in toc):
        return False
    return len({entry["page"] for entry in toc}) * 2 > page_count


def read_mobi_toc(mobi_path: str) -> list[dict]:
    """Read the table of contents of a MOBI/AZW/AZW3 comic.

    Returns a tree of {"title": str, "page": int, "children": [...]},
    where page is the 0-based index into the images written by
    extract_images_from_mobi. Entries whose target is not a page image
    are dropped (their children are kept). Returns [] when the file has
    no NCX or when the NCX is only a page list (see is_page_list).
    Raises on files that don't match expectations — callers should
    treat that as "no TOC".
    """
    with open(mobi_path, "rb") as f:
        data = f.read()

    records = _parse_records(data)
    r0 = data[slice(*records[0])]
    (ncx_index,) = struct.unpack_from(">I", r0, 0xF4)
    if ncx_index == 0xFFFFFFFF:
        return []

    (version,) = struct.unpack_from(">I", r0, 36)
    (encoding,) = struct.unpack_from(">I", r0, 28)
    codec = "utf-8" if encoding == 65001 else "cp1252"

    page_records = _page_records(data, records)
    page_of = {rec: page for page, rec in enumerate(page_records)}
    text = _read_text(data, records, r0)
    finder = _kf8_target_finder if version >= 8 else _mobi7_target_finder
    target = finder(data, records, r0, text)

    entries, cncx = _read_index(data, records, ncx_index)
    nodes = []
    roots = []
    for _, tags in entries:
        node = {
            "title": cncx.get(tags.get(_NCX_TAG_LABEL, [-1])[0], b"")
                         .decode(codec, "replace").strip(),
            "page": page_of.get(target(tags)),
            "children": [],
        }
        parent = tags.get(_NCX_TAG_PARENT, [-1])[0]
        if 0 <= parent < len(nodes):
            nodes[parent]["children"].append(node)
        else:
            roots.append(node)
        nodes.append(node)

    def prune(items: list[dict]) -> list[dict]:
        kept = []
        for item in items:
            item["children"] = prune(item["children"])
            if item["page"] is None or not item["title"]:
                kept.extend(item["children"])
            else:
                kept.append(item)
        return kept

    toc = prune(roots)
    return [] if is_page_list(toc, len(page_records)) else toc


if __name__ == "__main__":
    import sys
    n = extract_images_from_mobi(sys.argv[1], sys.argv[2])
    print(f"extracted {n} images")
