"""Windows Hotkey source seam (stub in this build)."""

from keyboard_killer.platform.stub import UnsupportedHotkeySource


class WindowsHotkeySource(UnsupportedHotkeySource):
    def __init__(self) -> None:
        super().__init__("windows")
