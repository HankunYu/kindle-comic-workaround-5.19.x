# Plugin preferences stored via Calibre's JSONConfig

PREFS_NAMESPACE = "KFXComicOutputPlugin"

DEFAULTS = {
    "reading_direction": "rtl",
    "language": "zh",
}

LANGUAGES = {
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "en": "English",
}


def get_prefs():
    """Load plugin preferences with defaults."""
    from calibre.utils.config import JSONConfig
    prefs = JSONConfig("plugins/" + PREFS_NAMESPACE)
    prefs.defaults = DEFAULTS.copy()
    return prefs
