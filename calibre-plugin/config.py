# Plugin preferences stored via Calibre's JSONConfig

PREFS_NAMESPACE = "KFXComicOutputPlugin"

DEFAULTS = {
    "reading_direction": "rtl",
    "language": "zh",
    "virtual_panels": "off",
    "facing_pages": False,
    "facing_start": "single",
}

LANGUAGES = {
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "en": "English",
}

VIRTUAL_PANELS = {
    "off": "Off",
    "horizontal": "Horizontal",
    "vertical": "Vertical",
}

FACING_START = {
    "single": "Start with single page (cover)",
    "double": "Start with double page",
}


def get_prefs():
    """Load plugin preferences with defaults."""
    from calibre.utils.config import JSONConfig
    prefs = JSONConfig("plugins/" + PREFS_NAMESPACE)
    prefs.defaults = DEFAULTS.copy()
    return prefs
