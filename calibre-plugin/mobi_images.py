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


def extract_images_from_mobi(mobi_path: str, output_dir: str) -> int:
    """Extract page images from a MOBI/AZW/AZW3 into output_dir.

    Images are written as 0001.jpg / 0002.png / ... — the EXTH-declared
    cover first (comic MOBIs append the cover record after the page
    records), then the remaining image records in record order, which is
    the reading order. The EXTH-declared thumbnail record is skipped (a
    shrunken duplicate of the cover, not a page).

    Returns the number of images written. Raises MobiFormatError (or
    other exceptions) when the file doesn't match expectations — callers
    should fall back to the ebook-convert path.
    """
    with open(mobi_path, "rb") as f:
        data = f.read()

    records = _parse_records(data)
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

    ordered = ([cover_index] if cover_index is not None else []) + page_indices

    count = 0
    for idx in ordered:
        start, end = records[idx]
        payload = data[start:end]
        ext = _image_ext(payload)
        if ext is None:      # cover record of an unexpected type
            continue
        count += 1
        out_path = os.path.join(output_dir, f"{count:04d}{ext}")
        with open(out_path, "wb") as f:
            f.write(payload)

    if count == 0:
        raise MobiFormatError("no page images found in records")
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


if __name__ == "__main__":
    import sys
    n = extract_images_from_mobi(sys.argv[1], sys.argv[2])
    print(f"extracted {n} images")
