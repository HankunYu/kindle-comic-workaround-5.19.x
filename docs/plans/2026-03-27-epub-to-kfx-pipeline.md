# EPUB to KFX Manga Conversion Pipeline

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automate manga EPUB → KFX conversion optimized for Kindle Scribe and Colorsoft, eliminating the white border issue on firmware 5.19.3.

**Architecture:** Python CLI script that extracts images from EPUB in spine order, feeds them to KCC for device-specific optimization, then converts KCC's output EPUB to KFX via Calibre's KFX Output plugin. All temp files are cleaned up automatically.

**Tech Stack:** Python 3.11, KCC (from GitHub), Calibre 9.6 + KFX Output 2.18.0, Pillow, lxml

---

## Task 1: Set Up KCC in Project Virtual Environment

**Files:**
- Create: `venv/` (Python virtual environment)
- Create: `setup.sh` (one-time setup script)

**Step 1: Create venv and install KCC**

```bash
cd /Users/hankun/GitHub/kindle-comic
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install git+https://github.com/ciromattia/kcc.git
```

**Step 2: Verify kcc-c2e is available**

Run: `source venv/bin/activate && kcc-c2e --help`
Expected: Usage help output listing profiles and options

**Step 3: Write setup.sh for reproducibility**

```bash
#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install git+https://github.com/ciromattia/kcc.git
echo "Setup complete. Run: source venv/bin/activate"
```

**Step 4: Commit**

```bash
git add setup.sh
git commit -m "feat: add setup script for KCC virtual environment"
```

---

## Task 2: Write EPUB Image Extractor

**Files:**
- Create: `convert.py` (main script, start with extractor logic)

**Step 1: Write the EPUB image extraction function**

Core logic:
1. EPUB is a ZIP → open with `zipfile`
2. Read `META-INF/container.xml` → find OPF path
3. Parse OPF → get `<spine>` order (list of itemrefs)
4. For each spine item → read the referenced XHTML file
5. In each XHTML → find the `<img src="...">` or `<svg><image xlink:href="...">` tag
6. Extract that image file from the ZIP
7. Save as `0001.jpg`, `0002.jpg`, ... in a temp directory

Key considerations:
- Namespace handling in OPF: `http://www.idpf.org/2007/opf`
- Namespace handling in XHTML: `http://www.w3.org/1999/xhtml`
- SVG images use `xlink:href`: `http://www.w3.org/1999/xlink`
- Image paths in XHTML are relative to the XHTML file location
- Preserve original image format (JPG/PNG) during extraction

```python
import os
import re
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {
    'container': 'urn:oasis:names:tc:opendocument:xmlns:container',
    'opf': 'http://www.idpf.org/2007/opf',
    'xhtml': 'http://www.w3.org/1999/xhtml',
    'xlink': 'http://www.w3.org/1999/xlink',
    'svg': 'http://www.w3.org/2000/svg',
}

def extract_images_from_epub(epub_path: str, output_dir: str) -> list[str]:
    """Extract images from EPUB in spine reading order.

    Returns list of extracted image paths.
    """
    with zipfile.ZipFile(epub_path, 'r') as zf:
        # 1. Find OPF path from container.xml
        container = ET.fromstring(zf.read('META-INF/container.xml'))
        rootfile = container.find('.//container:rootfile', NS)
        opf_path = rootfile.get('full-path')
        opf_dir = os.path.dirname(opf_path)

        # 2. Parse OPF - build manifest map
        opf = ET.fromstring(zf.read(opf_path))
        manifest = {}
        for item in opf.findall('.//opf:manifest/opf:item', NS):
            manifest[item.get('id')] = item.get('href')

        # 3. Get spine order
        spine = opf.findall('.//opf:spine/opf:itemref', NS)

        # 4. For each spine item, find and extract the image
        extracted = []
        counter = 0
        for itemref in spine:
            idref = itemref.get('idref')
            href = manifest.get(idref)
            if not href:
                continue

            # Resolve XHTML path relative to OPF
            xhtml_path = f"{opf_dir}/{href}" if opf_dir else href
            xhtml_content = zf.read(xhtml_path).decode('utf-8')
            xhtml_dir = os.path.dirname(xhtml_path)

            # Find image reference in XHTML
            img_rel = _find_image_in_xhtml(xhtml_content)
            if not img_rel:
                continue

            # Resolve image path relative to XHTML
            img_path = f"{xhtml_dir}/{img_rel}" if xhtml_dir else img_rel
            # Normalize path (handle ../ etc)
            img_path = os.path.normpath(img_path)

            # Determine extension
            ext = os.path.splitext(img_path)[1] or '.jpg'

            # Extract image
            counter += 1
            out_name = f"{counter:04d}{ext}"
            out_path = os.path.join(output_dir, out_name)

            with open(out_path, 'wb') as f:
                f.write(zf.read(img_path))

            extracted.append(out_path)

    return extracted


def _find_image_in_xhtml(xhtml_content: str) -> str | None:
    """Find image path from XHTML content.

    Handles both <img src="..."> and <svg><image xlink:href="..."> patterns.
    """
    try:
        root = ET.fromstring(xhtml_content)
    except ET.ParseError:
        # Fallback to regex if XML parsing fails
        return _find_image_regex(xhtml_content)

    # Try <img> tag
    for img in root.iter(f"{{{NS['xhtml']}}}img"):
        src = img.get('src')
        if src:
            return src

    # Try <image> tag (SVG wrapped)
    for img in root.iter(f"{{{NS['svg']}}}image"):
        href = img.get(f"{{{NS['xlink']}}}href") or img.get('href')
        if href:
            return href

    # Try without namespace (some EPUBs are loose)
    for img in root.iter('img'):
        src = img.get('src')
        if src:
            return src

    return _find_image_regex(xhtml_content)


def _find_image_regex(content: str) -> str | None:
    """Fallback regex to find image references."""
    # Match src="..." or xlink:href="..." pointing to image files
    patterns = [
        r'<img[^>]+src=["\']([^"\']+\.(jpe?g|png|webp|gif))["\']',
        r'xlink:href=["\']([^"\']+\.(jpe?g|png|webp|gif))["\']',
        r'href=["\']([^"\']+\.(jpe?g|png|webp|gif))["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, content, re.IGNORECASE)
        if m:
            return m.group(1)
    return None
```

**Step 2: Test extraction manually with a real EPUB**

Run: `python3 -c "from convert import extract_images_from_epub; imgs = extract_images_from_epub('test.epub', '/tmp/test_extract'); print(f'Extracted {len(imgs)} images')"`
Expected: Images extracted with sequential naming

**Step 3: Commit**

```bash
git add convert.py
git commit -m "feat: add EPUB image extraction in spine order"
```

---

## Task 3: Wire Up KCC Conversion Step

**Files:**
- Modify: `convert.py` (add KCC invocation)

**Step 1: Add KCC subprocess call**

```python
import subprocess

DEVICE_PROFILES = {
    'scribe': 'KS',
    'colorsoft': 'KO',
}

def run_kcc(input_dir: str, output_dir: str, device: str = 'scribe', title: str = '') -> str:
    """Run KCC to optimize images for target Kindle device.

    Returns path to the output EPUB file.
    """
    profile = DEVICE_PROFILES[device]

    cmd = [
        'kcc-c2e',
        '-p', profile,
        '-m',              # manga mode (right-to-left)
        '--forcecolor',    # preserve color for Colorsoft
        '-f', 'EPUB',      # output format
        '-o', output_dir,  # output directory
    ]

    if title:
        cmd.extend(['-t', title])

    cmd.append(input_dir)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"KCC failed:\n{result.stderr}")

    # Find the output EPUB in output_dir
    epub_files = list(Path(output_dir).glob('*.epub'))
    if not epub_files:
        raise RuntimeError(f"KCC produced no EPUB output in {output_dir}")

    return str(epub_files[0])
```

**Step 2: Test KCC step independently**

Run: `source venv/bin/activate && python3 -c "from convert import run_kcc; print(run_kcc('/tmp/test_extract', '/tmp/kcc_out'))"`
Expected: KCC processes images and produces an EPUB

**Step 3: Commit**

```bash
git add convert.py
git commit -m "feat: add KCC optimization step"
```

---

## Task 4: Wire Up Calibre KFX Output Step

**Files:**
- Modify: `convert.py` (add KFX conversion)

**Step 1: Add Calibre KFX Output call**

```python
CALIBRE_DEBUG = '/Applications/calibre.app/Contents/MacOS/calibre-debug'

def convert_to_kfx(epub_path: str, output_path: str) -> str:
    """Convert EPUB to KFX using Calibre's KFX Output plugin.

    Returns path to the output KFX file.
    """
    cmd = [
        CALIBRE_DEBUG,
        '-r', 'KFX Output',
        '--',
        epub_path,
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"KFX conversion failed:\n{result.stderr}")

    if not os.path.exists(output_path):
        raise RuntimeError(f"KFX output not found: {output_path}")

    return output_path
```

**Step 2: Test KFX conversion independently**

Run: `python3 -c "from convert import convert_to_kfx; print(convert_to_kfx('/tmp/kcc_out/test.epub', '/tmp/test.kfx'))"`
Expected: KFX file produced successfully

**Step 3: Commit**

```bash
git add convert.py
git commit -m "feat: add Calibre KFX Output conversion step"
```

---

## Task 5: Wire Up Full Pipeline and CLI

**Files:**
- Modify: `convert.py` (add main pipeline + argparse CLI)

**Step 1: Add full pipeline function**

```python
def convert_epub_to_kfx(epub_path: str, output_dir: str, device: str = 'scribe') -> str:
    """Full pipeline: EPUB → extract images → KCC → KFX.

    Returns path to the output KFX file.
    """
    epub_path = os.path.abspath(epub_path)
    output_dir = os.path.abspath(output_dir)
    book_name = Path(epub_path).stem

    with tempfile.TemporaryDirectory(prefix='kindle_comic_') as tmpdir:
        # Step 1: Extract images
        img_dir = os.path.join(tmpdir, 'images')
        os.makedirs(img_dir)
        print(f"[1/3] Extracting images from {Path(epub_path).name}...")
        images = extract_images_from_epub(epub_path, img_dir)
        print(f"       Extracted {len(images)} pages")

        # Step 2: KCC optimization
        kcc_dir = os.path.join(tmpdir, 'kcc_output')
        os.makedirs(kcc_dir)
        print(f"[2/3] Optimizing for {device} with KCC...")
        optimized_epub = run_kcc(img_dir, kcc_dir, device=device, title=book_name)
        print(f"       Output: {Path(optimized_epub).name}")

        # Step 3: Convert to KFX
        kfx_path = os.path.join(output_dir, f"{book_name}.kfx")
        print(f"[3/3] Converting to KFX...")
        convert_to_kfx(optimized_epub, kfx_path)
        print(f"       Done: {kfx_path}")

    return kfx_path
```

**Step 2: Add argparse CLI**

```python
def main():
    parser = argparse.ArgumentParser(
        description='Convert manga EPUB to KFX for Kindle'
    )
    parser.add_argument(
        'epub_files',
        nargs='+',
        help='EPUB file(s) to convert',
    )
    parser.add_argument(
        '-d', '--device',
        choices=['scribe', 'colorsoft'],
        default='scribe',
        help='Target Kindle device (default: scribe)',
    )
    parser.add_argument(
        '-o', '--output',
        default='.',
        help='Output directory (default: current directory)',
    )
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    for epub in args.epub_files:
        if not os.path.isfile(epub):
            print(f"Skipping {epub}: file not found")
            continue
        try:
            convert_epub_to_kfx(epub, args.output, device=args.device)
        except Exception as e:
            print(f"Error converting {epub}: {e}")

if __name__ == '__main__':
    main()
```

**Step 3: Test full pipeline end-to-end**

Run: `source venv/bin/activate && python3 convert.py --device scribe test_manga.epub`
Expected: KFX file produced in current directory

Run: `source venv/bin/activate && python3 convert.py --device colorsoft -o output/ *.epub`
Expected: Multiple KFX files produced in output/

**Step 4: Commit**

```bash
git add convert.py
git commit -m "feat: complete EPUB to KFX pipeline with CLI"
```

---

## Task 6: Write setup.sh and Final Polish

**Files:**
- Create: `setup.sh`
- Create: `.gitignore`

**Step 1: Write setup.sh**

```bash
#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip -q
pip install git+https://github.com/ciromattia/kcc.git -q

# Verify dependencies
echo "Checking dependencies..."

command -v kcc-c2e >/dev/null 2>&1 || { echo "ERROR: kcc-c2e not found"; exit 1; }

CALIBRE="/Applications/calibre.app/Contents/MacOS/calibre-debug"
[ -x "$CALIBRE" ] || { echo "ERROR: Calibre not found at $CALIBRE"; exit 1; }

$CALIBRE -r "KFX Output" -- --help >/dev/null 2>&1 || { echo "ERROR: KFX Output plugin not installed in Calibre"; exit 1; }

echo "All dependencies OK."
echo ""
echo "Usage:"
echo "  source venv/bin/activate"
echo "  python3 convert.py [--device scribe|colorsoft] [-o output_dir] file.epub [...]"
```

**Step 2: Write .gitignore**

```
venv/
__pycache__/
*.pyc
*.kfx
*.epub
```

**Step 3: Commit**

```bash
git add setup.sh .gitignore convert.py
git commit -m "feat: add setup script, gitignore, finalize pipeline"
```

---

## Summary

| Step | What | Tool |
|------|------|------|
| Extract | EPUB → images in spine order | Python zipfile + xml.etree |
| Optimize | Images → device-resolution EPUB | KCC (kcc-c2e) |
| Convert | Optimized EPUB → KFX | Calibre KFX Output plugin |

**CLI usage:**
```bash
# One-time setup
./setup.sh

# Single file
source venv/bin/activate
python3 convert.py manga.epub

# Batch with device selection
python3 convert.py --device colorsoft -o output/ *.epub
```
