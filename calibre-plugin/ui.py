from calibre.gui2.actions import InterfaceAction
from qt.core import QMenu


class KFXComicAction(InterfaceAction):
    """
    Calibre interface action that adds a toolbar button for converting
    selected comic/manga books to KFX format.
    """

    name = "KFX Comic Output"
    action_spec = (
        "Convert Comics to KFX",  # text
        None,                      # icon
        "Convert selected manga/comic books to KFX format for Kindle",
        None,                      # keyboard shortcut
    )
    dont_add_to = frozenset()
    dont_remove_from = frozenset()
    action_type = "current"

    def genesis(self):
        """Called once when the plugin is loaded. Set up the action."""
        self.qaction.triggered.connect(self._convert_selected)

        self._menu = QMenu(self.gui)
        self._menu.addAction("Convert selected books", self._convert_selected)
        self._menu.addSeparator()

        # Reading direction submenu
        self._dir_menu = self._menu.addMenu("Reading direction")
        self._rtl_action = self._dir_menu.addAction("Right to Left (manga)")
        self._rtl_action.setCheckable(True)
        self._rtl_action.triggered.connect(lambda: self._set_direction("rtl"))
        self._ltr_action = self._dir_menu.addAction("Left to Right (comic)")
        self._ltr_action.setCheckable(True)
        self._ltr_action.triggered.connect(lambda: self._set_direction("ltr"))

        # Virtual panels submenu
        from calibre_plugins.kfx_comic_output.config import VIRTUAL_PANELS
        self._vp_menu = self._menu.addMenu("Virtual panels")
        self._vp_actions = {}
        for key, label in VIRTUAL_PANELS.items():
            action = self._vp_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, k=key: self._set_virtual_panels(k))
            self._vp_actions[key] = action

        # Facing pages toggle
        self._facing_action = self._menu.addAction("Facing pages (spreads)")
        self._facing_action.setCheckable(True)
        self._facing_action.triggered.connect(self._toggle_facing_pages)

        # Language submenu
        from calibre_plugins.kfx_comic_output.config import LANGUAGES
        self._lang_menu = self._menu.addMenu("Language")
        self._lang_actions = {}
        for key, label in LANGUAGES.items():
            action = self._lang_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, k=key: self._set_language(k))
            self._lang_actions[key] = action

        self._update_checks()
        self.qaction.setMenu(self._menu)

    def _convert_selected(self):
        """Entry point: convert all selected books."""
        from calibre_plugins.kfx_comic_output.jobs import start_conversion
        start_conversion(self.gui)

    def _set_direction(self, direction):
        from calibre_plugins.kfx_comic_output.config import get_prefs
        prefs = get_prefs()
        prefs["reading_direction"] = direction
        prefs.commit()
        self._update_checks()

    def _toggle_facing_pages(self):
        from calibre_plugins.kfx_comic_output.config import get_prefs
        prefs = get_prefs()
        prefs["facing_pages"] = not prefs.get("facing_pages", False)
        prefs.commit()
        self._update_checks()

    def _set_virtual_panels(self, mode):
        from calibre_plugins.kfx_comic_output.config import get_prefs
        prefs = get_prefs()
        prefs["virtual_panels"] = mode
        prefs.commit()
        self._update_checks()

    def _set_language(self, language):
        from calibre_plugins.kfx_comic_output.config import get_prefs
        prefs = get_prefs()
        prefs["language"] = language
        prefs.commit()
        self._update_checks()

    def _update_checks(self):
        from calibre_plugins.kfx_comic_output.config import get_prefs
        prefs = get_prefs()
        is_rtl = prefs["reading_direction"] == "rtl"
        self._rtl_action.setChecked(is_rtl)
        self._ltr_action.setChecked(not is_rtl)
        self._facing_action.setChecked(prefs.get("facing_pages", False))
        current_vp = prefs["virtual_panels"]
        for key, action in self._vp_actions.items():
            action.setChecked(key == current_vp)
        current_lang = prefs["language"]
        for key, action in self._lang_actions.items():
            action.setChecked(key == current_lang)

    def location_selected(self, loc):
        pass

    def shutting_down(self):
        pass
