"""macOS Autostart seam (stub in this build)."""

from keyless.platform.stub import UnsupportedAutostart


class macOSAutostart(UnsupportedAutostart):
    def __init__(self) -> None:
        super().__init__("macos")
