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

        self._update_direction_check()
        self.qaction.setMenu(self._menu)

    def _convert_selected(self):
        """Entry point: convert all selected books."""
        from calibre_plugins.kfx_comic_output.jobs import start_conversion
        start_conversion(self.gui)

    def _set_direction(self, direction):
        from calibre_plugins.kfx_comic_output.config import get_prefs
        prefs = get_prefs()
        prefs["reading_direction"] = direction
        self._update_direction_check()

    def _update_direction_check(self):
        from calibre_plugins.kfx_comic_output.config import get_prefs
        prefs = get_prefs()
        is_rtl = prefs["reading_direction"] == "rtl"
        self._rtl_action.setChecked(is_rtl)
        self._ltr_action.setChecked(not is_rtl)

    def location_selected(self, loc):
        pass

    def shutting_down(self):
        pass
