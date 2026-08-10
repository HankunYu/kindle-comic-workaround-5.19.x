# kindle-comic-workaround-5.19.x

Convert manga/comic to KFX format — a workaround for blank pages, white borders, and slow loading issues on Kindle firmware 5.19.x.

将漫画转换为 KFX 格式——解决 Kindle 固件 5.19.x 上漫画出现空白页、白边和加载缓慢的问题。

## Problem / 问题

Since Kindle firmware 5.19.2, sideloaded manga and comics have display issues:

- **Send to Kindle**: Blank pages and extremely slow loading
- **EPUB/MOBI sideloading**: White borders around pages, images don't fill the screen

自 Kindle 固件 5.19.2 起，侧载的漫画出现显示问题：

- **Send to Kindle**：出现空白页，加载极慢
- **EPUB/MOBI 侧载**：页面周围出现白边，图片无法铺满屏幕

## Solution / 解决方案

Convert manga/comic files (EPUB, MOBI, AZW, AZW3, PDF) to KFX format via a fully self-contained, reverse-engineered KFX writer — no Kindle Previewer, no third-party Calibre plugins. KFX is Kindle's native format and renders comics correctly without the issues above.

将漫画文件（EPUB、MOBI、AZW、AZW3、PDF）通过完全自研的逆向工程 KFX 生成器转换为 KFX 格式——不依赖 Kindle Previewer，也不依赖任何第三方 Calibre 插件。KFX 是 Kindle 的原生格式，能正确渲染漫画，不会出现上述问题。

```
EPUB/MOBI/AZW/PDF → Extract images → Generate KFX
```

Typical conversion takes well under a second per volume: MOBI page images are read directly at the record level (no format round trip), and the KFX container is written in a single pass.

单卷转换通常不到一秒：MOBI 页面图片直接按 record 层读取（无格式往返），KFX 容器单趟写出。

## Requirements / 依赖

- Calibre plugin: just [Calibre](https://calibre-ebook.com/) — nothing else
- CLI:
  - Python 3.10+
  - [Pillow](https://pypi.org/project/Pillow/) (`pip install -r requirements.txt`)
  - Calibre is only needed for PDF input (and as a fallback for unusual MOBI files); EPUB/MOBI/AZW input needs no Calibre at all

依赖说明：
- Calibre 插件：只需要 [Calibre](https://calibre-ebook.com/) 本体，无需其他插件
- CLI 工具：
  - Python 3.10+
  - [Pillow](https://pypi.org/project/Pillow/)（`pip install -r requirements.txt`）
  - 仅 PDF 输入需要 Calibre（非常规 MOBI 会回退到 `ebook-convert`）；EPUB/MOBI/AZW 输入完全不需要 Calibre

## Usage / 使用方法

### Calibre Plugin (recommended) / Calibre 插件（推荐）

Install: download `kfx-comic-output.zip` from [Releases](https://github.com/HankunYu/kindle-comic-workaround-5.19.x/releases), then:

安装：从 [Releases](https://github.com/HankunYu/kindle-comic-workaround-5.19.x/releases) 下载 `kfx-comic-output.zip`，然后：

```bash
calibre-customize -a kfx-comic-output.zip
```

Or in Calibre GUI: **Preferences → Plugins → Load plugin from file** → select `kfx-comic-output.zip`

或在 Calibre GUI 中：**首选项 → 插件 → 从文件加载插件** → 选择 `kfx-comic-output.zip`

If the button doesn't appear in the toolbar after installation, add it manually: **Preferences → Toolbars & menus → The main toolbar** → find "Convert Comics to KFX" in the left list → move it to the right → Apply.

如果安装后工具栏没有显示按钮，需要手动添加：**首选项 → 工具栏和菜单 → 主工具栏** → 在左侧列表找到 "Convert Comics to KFX" → 移到右侧 → 应用。

Then:
1. Select books in Calibre
2. Click **"Convert Comics to KFX"** in the toolbar
3. Use the dropdown arrow to configure:
   - **Reading direction**: Right to Left (manga) / Left to Right (comic)
   - **Virtual panels**: Off / Horizontal / Vertical (guided panel navigation)
   - **Facing pages**: Enable spread view for landscape reading
   - **Facing pages start**: Single (cover solo, then 2+3, 4+5...) / Double (1+2, 3+4...)
   - **Gamma correction**: Off (default) / 1.4 / 1.8 / 2.2 — Kindle firmware renders fixed-layout comics through a darker tone path than reflowable books; 1.8 brightens midtones to compensate. Off keeps original image bytes untouched
   - **Language**: Japanese / Chinese / Korean / English

使用：
1. 在 Calibre 中选中书籍
2. 点击工具栏的 **"Convert Comics to KFX"** 按钮
3. 通过下拉箭头配置：
   - **阅读方向**：从右到左（日漫）/ 从左到右（美漫）
   - **虚拟面板**：关闭 / 水平 / 垂直（引导式面板导航）
   - **对开页**：启用横屏双页显示
   - **对开页起始**：首页单独（封面单独，再 2+3、4+5…配对）/ 首页直接配对（1+2、3+4…）
   - **伽马校正**：关闭（默认）/ 1.4 / 1.8 / 2.2 —— Kindle 固件渲染固定版式漫画的色调比流式书更深，1.8 通过提亮中间调补偿；关闭则保持原始图片字节不变
   - **语言**：日语 / 中文 / 韩语 / 英语

### CLI

```bash
# EPUB
python convert.py manga.epub

# MOBI / AZW / AZW3
python convert.py manga.mobi

# PDF
python convert.py manga.pdf

# Left to right reading direction / 从左到右阅读
python convert.py --direction ltr comic.epub

# Facing pages for landscape / 对开页横屏阅读
python convert.py --facing-pages manga.epub

# Facing pages, pair from page 1 / 对开页从首页直接配对
python convert.py --facing-pages --facing-start double manga.epub

# Virtual panel navigation / 虚拟面板导航
python convert.py --virtual-panels horizontal manga.epub

# Enable gamma brightening for the Kindle comic rendering path (off by default)
# 启用伽马提亮补偿 Kindle 漫画渲染路径（默认关闭）
python convert.py --gamma 1.8 manga.epub

# Multiple files / 批量转换
python convert.py *.epub *.mobi *.pdf

# Specify output directory / 指定输出目录
python convert.py -o output/ manga.epub
```

### Transfer to Kindle / 传输到 Kindle

Copy the `.kfx` file to your Kindle's `documents` folder via USB, or use Calibre's Send to Device.

通过 USB 将 `.kfx` 文件复制到 Kindle 的 `documents` 文件夹，或使用 Calibre 的发送到设备功能。

## How It Works / 工作原理

1. **Extract images** in reading order — MOBI/AZW page images are read directly out of the PalmDB records (cover reordered to front, thumbnail record skipped); EPUB is parsed via its spine; PDF goes through Calibre's ebook-convert
2. **Generate KFX directly** with a self-contained writer: it builds the Kindle YJ fragment structures (sections, storylines, position maps, metadata) and serializes them into a single KFX container — byte-for-byte equivalent to the output of the Kindle Create → KFX Output plugin toolchain, verified fragment by fragment

---

1. 按阅读顺序**提取图片**——MOBI/AZW 直接从 PalmDB record 中读取页面图片（封面重排到最前，跳过缩略图记录）；EPUB 按 spine 解析；PDF 走 Calibre 的 ebook-convert
2. 使用自研序列化器**直接生成 KFX**：构造 Kindle YJ fragment 结构（章节、故事线、位置映射、元数据）并序列化为单容器 KFX——与 Kindle Create → KFX Output 插件工具链的产物逐字节等价（已逐 fragment 验证）

## License / 许可证

MIT
