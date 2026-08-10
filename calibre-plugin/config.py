# Plugin preferences stored via Calibre's JSONConfig

PREFS_NAMESPACE = "KFXComicOutputPlugin"

DEFAULTS = {
    "reading_direction": "rtl",
    "language": "zh",
    "virtual_panels": "off",
    "facing_pages": False,
    "facing_start": "single",
    "gamma": 1.0,
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

# Display-gamma compensation for Kindle e-ink comics. The Kindle
# firmware renders fixed-layout comics through a darker tone path than
# reflowable-book images (measured in issue #4; Amazon's converters do
# NOT alter pixels). 1.8 brightens midtones to compensate; 1.0 (default)
# embeds original image bytes untouched.
GAMMA_CORRECTION = {
    1.0: "Off (keep original, default)",
    1.4: "1.4 (light)",
    1.8: "1.8 (brighten, match reflowable look)",
    2.2: "2.2 (strong)",
}


def get_prefs():
    """Load plugin preferences with defaults."""
    from calibre.utils.config import JSONConfig
    prefs = JSONConfig("plugins/" + PREFS_NAMESPACE)
    prefs.defaults = DEFAULTS.copy()
    return prefs
