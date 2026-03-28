from qt.core import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QGroupBox

# Plugin preferences stored via Calibre's JSONConfig
PREFS_NAMESPACE = "KFXComicOutputPlugin"

# Default settings
DEFAULTS = {
    "reading_direction": "rtl",
    "language": "ja",
}

# Supported device targets (future expansion)
READING_DIRECTIONS = {
    "rtl": "Right to Left (manga)",
    "ltr": "Left to Right (comic)",
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


class ConfigWidget(QWidget):
    """Configuration dialog for KFX Comic Output plugin."""

    def __init__(self):
        super().__init__()
        self._prefs = get_prefs()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Conversion settings group
        group = QGroupBox("Conversion Settings")
        group_layout = QVBoxLayout(group)

        # Reading direction
        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Reading direction:"))
        self._direction_combo = QComboBox()
        for key, label in READING_DIRECTIONS.items():
            self._direction_combo.addItem(label, key)
        current_dir = self._prefs["reading_direction"]
        idx = self._direction_combo.findData(current_dir)
        if idx >= 0:
            self._direction_combo.setCurrentIndex(idx)
        dir_layout.addWidget(self._direction_combo)
        group_layout.addLayout(dir_layout)

        # Language
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(QLabel("Language:"))
        self._language_combo = QComboBox()
        for key, label in LANGUAGES.items():
            self._language_combo.addItem(label, key)
        current_lang = self._prefs["language"]
        idx = self._language_combo.findData(current_lang)
        if idx >= 0:
            self._language_combo.setCurrentIndex(idx)
        lang_layout.addWidget(self._language_combo)
        group_layout.addLayout(lang_layout)

        layout.addWidget(group)
        layout.addStretch()

    def save_settings(self):
        """Persist current widget values to Calibre preferences."""
        self._prefs["reading_direction"] = self._direction_combo.currentData()
        self._prefs["language"] = self._language_combo.currentData()
