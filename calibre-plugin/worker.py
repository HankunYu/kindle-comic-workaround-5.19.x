"""
Background conversion worker for KFX Comic Output plugin.

Handles the full pipeline: extract images from EPUB/CBZ/PDF -> generate KPF -> convert to KFX.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Pillow is bundled with Calibre
from PIL import Image

from calibre_plugins.kfx_comic_output.config import get_prefs


# XML namespace constants for EPUB parsing
NS_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
NS_OPF = "http://www.idpf.org/2007/opf"
NS_XHTML = "http://www.w3.org/1999/xhtml"
NS_SVG = "http://www.w3.org/2000/svg"
NS_XLINK = "http://www.w3.org/1999/xlink"
NS_DC = "http://purl.org/dc/elements/1.1/"


def _find_calibre_debug():
    """Locate the calibre-debug executable. Uses Calibre's own path when running as a plugin."""
    # When running inside Calibre, we can derive the path from sys.executable
    calibre_dir = os.path.dirname(sys.executable)
    if sys.platform == "win32":
        candidate = os.path.join(calibre_dir, "calibre-debug.exe")
    elif sys.platform == "darwin":
        candidate = os.path.join(calibre_dir, "calibre-debug")
    else:
        candidate = os.path.join(calibre_dir, "calibre-debug")

    if os.path.isfile(candidate):
        return candidate

    # Fallback: try well-known paths
    if sys.platform == "darwin":
        fallback = "/Applications/calibre.app/Contents/MacOS/calibre-debug"
        if os.path.isfile(fallback):
            return fallback

    # Fallback: PATH lookup
    found = shutil.which("calibre-debug")
    if found:
        return found

    raise FileNotFoundError(
        "calibre-debug not found. Ensure Calibre is installed properly."
    )


# ============================================================================
# EPUB image extraction (adapted from convert.py)
# ============================================================================

def _find_opf_path(epub_zip):
    """Read META-INF/container.xml to locate the OPF file path."""
    container_xml = epub_zip.read("META-INF/container.xml")
    root = ET.fromstring(container_xml)
    rootfiles = root.find(f".//{{{NS_CONTAINER}}}rootfile")
    if rootfiles is None:
        raise ValueError("No rootfile found in container.xml")
    opf_path = rootfiles.get("full-path")
    if not opf_path:
        raise ValueError("rootfile has no full-path attribute")
    return opf_path


def _parse_spine_items(epub_zip, opf_path):
    """Parse OPF to get spine-ordered content document hrefs."""
    opf_xml = epub_zip.read(opf_path)
    root = ET.fromstring(opf_xml)
    opf_dir = str(Path(opf_path).parent)
    if opf_dir == ".":
        opf_dir = ""

    # Build manifest id -> href mapping
    manifest = root.find(f"{{{NS_OPF}}}manifest")
    if manifest is None:
        raise ValueError("No manifest found in OPF")

    id_to_href = {}
    for item in manifest.findall(f"{{{NS_OPF}}}item"):
        item_id = item.get("id")
        href = item.get("href")
        if item_id and href:
            full_href = f"{opf_dir}/{href}" if opf_dir else href
            id_to_href[item_id] = full_href

    # Get spine order
    spine = root.find(f"{{{NS_OPF}}}spine")
    if spine is None:
        raise ValueError("No spine found in OPF")

    items = []
    for itemref in spine.findall(f"{{{NS_OPF}}}itemref"):
        idref = itemref.get("idref")
        if idref and idref in id_to_href:
            items.append((idref, id_to_href[idref]))

    return items


def _extract_images_from_xhtml(epub_zip, xhtml_path):
    """
    Extract all image references from an XHTML spine item.
    Handles both <img src="..."> and <svg><image xlink:href="..."> patterns.
    Falls back to regex if XML parsing fails.
    Returns a list of resolved image paths (may contain one or many).
    """
    xhtml_bytes = epub_zip.read(xhtml_path)
    xhtml_dir = str(Path(xhtml_path).parent)
    if xhtml_dir == ".":
        xhtml_dir = ""

    image_hrefs = []

    # Try XML parsing first
    try:
        root = ET.fromstring(xhtml_bytes)

        # Look for <svg><image xlink:href="...">
        for image_el in root.iter(f"{{{NS_SVG}}}image"):
            href = image_el.get(f"{{{NS_XLINK}}}href")
            if href:
                image_hrefs.append(href)

        # Look for <img src="..."> (XHTML namespace)
        for img_el in root.iter(f"{{{NS_XHTML}}}img"):
            src = img_el.get("src")
            if src:
                image_hrefs.append(src)

        # Also try without namespace
        if not image_hrefs:
            for img_el in root.iter("img"):
                src = img_el.get("src")
                if src:
                    image_hrefs.append(src)

            for image_el in root.iter("image"):
                href = image_el.get(f"{{{NS_XLINK}}}href") or image_el.get("href")
                if href:
                    image_hrefs.append(href)

    except ET.ParseError:
        pass

    # Regex fallback
    if not image_hrefs:
        xhtml_text = xhtml_bytes.decode("utf-8", errors="replace")
        for match in re.finditer(r'<image[^>]+xlink:href=["\']([^"\']+)["\']', xhtml_text):
            image_hrefs.append(match.group(1))
        if not image_hrefs:
            for match in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', xhtml_text):
                image_hrefs.append(match.group(1))

    # Resolve relative paths and deduplicate
    results = []
    seen = set()
    for href in image_hrefs:
        if xhtml_dir:
            full_path = str(Path(f"{xhtml_dir}/{href}"))
        else:
            full_path = href
        full_path = os.path.normpath(full_path).replace("\\", "/")
        if full_path not in seen:
            seen.add(full_path)
            results.append(full_path)

    return results


def _is_calibre_default_cover_page(xhtml_bytes):
    """
    Detect Calibre's auto-generated placeholder cover page.

    When Calibre converts inputs that lack an embedded cover (e.g. PDF via
    ebook-convert), its EPUB Output plugin injects a titlepage.xhtml marked
    with <meta name="calibre:cover" content="true"/>. The referenced
    cover_image.jpg is a synthetic placeholder (solid-color background with
    file name + author), not the real first page of the source.

    Native manga/comic EPUBs use their own cover page without this marker,
    so filtering titlepages by this meta is safe — it only drops Calibre's
    fake placeholder, letting the real first content page become 0001.jpg.
    """
    # Cheap prefilter — avoid regex on every xhtml
    if b"calibre:cover" not in xhtml_bytes:
        return False
    # Full check: a <meta> element with both name="calibre:cover" and content="true",
    # in either attribute order.
    pattern = (
        rb'<meta\b[^>]*\bname=["\']calibre:cover["\'][^>]*\bcontent=["\']true["\']'
        rb'|<meta\b[^>]*\bcontent=["\']true["\'][^>]*\bname=["\']calibre:cover["\']'
    )
    return bool(re.search(pattern, xhtml_bytes))


def _extract_images_from_epub(epub_path, output_dir):
    """
    Extract images from EPUB in spine reading order.
    Images are renamed sequentially: 0001.jpg, 0002.png, etc.
    Returns the number of images extracted.
    """
    count = 0
    with zipfile.ZipFile(epub_path, "r") as epub_zip:
        opf_path = _find_opf_path(epub_zip)
        spine_items = _parse_spine_items(epub_zip, opf_path)
        zip_names = set(epub_zip.namelist())

        for _, xhtml_href in spine_items:
            if xhtml_href not in zip_names:
                continue

            # Skip Calibre-generated placeholder titlepages (see helper docstring).
            # Otherwise the synthetic cover_image.jpg would become 0001.jpg and
            # push the real first page to 0002.jpg — which is the PDF cover bug.
            if _is_calibre_default_cover_page(epub_zip.read(xhtml_href)):
                continue

            image_paths = _extract_images_from_xhtml(epub_zip, xhtml_href)
            for image_path in image_paths:
                if image_path not in zip_names:
                    continue

                ext = Path(image_path).suffix.lower()
                if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                    ext = ".jpg"
                if ext == ".jpeg":
                    ext = ".jpg"

                count += 1
                seq_name = f"{count:04d}{ext}"
                image_data = epub_zip.read(image_path)

                out_path = os.path.join(output_dir, seq_name)
                with open(out_path, "wb") as f:
                    f.write(image_data)

    return count


def _extract_images_from_cbz(cbz_path, output_dir):
    """
    Extract images from a CBZ file (ZIP of images) in sorted order.
    Returns the number of images extracted.
    """
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    count = 0

    with zipfile.ZipFile(cbz_path, "r") as zf:
        entries = sorted(
            [n for n in zf.namelist()
             if Path(n).suffix.lower() in image_exts
             and not Path(n).name.startswith(".")],
        )

        for entry in entries:
            ext = Path(entry).suffix.lower()
            if ext == ".jpeg":
                ext = ".jpg"

            count += 1
            seq_name = f"{count:04d}{ext}"
            image_data = zf.read(entry)

            out_path = os.path.join(output_dir, seq_name)
            with open(out_path, "wb") as f:
                f.write(image_data)

    return count


def _extract_metadata_from_epub(epub_path):
    """Extract title and author from EPUB metadata."""
    metadata = {"title": Path(epub_path).stem, "author": ""}
    try:
        with zipfile.ZipFile(epub_path, "r") as epub_zip:
            opf_path = _find_opf_path(epub_zip)
            opf_xml = epub_zip.read(opf_path)
            root = ET.fromstring(opf_xml)

            title_el = root.find(f".//{{{NS_DC}}}title")
            if title_el is not None and title_el.text:
                metadata["title"] = title_el.text.strip()

            creator_el = root.find(f".//{{{NS_DC}}}creator")
            if creator_el is not None and creator_el.text:
                metadata["author"] = creator_el.text.strip()
    except Exception:
        pass

    return metadata


# ============================================================================
# Main conversion pipeline
# ============================================================================

def convert_book(book_info, log=None):
    """
    Convert a single book from EPUB/CBZ to KFX.

    Args:
        book_info: Dict with keys: book_id, title, author, source_path, source_fmt
        log: Optional Calibre log object.

    Returns:
        Path to the generated KFX file (in a temp directory; caller must clean up).
    """
    from calibre_plugins.kfx_comic_output.kpf_generator import generate_kpf

    prefs = get_prefs()
    reading_direction = prefs["reading_direction"]
    language = prefs["language"]
    virtual_panels = prefs.get("virtual_panels", "off")
    facing_pages = prefs.get("facing_pages", False)
    facing_start = prefs.get("facing_start", "single")

    source_path = book_info["source_path"]
    source_fmt = book_info["source_fmt"]
    title = book_info["title"]
    author = book_info["author"]
    book_stem = Path(source_path).stem

    if log:
        log.info(f"KFX Comic: Converting '{title}' from {source_fmt}")

    # Create a temp directory that persists until caller cleans up the KFX file
    tmp_dir = tempfile.mkdtemp(prefix="kfx-comic-")
    image_dir = os.path.join(tmp_dir, "images")
    os.makedirs(image_dir)

    try:
        # Step 1: Extract images
        if log:
            log.info(f"KFX Comic: Extracting images from {source_fmt}...")

        epub_path = source_path
        if source_fmt in ("MOBI", "AZW", "AZW3", "PDF"):
            # Convert to EPUB first using Calibre's ebook-convert
            if log:
                log.info(f"KFX Comic: Converting {source_fmt} to EPUB...")
            epub_path = os.path.join(tmp_dir, Path(source_path).stem + ".epub")
            calibre_debug = _find_calibre_debug()
            exe = ".exe" if sys.platform == "win32" else ""
            convert_bin = str(Path(calibre_debug).parent / f"ebook-convert{exe}")
            if not os.path.isfile(convert_bin):
                convert_bin = shutil.which("ebook-convert")
            if not convert_bin:
                raise FileNotFoundError("ebook-convert not found")
            result = subprocess.run(
                [convert_bin, source_path, epub_path],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to convert {source_fmt} to EPUB")
            source_fmt = "EPUB"

        if source_fmt == "EPUB":
            epub_meta = _extract_metadata_from_epub(epub_path)
            if not title or title == "Unknown":
                title = epub_meta["title"]
            if not author:
                author = epub_meta["author"]
            image_count = _extract_images_from_epub(epub_path, image_dir)
        elif source_fmt == "CBZ":
            image_count = _extract_images_from_cbz(source_path, image_dir)
        else:
            raise ValueError(f"Unsupported format: {source_fmt}")

        if image_count == 0:
            raise RuntimeError(f"No images found in {source_fmt} file")

        if log:
            log.info(f"KFX Comic: Extracted {image_count} images")

        # Step 2: Generate KPF
        if log:
            log.info(f"KFX Comic: Generating KPF...")

        kpf_path = os.path.join(tmp_dir, f"{book_stem}.kpf")

        image_paths = sorted([
            os.path.join(image_dir, f)
            for f in os.listdir(image_dir)
            if os.path.isfile(os.path.join(image_dir, f))
            and Path(f).suffix.lower() in (".jpg", ".jpeg", ".png")
        ])

        generate_kpf(
            image_paths=image_paths,
            output_path=kpf_path,
            title=title,
            author=author,
            reading_direction=reading_direction,
            language=language,
            virtual_panels=virtual_panels,
            facing_pages=facing_pages,
            facing_start=facing_start,
        )

        if log:
            kpf_size_mb = os.path.getsize(kpf_path) / (1024 * 1024)
            log.info(f"KFX Comic: KPF generated ({kpf_size_mb:.1f} MB)")

        # Step 3: Convert KPF to KFX via calibre-debug
        if log:
            log.info(f"KFX Comic: Converting KPF to KFX...")

        kfx_path = os.path.join(tmp_dir, f"{book_stem}.kfx")

        calibre_debug = _find_calibre_debug()
        cmd = [
            calibre_debug,
            "-r", "KFX Output",
            "--",
            kpf_path,
            kfx_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            error_detail = f"stdout: {result.stdout}\nstderr: {result.stderr}"
            if log:
                log.error(f"KFX conversion failed:\n{error_detail}")
            raise RuntimeError(
                f"KFX conversion failed (exit code {result.returncode}). "
                f"Check that the KFX Output plugin is installed."
            )

        if not os.path.isfile(kfx_path):
            raise RuntimeError(
                "KFX output file was not created. "
                "Check that the KFX Output plugin is installed and working."
            )

        if log:
            kfx_size_mb = os.path.getsize(kfx_path) / (1024 * 1024)
            log.info(f"KFX Comic: KFX generated ({kfx_size_mb:.1f} MB)")

        # Clean up intermediate files but keep the KFX
        shutil.rmtree(image_dir, ignore_errors=True)
        if os.path.isfile(kpf_path):
            os.unlink(kpf_path)

        return kfx_path

    except Exception:
        # On failure, clean up the entire temp dir
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
