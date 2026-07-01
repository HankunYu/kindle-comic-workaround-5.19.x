# Plugin preferences stored via Calibre's JSONConfig

PREFS_NAMESPACE = "KFXComicOutputPlugin"

DEFAULTS = {
    "reading_direction": "rtl",
    "language": "zh",
    "virtual_panels": "off",
    "facing_pages": False,
    "facing_start": "single",
    "gamma": 1.8,
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

# Display-gamma compensation for Kindle e-ink. Amazon's converters
# (Kindle Previewer / Send-to-Kindle) brighten image midtones at
# conversion time; 1.8 matches their output (see issue #4). 1.0 embeds
# original image bytes untouched.
GAMMA_CORRECTION = {
    1.0: "Off (keep original)",
    1.4: "1.4 (light)",
    1.8: "1.8 (match Kindle, recommended)",
    2.2: "2.2 (strong)",
}


def get_prefs():
    """Load plugin preferences with defaults."""
    from calibre.utils.config import JSONConfig
    prefs = JSONConfig("plugins/" + PREFS_NAMESPACE)
    prefs.defaults = DEFAULTS.copy()
    return prefs
