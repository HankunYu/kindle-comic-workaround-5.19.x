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

Convert manga/comic files (EPUB, MOBI, AZW, AZW3) to KFX format via a reverse-engineered KPF (Kindle Publishing Format) generator. KFX is Kindle's native format and renders comics correctly without the issues above.

将漫画文件（EPUB、MOBI、AZW、AZW3）通过逆向工程的 KPF 生成器转换为 KFX 格式。KFX 是 Kindle 的原生格式，能正确渲染漫画，不会出现上述问题。

```
EPUB/MOBI/AZW → Extract images → Generate KPF → Convert to KFX
```

## Requirements / 依赖

- [Calibre](https://calibre-ebook.com/) with [KFX Output](https://www.mobileread.com/forums/showthread.php?t=272407) plugin installed

CLI only:
- Python 3.10+
- [Pillow](https://pypi.org/project/Pillow/) (`pip install -r requirements.txt`)

仅 CLI 工具需要：
- Python 3.10+
- [Pillow](https://pypi.org/project/Pillow/)（`pip install -r requirements.txt`）

## Usage / 使用方法

### Calibre Plugin (recommended) / Calibre 插件（推荐）

Install: download `kfx-comic-output.zip` from [Releases](https://github.com/HankunYu/kindle-comic-workaround-5.19.x/releases), then:

安装：从 [Releases](https://github.com/HankunYu/kindle-comic-workaround-5.19.x/releases) 下载 `kfx-comic-output.zip`，然后：

```bash
calibre-customize -a kfx-comic-output.zip
```

Or in Calibre GUI: **Preferences → Plugins → Load plugin from file** → select `kfx-comic-output.zip`

或在 Calibre GUI 中：**首选项 → 插件 → 从文件加载插件** → 选择 `kfx-comic-output.zip`

Then:
1. Select books in Calibre
2. Click **"Convert Comics to KFX"** in the toolbar
3. Use the dropdown arrow to switch between **Right to Left** (manga) and **Left to Right** (comic) reading direction

使用：
1. 在 Calibre 中选中书籍
2. 点击工具栏的 **"Convert Comics to KFX"** 按钮
3. 通过下拉箭头切换 **从右到左**（日漫）或 **从左到右**（美漫）阅读方向

### CLI

```bash
# EPUB
python convert.py manga.epub

# MOBI / AZW / AZW3
python convert.py manga.mobi

# Multiple files / 批量转换
python convert.py *.epub *.mobi

# Specify output directory / 指定输出目录
python convert.py -o output/ manga.epub
```

### Transfer to Kindle / 传输到 Kindle

Copy the `.kfx` file to your Kindle's `documents` folder via USB, or use Calibre's Send to Device.

通过 USB 将 `.kfx` 文件复制到 Kindle 的 `documents` 文件夹，或使用 Calibre 的发送到设备功能。

## How It Works / 工作原理

1. **Extract images** from EPUB/MOBI in reading order (MOBI/AZW are first converted to EPUB via Calibre)
2. **Generate KPF** using a reverse-engineered Kindle Publishing Format generator (bypasses the GUI-only Kindle Create tool)
3. **Convert KPF to KFX** via Calibre's KFX Output plugin

---

1. 按阅读顺序从 EPUB/MOBI 中**提取图片**（MOBI/AZW 会先通过 Calibre 转为 EPUB）
2. 使用逆向工程的 KPF 生成器**生成 KPF**（绕过了只有 GUI 的 Kindle Create 工具）
3. 通过 Calibre 的 KFX Output 插件将 **KPF 转换为 KFX**

## License / 许可证

MIT
