from calibre.gui2.actions import InterfaceAction
from qt.core import QMenu

from calibre_plugins.kfx_comic_output.i18n import T


class KFXComicAction(InterfaceAction):
    """
    Calibre interface action that adds a toolbar button for converting
    selected comic/manga books to KFX format.
    """

    name = "KFX Comic Output"
    # action_spec keeps English source strings — they are overwritten with
    # localized text in genesis() via setText()/setToolTip(). This avoids
    # calling into calibre.utils.localization at class-definition time.
    action_spec = (
        "Convert Comics to KFX",  # text
        None,                      # icon — loaded from plugin zip in genesis()
        "Convert selected manga/comic books to KFX format for Kindle",
        None,                      # keyboard shortcut
    )
    dont_add_to = frozenset()
    dont_remove_from = frozenset()
    action_type = "current"

    def genesis(self):
        """Called once when the plugin is loaded. Set up the action."""
        # Load toolbar icon from the plugin zip. get_icons is injected by
        # Calibre's plugin loader and reads files out of the plugin's zip.
        icon = get_icons("images/icon.png")  # noqa: F821  (injected)
        if icon is not None and not icon.isNull():
            self.qaction.setIcon(icon)

        # Apply localized text/tooltip on top of the English action_spec.
        self.qaction.setText(T("Convert Comics to KFX"))
        self.qaction.setToolTip(
            T("Convert selected manga/comic books to KFX format for Kindle")
        )

        self.qaction.triggered.connect(self._convert_selected)

        self._menu = QMenu(self.gui)
        self._menu.addAction(T("Convert selected books"), self._convert_selected)
        self._menu.addSeparator()

        # Reading direction submenu
        self._dir_menu = self._menu.addMenu(T("Reading direction"))
        self._rtl_action = self._dir_menu.addAction(T("Right to Left (manga)"))
        self._rtl_action.setCheckable(True)
        self._rtl_action.triggered.connect(lambda: self._set_direction("rtl"))
        self._ltr_action = self._dir_menu.addAction(T("Left to Right (comic)"))
        self._ltr_action.setCheckable(True)
        self._ltr_action.triggered.connect(lambda: self._set_direction("ltr"))

        # Virtual panels submenu
        from calibre_plugins.kfx_comic_output.config import VIRTUAL_PANELS
        self._vp_menu = self._menu.addMenu(T("Virtual panels"))
        self._vp_actions = {}
        for key, label in VIRTUAL_PANELS.items():
            action = self._vp_menu.addAction(T(label))
            action.setCheckable(True)
            action.triggered.connect(lambda checked, k=key: self._set_virtual_panels(k))
            self._vp_actions[key] = action

        # Facing pages toggle
        self._facing_action = self._menu.addAction(T("Facing pages (spreads)"))
        self._facing_action.setCheckable(True)
        self._facing_action.triggered.connect(self._toggle_facing_pages)

        # Facing-pages start mode submenu (only meaningful when facing pages on)
        from calibre_plugins.kfx_comic_output.config import FACING_START
        self._fs_menu = self._menu.addMenu(T("Facing pages start"))
        self._fs_actions = {}
        for key, label in FACING_START.items():
            action = self._fs_menu.addAction(T(label))
            action.setCheckable(True)
            action.triggered.connect(lambda checked, k=key: self._set_facing_start(k))
            self._fs_actions[key] = action

        # Language submenu
        from calibre_plugins.kfx_comic_output.config import LANGUAGES
        self._lang_menu = self._menu.addMenu(T("Language"))
        self._lang_actions = {}
        for key, label in LANGUAGES.items():
            action = self._lang_menu.addAction(T(label))
            action.setCheckable(True)
            action.triggered.connect(lambda checked, k=key: self._set_language(k))
            self._lang_actions[key] = action

        # Check for updates
        self._menu.addSeparator()
        self._update_check_action = self._menu.addAction(T("Check for updates"))
        self._update_check_action.triggered.connect(self._check_for_updates)

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

    def _set_facing_start(self, mode):
        from calibre_plugins.kfx_comic_output.config import get_prefs
        prefs = get_prefs()
        prefs["facing_start"] = mode
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
        current_fs = prefs.get("facing_start", "single")
        for key, action in self._fs_actions.items():
            action.setChecked(key == current_fs)
        current_lang = prefs["language"]
        for key, action in self._lang_actions.items():
            action.setChecked(key == current_lang)

    def _check_for_updates(self):
        """Query GitHub for the latest release and report to the user."""
        from calibre.gui2 import info_dialog, error_dialog, question_dialog
        from qt.core import QApplication, Qt, QUrl, QDesktopServices
        from calibre_plugins.kfx_comic_output.updater import (
            fetch_latest_release, compare_with_current, format_version,
            RELEASES_PAGE,
        )
        from calibre_plugins.kfx_comic_output import __plugin_version__

        # Synchronous call with a short timeout — UI blocks briefly, but the
        # wait cursor gives immediate feedback and 5s is acceptable for a
        # manually-triggered check.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                info = fetch_latest_release(timeout=5)
            finally:
                QApplication.restoreOverrideCursor()
        except Exception as e:
            error_dialog(
                self.gui,
                T("Update check failed"),
                T("Could not check for updates: {error}").format(error=e),
                det_msg=T("Releases page: {url}").format(url=RELEASES_PAGE),
                show=True,
            )
            return

        current_str = format_version(__plugin_version__)
        latest_str = format_version(info["version"])
        status = compare_with_current(info["version"])

        if status == "newer":
            open_it = question_dialog(
                self.gui,
                T("Update available"),
                T(
                    "A new version {new} is available "
                    "(currently installed: {cur}).\n\nOpen the release page?"
                ).format(new=latest_str, cur=current_str),
                det_msg=info.get("body", "") or "",
            )
            if open_it:
                QDesktopServices.openUrl(QUrl(info["html_url"]))
        else:
            # 'same' or 'older' (e.g. user running a local dev build) both
            # count as "no update needed" from the user's perspective.
            info_dialog(
                self.gui,
                T("No updates"),
                T("You are already running the latest version ({cur}).").format(
                    cur=current_str
                ),
                show=True,
            )

    def location_selected(self, loc):
        pass

    def shutting_down(self):
        pass
