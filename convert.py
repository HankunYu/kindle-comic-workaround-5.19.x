#!/usr/bin/env python3
"""
Manga/comic to KFX conversion pipeline for Kindle devices.

Pipeline:
  1. Convert to EPUB if needed (PDF/MOBI/AZW via Calibre's ebook-convert)
  2. Extract images from EPUB in spine reading order
  3. Generate KPF via custom KPF generator (reverse-engineered format)
  4. Convert KPF to KFX via Calibre KFX Output plugin
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from kpf_generator import generate_kpf, DEFAULT_GAMMA

# Namespace constants
NS_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
NS_OPF = "http://www.idpf.org/2007/opf"
NS_XHTML = "http://www.w3.org/1999/xhtml"
NS_SVG = "http://www.w3.org/2000/svg"
NS_XLINK = "http://www.w3.org/1999/xlink"


def find_calibre_debug() -> str | None:
    """Find calibre-debug binary across platforms."""
    if platform.system() == "Darwin":
        path = "/Applications/calibre.app/Contents/MacOS/calibre-debug"
        if os.path.isfile(path):
            return path
    elif platform.system() == "Windows":
        for candidate in [
            os.path.expandvars(r"%ProgramFiles%\Calibre2\calibre-debug.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Calibre2\calibre-debug.exe"),
        ]:
            if os.path.isfile(candidate):
                return candidate
    # Fallback: check PATH
    return shutil.which("calibre-debug")


def find_opf_path(epub_zip: zipfile.ZipFile) -> str:
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


def parse_spine_items(epub_zip: zipfile.ZipFile, opf_path: str) -> list[tuple[str, str]]:
    """
    Parse OPF to get spine-ordered content documents.

    Returns a list of (item_id, href) tuples in spine order.
    The href is resolved relative to the OPF directory.
    """
    opf_xml = epub_zip.read(opf_path)
    root = ET.fromstring(opf_xml)
    opf_dir = str(Path(opf_path).parent)
    if opf_dir == ".":
        opf_dir = ""

    manifest = root.find(f"{{{NS_OPF}}}manifest")
    if manifest is None:
        raise ValueError("No manifest found in OPF")

    id_to_href = {}
    for item in manifest.findall(f"{{{NS_OPF}}}item"):
        item_id = item.get("id")
        href = item.get("href")
        if item_id and href:
            if opf_dir:
                full_href = f"{opf_dir}/{href}"
            else:
                full_href = href
            id_to_href[item_id] = full_href

    spine = root.find(f"{{{NS_OPF}}}spine")
    if spine is None:
        raise ValueError("No spine found in OPF")

    items = []
    for itemref in spine.findall(f"{{{NS_OPF}}}itemref"):
        idref = itemref.get("idref")
        if idref and idref in id_to_href:
            items.append((idref, id_to_href[idref]))

    return items


def extract_images_from_xhtml(epub_zip: zipfile.ZipFile, xhtml_path: str) -> list[str]:
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

    try:
        root = ET.fromstring(xhtml_bytes)

        for image_el in root.iter(f"{{{NS_SVG}}}image"):
            href = image_el.get(f"{{{NS_XLINK}}}href")
            if href:
                image_hrefs.append(href)

        for img_el in root.iter(f"{{{NS_XHTML}}}img"):
            src = img_el.get("src")
            if src:
                image_hrefs.append(src)

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

    if not image_hrefs:
        xhtml_text = xhtml_bytes.decode("utf-8", errors="replace")

        for match in re.finditer(r'<image[^>]+xlink:href=["\']([^"\']+)["\']', xhtml_text):
            image_hrefs.append(match.group(1))

        if not image_hrefs:
            for match in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', xhtml_text):
                image_hrefs.append(match.group(1))

    results = []
    seen = set()
    for href in image_hrefs:
        if xhtml_dir:
            full_path = str(Path(f"{xhtml_dir}/{href}"))
        else:
            full_path = href
        full_path = os.path.normpath(full_path)
        full_path = full_path.replace("\\", "/")
        if full_path not in seen:
            seen.add(full_path)
            results.append(full_path)

    return results


def extract_metadata(epub_path: str) -> dict[str, str]:
    """Extract title and author from EPUB metadata."""
    metadata = {"title": Path(epub_path).stem, "author": ""}
    try:
        with zipfile.ZipFile(epub_path, "r") as epub_zip:
            opf_path = find_opf_path(epub_zip)
            opf_xml = epub_zip.read(opf_path)
            root = ET.fromstring(opf_xml)

            ns_dc = "http://purl.org/dc/elements/1.1/"

            title_el = root.find(f".//{{{ns_dc}}}title")
            if title_el is not None and title_el.text:
                metadata["title"] = title_el.text.strip()

            creator_el = root.find(f".//{{{ns_dc}}}creator")
            if creator_el is not None and creator_el.text:
                metadata["author"] = creator_el.text.strip()
    except Exception:
        pass

    return metadata


def extract_images(epub_path: str, output_dir: str) -> int:
    """
    Extract images from EPUB in spine reading order.

    Images are renamed sequentially: 0001.jpg, 0002.png, etc.
    Returns the number of images extracted.
    """
    count = 0
    with zipfile.ZipFile(epub_path, "r") as epub_zip:
        opf_path = find_opf_path(epub_zip)
        spine_items = parse_spine_items(epub_zip, opf_path)
        zip_names = set(epub_zip.namelist())

        for _, xhtml_href in spine_items:
            if xhtml_href not in zip_names:
                continue

            image_paths = extract_images_from_xhtml(epub_zip, xhtml_href)
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


def run_kpf_generation(image_dir: str, kpf_path: str, title: str = "",
                       author: str = "", reading_direction: str = "rtl",
                       language: str = "ja", virtual_panels: str = "off",
                       facing_pages: bool = False,
                       facing_start: str = "single",
                       gamma: float = DEFAULT_GAMMA) -> None:
    """Generate KPF from images using the custom KPF generator."""
    image_paths = sorted([
        os.path.join(image_dir, f) for f in os.listdir(image_dir)
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
        gamma=gamma,
    )


def run_kfx_conversion(kpf_path: str, kfx_path: str) -> None:
    """Convert a KPF to KFX using Calibre's KFX Output plugin directly."""
    calibre = find_calibre_debug()
    if not calibre:
        raise FileNotFoundError("calibre-debug not found. Install Calibre from https://calibre-ebook.com/")

    cmd = [
        calibre,
        "-r", "KFX Output",
        "--",
        kpf_path,
        kfx_path,
    ]

    print(f"    Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"    Calibre stdout: {result.stdout}")
        print(f"    Calibre stderr: {result.stderr}")
        raise RuntimeError(f"KFX conversion failed with return code {result.returncode}")

    if not os.path.exists(kfx_path):
        raise RuntimeError(f"KFX output not found: {kfx_path}")


SUPPORTED_FORMATS = (".epub", ".mobi", ".azw", ".azw3", ".pdf")


def convert_to_epub_if_needed(input_path: str, tmp_dir: str) -> str:
    """Convert MOBI/AZW/PDF to EPUB if needed. Returns path to EPUB file."""
    ext = Path(input_path).suffix.lower()
    if ext == ".epub":
        return input_path

    calibre_convert = find_calibre_debug()
    if not calibre_convert:
        raise FileNotFoundError("calibre-debug not found")

    # Use ebook-convert (same directory as calibre-debug)
    convert_bin = str(Path(calibre_convert).parent / "ebook-convert")
    if not os.path.isfile(convert_bin):
        convert_bin = shutil.which("ebook-convert")
    if not convert_bin:
        raise FileNotFoundError("ebook-convert not found")

    epub_path = os.path.join(tmp_dir, Path(input_path).stem + ".epub")
    # --no-default-epub-cover: without it, Calibre injects a titlepage for
    # every book — a synthetic placeholder when the source has no cover
    # (would become a fake 0001.jpg), and a wrapper around the real cover
    # when it has one. The flag suppresses only the placeholder, while a
    # genuine cover keeps its titlepage and is extracted as page 1.
    cmd = [convert_bin, input_path, epub_path, "--no-default-epub-cover"]
    print(f"    Converting {ext} to EPUB...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to convert {ext} to EPUB: {result.stderr}")
    return epub_path


def convert_to_kfx(input_path: str, output_dir: str,
                   reading_direction: str = "rtl",
                   virtual_panels: str = "off",
                   facing_pages: bool = False,
                   facing_start: str = "single",
                   gamma: float = DEFAULT_GAMMA) -> None:
    """
    Full pipeline: EPUB/MOBI -> extract images -> KPF -> KFX.

    Args:
        input_path: Path to the source manga file (EPUB, MOBI, AZW, AZW3, PDF).
        output_dir: Directory where the final KFX file will be placed.
        reading_direction: "rtl" for right-to-left, "ltr" for left-to-right.
        virtual_panels: "off", "horizontal", or "vertical".
        facing_pages: Enable facing pages (spreads) for landscape viewing.
        facing_start: "single" (default) keeps page 1 solo, "double" pairs from page 1.
        gamma: Display-gamma compensation for Kindle e-ink; 1.0 disables.
    """
    input_name = Path(input_path).stem
    kfx_output = os.path.join(output_dir, f"{input_name}.kfx")

    print(f"\n{'='*60}")
    print(f"Processing: {input_name}")
    print(f"Output: {kfx_output}")
    print(f"{'='*60}")

    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"File not found: {input_path}")

    with tempfile.TemporaryDirectory(prefix="kindle-comic-") as tmp_dir:
        # Convert to EPUB if needed
        epub_path = convert_to_epub_if_needed(input_path, tmp_dir)

        image_dir = os.path.join(tmp_dir, "images")
        os.makedirs(image_dir)

        # Step 1: Extract metadata and images from EPUB
        print(f"\n[1/3] Extracting from EPUB...")
        metadata = extract_metadata(epub_path)
        print(f"    Title: {metadata['title']}")
        if metadata["author"]:
            print(f"    Author: {metadata['author']}")
        image_count = extract_images(epub_path, image_dir)
        if image_count == 0:
            raise RuntimeError("No images found in file")
        print(f"    Extracted {image_count} images")

        # Step 2: Generate KPF
        kpf_path = os.path.join(tmp_dir, f"{input_name}.kpf")
        print(f"\n[2/3] Generating KPF...")
        if gamma and abs(gamma - 1.0) > 1e-3:
            print(f"    Gamma correction: {gamma}")
        run_kpf_generation(image_dir, kpf_path,
                           title=metadata["title"], author=metadata["author"],
                           reading_direction=reading_direction,
                           virtual_panels=virtual_panels,
                           facing_pages=facing_pages,
                           facing_start=facing_start,
                           gamma=gamma)
        kpf_size = os.path.getsize(kpf_path) / (1024 * 1024)
        print(f"    KPF: {kpf_size:.1f} MB")

        # Step 3: Convert KPF to KFX
        print(f"\n[3/3] Converting KPF to KFX...")
        os.makedirs(output_dir, exist_ok=True)
        run_kfx_conversion(kpf_path, kfx_output)
        print(f"    Output: {kfx_output}")

    print(f"\nDone: {kfx_output}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert manga/comic files to KFX format for Kindle devices."
    )
    parser.add_argument(
        "--output", "-o",
        default=".",
        help="Output directory for KFX files (default: current directory)",
    )
    parser.add_argument(
        "--direction",
        choices=["rtl", "ltr"],
        default="rtl",
        help="Reading direction: rtl (manga) or ltr (comic) (default: rtl)",
    )
    parser.add_argument(
        "--virtual-panels",
        choices=["off", "horizontal", "vertical"],
        default="off",
        help="Virtual panel navigation mode (default: off)",
    )
    parser.add_argument(
        "--facing-pages",
        action="store_true",
        help="Enable facing pages (spreads) for landscape viewing",
    )
    parser.add_argument(
        "--facing-start",
        choices=["single", "double"],
        default="single",
        help="Facing-pages start mode: 'single' keeps page 1 solo (cover) and "
             "pairs from page 2; 'double' pairs from page 1 (default: single)",
    )
    parser.add_argument(
        "--gamma",
        type=float,
        default=DEFAULT_GAMMA,
        help="Display-gamma compensation for Kindle e-ink. Brightens midtones "
             "at conversion time the same way Kindle Previewer / Send-to-Kindle "
             "do, so pages don't render darker than Amazon-converted books. "
             f"Use 1.0 to keep original image bytes untouched (default: {DEFAULT_GAMMA})",
    )
    parser.add_argument(
        "input_files",
        nargs="+",
        metavar="file",
        help="One or more EPUB/MOBI/AZW/AZW3/PDF files to convert",
    )

    args = parser.parse_args()

    calibre = find_calibre_debug()
    if not calibre:
        print("Error: calibre-debug not found. Install Calibre from https://calibre-ebook.com/")
        sys.exit(1)
    print(f"Calibre: {calibre}")

    success_count = 0
    fail_count = 0

    for input_file in args.input_files:
        ext = Path(input_file).suffix.lower()
        if ext not in SUPPORTED_FORMATS:
            print(f"Skipping {input_file}: unsupported format '{ext}'")
            fail_count += 1
            continue
        try:
            convert_to_kfx(input_file, args.output, args.direction,
                          args.virtual_panels, args.facing_pages,
                          args.facing_start, args.gamma)
            success_count += 1
        except Exception as e:
            print(f"\nError processing {input_file}: {e}")
            fail_count += 1

    total = success_count + fail_count
    print(f"\n{'='*60}")
    print(f"Summary: {success_count}/{total} succeeded, {fail_count}/{total} failed")
    print(f"{'='*60}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
