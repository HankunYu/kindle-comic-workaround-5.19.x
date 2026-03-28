from calibre.customize import InterfaceActionBase


class KFXComicOutputPlugin(InterfaceActionBase):
    """
    Calibre plugin that adds a toolbar button for converting manga/comic
    files to KFX format optimized for Kindle e-readers.

    Pipeline: EPUB/MOBI → extract images → generate KPF → convert to KFX
    """

    name = "KFX Comic Output"
    description = "Convert manga/comic to KFX format optimized for Kindle"
    supported_platforms = ["osx", "windows", "linux"]
    author = "kindle-comic"
    version = (1, 1, 0)
    minimum_calibre_version = (5, 0, 0)
    actual_plugin = "calibre_plugins.kfx_comic_output.ui:KFXComicAction"

    def is_customizable(self):
        return False
