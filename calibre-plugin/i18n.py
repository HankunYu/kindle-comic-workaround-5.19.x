"""
Lightweight i18n helper for the KFX Comic Output plugin.

Detects Calibre's UI language via calibre.utils.localization.get_lang()
and picks between inline English / Simplified Chinese string tables.
No .mo files required — strings live here for easy maintenance.

Usage:
    from calibre_plugins.kfx_comic_output.i18n import T
    label = T("Reading direction")
    msg = T("Converting {n} comic(s) to KFX...").format(n=3)
"""

# English source string -> Simplified Chinese. If a key is missing,
# T() falls back to the source string so the UI never breaks.
_ZH = {
    # ui.py — toolbar action and menus
    "Convert Comics to KFX": "漫画转 KFX",
    "Convert selected manga/comic books to KFX format for Kindle":
        "将所选漫画转换为 Kindle 的 KFX 格式",
    "Convert selected books": "转换所选图书",
    "Reading direction": "阅读方向",
    "Right to Left (manga)": "从右到左(日漫)",
    "Left to Right (comic)": "从左到右(美漫)",
    "Virtual panels": "虚拟分镜",
    "Facing pages (spreads)": "对开页(跨页)",
    "Facing pages start": "对开页起始",
    "Start with single page (cover)": "首页单独(封面)",
    "Start with double page": "首页直接配对",
    "Language": "语言",
    "Gamma correction": "伽马校正",

    # config.py — gamma correction labels
    "Off (keep original, default)": "关闭(保持原图,默认)",
    "1.4 (light)": "1.4(轻度)",
    "1.8 (brighten, match reflowable look)": "1.8(提亮,匹配流式书观感)",
    "2.2 (strong)": "2.2(强)",

    # config.py — language labels
    "Japanese": "日语",
    "Chinese": "中文",
    "Korean": "韩语",
    "English": "英语",

    # config.py — virtual panels labels
    "Off": "关闭",
    "Horizontal": "水平",
    "Vertical": "垂直",

    # jobs.py — dialog titles
    "No books selected": "未选择图书",
    "Nothing to convert": "没有可转换的图书",
    "Some books skipped": "部分图书已跳过",
    "Conversion failed": "转换失败",
    "Conversion partially complete": "部分转换完成",
    "Conversion complete": "转换完成",

    # jobs.py — dialog body text
    "Please select one or more comic/manga books to convert.":
        "请先选择要转换的漫画。",
    "No convertible books found in selection.":
        "所选图书中没有可转换的内容。",
    "The following books were skipped (no supported format):":
        "以下图书已跳过(无支持的格式):",
    "(no supported format)": "(无支持的格式)",
    "(file not found)": "(文件未找到)",
    "Cancelled": "已取消",
    "KFX output file not found": "未找到 KFX 输出文件",

    # jobs.py — templates (formatted with .format())
    "Converting {n} comic(s) to KFX...": "正在将 {n} 本漫画转换为 KFX...",
    "[{i}/{n}] {title}": "[{i}/{n}] {title}",
    "{s} succeeded, {f} failed.": "{s} 本成功,{f} 本失败。",
    "Successfully converted {n} book(s) to KFX.":
        "已成功将 {n} 本图书转换为 KFX。",
    "Successfully converted {n} book(s):": "成功转换 {n} 本图书:",
    "Failed to convert {n} book(s):": "转换失败 {n} 本:",
    "Failed to add KFX to library: {error}": "添加 KFX 到书库失败:{error}",
    "{title}: {error}": "{title}:{error}",

    # ui.py / updater.py — update checker
    "Check for updates": "检查更新",
    "Update available": "发现新版本",
    "No updates": "已是最新版本",
    "Update check failed": "检查更新失败",
    "A new version {new} is available "
    "(currently installed: {cur}).\n\nOpen the release page?":
        "发现新版本 {new}(当前已安装:{cur})。\n\n是否打开发布页面?",
    "You are already running the latest version ({cur}).":
        "当前已是最新版本({cur})。",
    "Could not check for updates: {error}": "无法检查更新:{error}",
    "Releases page: {url}": "发布页面:{url}",
}


def _is_chinese():
    """Return True when Calibre's UI language is any Chinese variant."""
    try:
        from calibre.utils.localization import get_lang
        lang = (get_lang() or "").lower()
    except Exception:
        return False
    # Matches zh, zh_CN, zh-Hans, zh_TW, zh-Hant, etc.
    return lang.startswith("zh")


def T(s):
    """Translate a source string to the current Calibre UI language.

    Falls back to the source string when no translation exists or when
    the UI is not in Chinese.
    """
    if _is_chinese():
        return _ZH.get(s, s)
    return s
