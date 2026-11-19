"""macOS Camera source seam (stub in this build)."""

from keyboard_killer.platform.stub import UnsupportedCameraSource


class macOSCameraSource(UnsupportedCameraSource):
    def __init__(self) -> None:
        super().__init__("macos")
