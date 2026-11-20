"""macOS tray seam (stub in this build)."""

from keyless.platform.stub import UnsupportedTray


class macOSTray(UnsupportedTray):
    def __init__(self) -> None:
        super().__init__("macos")
