"""macOS Hotkey source seam (stub in this build)."""

from keyless.platform.stub import UnsupportedHotkeySource


class macOSHotkeySource(UnsupportedHotkeySource):
    def __init__(self) -> None:
        super().__init__("macos")
