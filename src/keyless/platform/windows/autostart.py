"""Windows Autostart seam (stub in this build)."""

from keyless.platform.stub import UnsupportedAutostart


class WindowsAutostart(UnsupportedAutostart):
    def __init__(self) -> None:
        super().__init__("windows")
