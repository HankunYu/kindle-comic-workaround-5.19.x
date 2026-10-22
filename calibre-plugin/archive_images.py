#!/usr/bin/env python3
"""
Page image extraction from comic archives (ZIP/CBZ, RAR/CBR, 7z/CB7).

A comic archive is just a bag of page images; the reading order is the
natural sort order of their paths ("2.jpg" before "10.jpg", folder by
folder). The container type is sniffed from the file's magic bytes, not
its extension — plenty of ".cbr" files are really ZIPs.

Backends, tried in order:
  - ZIP: the standard library, read in place
  - RAR: Calibre's bundled unrar (calibre.utils.unrar), then the
    unrar / 7z / bsdtar command-line tools
  - 7z: py7zr (bundled with Calibre), then the 7z / bsdtar tools

JPEG and PNG pages pass through byte for byte; other formats (WebP,
GIF, BMP, TIFF) are re-encoded to JPEG, since the KFX writer only takes
JPEG/PNG and Amazon's own comic pipeline only ever emits JPEG pages.
Optional ComicInfo.xml supplies title/author.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

ARCHIVE_EXTS = (".zip", ".cbz", ".rar", ".cbr", ".7z", ".cb7")

_IMAGE_EXTS = {".jpg", ".jpeg", ".jpe", ".png", ".gif", ".webp",
               ".bmp", ".tif", ".tiff"}

# Folders/files archivers and OSes leave behind that are never pages
_JUNK_PARTS = {"__macosx", "thumbs.db", "desktop.ini"}

_MAGIC_ZIP = (b"PK\x03\x04", b"PK\x05\x06")
_MAGIC_RAR = b"Rar!\x1a\x07"
_MAGIC_7Z = b"7z\xbc\xaf\x27\x1c"

# Quality for re-encoding non-JPEG/PNG pages; same as the gamma
# re-encode in kpf_generator, high enough to keep the loss invisible
_JPEG_QUALITY = 92


class ArchiveFormatError(ValueError):
    """Raised when the archive can't be read or holds no page images."""


_ENCRYPTED = "password-protected archives are not supported"


def _archive_kind(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(_MAGIC_ZIP):
        return "zip"
    if head.startswith(_MAGIC_RAR):
        return "rar"
    if head.startswith(_MAGIC_7Z):
        return "7z"
    raise ArchiveFormatError("not a ZIP, RAR or 7z archive")


def _natural_key(path: str) -> tuple:
    """Sort key comparing digit runs numerically, path component by component."""
    return tuple(
        tuple((1, int(tok)) if tok.isdigit() else (0, tok.lower())
              for tok in re.split(r"(\d+)", part))
        for part in path.split("/")
    )


def _is_page_name(name: str) -> bool:
    parts = name.split("/")
    if any(p.startswith(".") or p.lower() in _JUNK_PARTS for p in parts):
        return False
    return Path(name).suffix.lower() in _IMAGE_EXTS


# ============================================================================
# Backends: each yields {posix relative path: zero-arg reader -> bytes}
# ============================================================================

def _zip_name(info: zipfile.ZipInfo) -> str:
    # Entries without the UTF-8 flag are decoded as cp437 by zipfile, but
    # most modern tools write UTF-8 there anyway; recover those names.
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except UnicodeError:
        return info.filename


@contextmanager
def _zip_members(path: str):
    with zipfile.ZipFile(path, "r") as zf:
        if any(i.flag_bits & 0x1 for i in zf.infolist()):
            raise ArchiveFormatError(_ENCRYPTED)
        yield {_zip_name(i): (lambda i=i: zf.read(i))
               for i in zf.infolist() if not i.is_dir()}


def _find_tool(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32" and "7z" in names:
        for base in (os.environ.get("ProgramFiles"),
                     os.environ.get("ProgramFiles(x86)")):
            if base:
                candidate = os.path.join(base, "7-Zip", "7z.exe")
                if os.path.isfile(candidate):
                    return candidate
    return None


def _run_tool(cmd: list[str]) -> None:
    # stdin closed so a password-protected archive fails instead of prompting
    result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise ArchiveFormatError(
            f"{Path(cmd[0]).name} failed: {result.stderr.strip() or result.returncode}")


def _extract_with_tools(path: str, dest: str, tools: list[str]) -> None:
    commands = {
        "unrar": lambda exe: [exe, "x", "-y", "-p-", "-idq", path, dest + os.sep],
        "7z": lambda exe: [exe, "x", "-y", "-p", "-bso0", "-bsp0", f"-o{dest}", path],
        "bsdtar": lambda exe: [exe, "-x", "-f", path, "-C", dest],
    }
    aliases = {"unrar": ("unrar",), "7z": ("7z", "7zz", "7za"),
               "bsdtar": ("bsdtar",)}
    for tool in tools:
        exe = _find_tool(*aliases[tool])
        if exe:
            _run_tool(commands[tool](exe))
            return
    raise ArchiveFormatError(
        "no extractor available; install one of: " + ", ".join(tools))


def _extract_rar(path: str, dest: str) -> None:
    try:
        from calibre.utils.unrar import extract
    except ImportError:
        _extract_with_tools(path, dest, ["unrar", "7z", "bsdtar"])
    else:
        extract(path, dest)


def _extract_7z(path: str, dest: str) -> None:
    try:
        import py7zr
    except ImportError:
        _extract_with_tools(path, dest, ["7z", "bsdtar"])
    else:
        try:
            with py7zr.SevenZipFile(path, "r") as archive:
                if archive.needs_password():
                    raise ArchiveFormatError(_ENCRYPTED)
                archive.extractall(path=dest)
        except py7zr.exceptions.PasswordRequired:
            raise ArchiveFormatError(_ENCRYPTED) from None


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


@contextmanager
def _extracted_members(path: str, extract):
    with tempfile.TemporaryDirectory(prefix="kfx-archive-") as tmp:
        extract(path, tmp)
        root = os.path.realpath(tmp)
        members = {}
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                # Never follow links an archive planted to escape the tmp dir
                if os.path.islink(full):
                    continue
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                members[rel] = (lambda full=full: _read_file(full))
        yield members


def _members(path: str):
    kind = _archive_kind(path)
    if kind == "zip":
        return _zip_members(path)
    if kind == "rar":
        return _extracted_members(path, _extract_rar)
    return _extracted_members(path, _extract_7z)


# ============================================================================
# Public API
# ============================================================================

def _image_ext(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return None


def _to_jpeg(data: bytes) -> bytes:
    """Re-encode a non-JPEG/PNG page as JPEG (first frame only).

    Transparency is flattened onto white; grayscale stays grayscale.
    """
    from PIL import Image

    with Image.open(BytesIO(data)) as im:
        if im.mode == "P":
            im = im.convert("RGBA" if "transparency" in im.info else "RGB")
        if im.mode in ("RGBA", "LA"):
            background = Image.new(im.mode[:-1], im.size, "white")
            background.paste(im, mask=im.getchannel("A"))
            im = background
        elif im.mode == "1":
            im = im.convert("L")
        elif im.mode not in ("L", "RGB"):
            im = im.convert("RGB")
        out = BytesIO()
        im.save(out, "JPEG", quality=_JPEG_QUALITY)
    return out.getvalue()


def _read_comicinfo(data: bytes) -> dict[str, str]:
    root = ET.fromstring(data)

    def text(tag: str) -> str:
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else ""

    title = text("Title")
    if not title and text("Series"):
        title = " ".join(filter(None, (text("Series"), text("Number"))))
    return {"title": title, "author": text("Writer") or text("Penciller")}


class ComicArchive:
    """An opened comic archive; create it with open_archive()."""

    def __init__(self, members: dict):
        self._members = members

    def extract_images(self, output_dir: str) -> int:
        """Write the page images to output_dir as 0001.jpg / 0002.png / ...

        Returns the number of images written. Raises ArchiveFormatError
        when the archive holds no page images.
        """
        names = sorted((n for n in self._members if _is_page_name(n)),
                       key=lambda n: (_natural_key(n), n))
        if not names:
            raise ArchiveFormatError("no page images found in archive")

        for count, name in enumerate(names, 1):
            data = self._members[name]()
            ext = _image_ext(data)
            if ext is None:
                data, ext = _to_jpeg(data), ".jpg"
            out_path = os.path.join(output_dir, f"{count:04d}{ext}")
            with open(out_path, "wb") as f:
                f.write(data)
        return count

    def read_metadata(self) -> dict[str, str]:
        """Title/author from a ComicInfo.xml, if any (best effort)."""
        for name, read in self._members.items():
            if name.split("/")[-1].lower() == "comicinfo.xml":
                try:
                    return _read_comicinfo(read())
                except ET.ParseError:
                    break
        return {"title": "", "author": ""}


@contextmanager
def open_archive(path: str):
    """Open a ZIP/CBZ, RAR/CBR or 7z/CB7 comic archive.

    RAR and 7z archives are unpacked to a temporary directory that lives
    until the context exits; ZIPs are read in place.
    """
    with _members(path) as members:
        yield ComicArchive(members)
