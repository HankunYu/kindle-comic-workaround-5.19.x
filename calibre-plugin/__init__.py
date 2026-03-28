from calibre.customize import InterfaceActionBase


PLUGIN_VERSION = (1, 0, 0)


class KFXComicOutputPlugin(InterfaceActionBase):
    """
    Calibre plugin that adds a toolbar button for converting manga/comic
    EPUB files to KFX format optimized for Kindle e-readers.

    Pipeline: EPUB -> extract images -> generate KPF -> convert to KFX
    """

    name = "KFX Comic Output"
    description = "Convert manga/comic EPUB to KFX format optimized for Kindle"
    supported_platforms = ["osx", "windows", "linux"]
    author = "kindle-comic"
    version = PLUGIN_VERSION
    minimum_calibre_version = (5, 0, 0)
    actual_plugin = "calibre_plugins.kfx_comic_output.ui:KFXComicAction"

    def is_customizable(self):
        return True

    def config_widget(self):
        from calibre_plugins.kfx_comic_output.config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
