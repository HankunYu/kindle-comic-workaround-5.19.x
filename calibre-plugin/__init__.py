from calibre.customize import InterfaceActionBase


# Single source of truth for the plugin version. Both the class attribute
# below and updater.py read from this constant, so bumping the version
# here is enough — no other file needs to change.
__plugin_version__ = (1, 1, 5)


class KFXComicOutputPlugin(InterfaceActionBase):
    """
    Calibre plugin that adds a toolbar button for converting manga/comic
    files to KFX format optimized for Kindle e-readers.

    Pipeline: EPUB/MOBI → extract images → generate KPF → convert to KFX
    """

    name = "KFX Comic Output"
    description = "Convert manga/comic to KFX format optimized for Kindle"
    supported_platforms = ["osx", "windows", "linux"]
    author = "Hankun Yu"
    version = __plugin_version__
    minimum_calibre_version = (5, 0, 0)
    actual_plugin = "calibre_plugins.kfx_comic_output.ui:KFXComicAction"

    def is_customizable(self):
        return False
