from calibre.gui2.actions import InterfaceAction


class KFXComicAction(InterfaceAction):
    """
    Calibre interface action that adds a toolbar button for converting
    selected comic/manga books to KFX format.
    """

    name = "KFX Comic Output"
    action_spec = (
        "Convert Comics to KFX",  # text
        None,                      # icon
        "Convert selected manga/comic EPUB books to KFX format for Kindle",
        None,                      # keyboard shortcut
    )
    dont_add_to = frozenset()
    dont_remove_from = frozenset()
    action_type = "current"

    def genesis(self):
        """Called once when the plugin is loaded. Set up the action."""
        self.qaction.triggered.connect(self._convert_selected)

    def _convert_selected(self):
        """Entry point: convert all selected books."""
        from calibre_plugins.kfx_comic_output.jobs import start_conversion
        start_conversion(self.gui)

    def _show_config(self):
        """Show plugin configuration dialog."""
        self.interface_action_base_plugin.do_user_config(self.gui)

    def location_selected(self, loc):
        """Called when user switches between library views."""
        pass

    def shutting_down(self):
        """Called when calibre is shutting down."""
        pass
