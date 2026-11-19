"""macOS Screen source seam (stub in this build)."""

from keyboard_killer.platform.stub import UnsupportedScreenSource


class macOSScreenSource(UnsupportedScreenSource):
    def __init__(self) -> None:
        super().__init__("macos")
