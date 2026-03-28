# kindle-comic-workaround-5.19.x

Convert manga/comic EPUB to KFX format — a workaround for blank pages, white borders, and slow loading issues on Kindle firmware 5.19.x.

将漫画 EPUB 转换为 KFX 格式——解决 Kindle 固件 5.19.x 上漫画出现空白页、白边和加载缓慢的问题。

## Problem / 问题

Since Kindle firmware 5.19.2, sideloaded manga and comics have display issues:

- **Send to Kindle**: Blank pages and extremely slow loading
- **EPUB/MOBI sideloading**: White borders around pages, images don't fill the screen

自 Kindle 固件 5.19.2 起，侧载的漫画出现显示问题：

- **Send to Kindle**：出现空白页，加载极慢
- **EPUB/MOBI 侧载**：页面周围出现白边，图片无法铺满屏幕

## Solution / 解决方案

Convert EPUB to KFX format via a reverse-engineered KPF (Kindle Publishing Format) generator. KFX is Kindle's native format and renders comics correctly without the issues above.

通过逆向工程的 KPF（Kindle Publishing Format）生成器将 EPUB 转换为 KFX 格式。KFX 是 Kindle 的原生格式，能正确渲染漫画，不会出现上述问题。

**Pipeline:**

```
EPUB → Extract images → Generate KPF → Convert to KFX
```

## Requirements / 依赖

- Python 3.10+
- [Pillow](https://pypi.org/project/Pillow/)
- [Calibre](https://calibre-ebook.com/) with [KFX Output](https://www.mobileread.com/forums/showthread.php?t=272407) plugin installed

## Installation / 安装

```bash
git clone https://github.com/HankunYu/kindle-comic-workaround-5.19.x.git
cd kindle-comic-workaround-5.19.x
pip install -r requirements.txt
```

## Usage / 使用方法

### CLI

```bash
# Single file / 单个文件
python convert.py manga.epub

# Multiple files / 批量转换
python convert.py *.epub

# Specify output directory / 指定输出目录
python convert.py -o output/ manga.epub
```

### Calibre Plugin / Calibre 插件

Install the plugin:

安装插件：

```bash
calibre-customize -a kfx-comic-output.zip
```

Or in Calibre GUI: **Preferences → Plugins → Load plugin from file** → select `kfx-comic-output.zip`

或在 Calibre GUI 中：**首选项 → 插件 → 从文件加载插件** → 选择 `kfx-comic-output.zip`

Then select books in Calibre and click **"Convert Comics to KFX"** in the toolbar.

然后在 Calibre 中选中书籍，点击工具栏的 **"Convert Comics to KFX"** 按钮。

### Transfer to Kindle / 传输到 Kindle

Copy the `.kfx` file to your Kindle's `documents` folder via USB.

通过 USB 将 `.kfx` 文件复制到 Kindle 的 `documents` 文件夹。

## How It Works / 工作原理

1. **Extract images** from EPUB in spine reading order
2. **Generate KPF** using a reverse-engineered Kindle Publishing Format generator (bypasses the GUI-only Kindle Create tool)
3. **Convert KPF to KFX** via Calibre's KFX Output plugin

---

1. 按 spine 阅读顺序从 EPUB 中**提取图片**
2. 使用逆向工程的 KPF 生成器**生成 KPF**（绕过了只有 GUI 的 Kindle Create 工具）
3. 通过 Calibre 的 KFX Output 插件将 **KPF 转换为 KFX**

## License / 许可证

MIT
