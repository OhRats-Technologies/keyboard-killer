"""Windows Pointer backend seam (stub in this build)."""

from keyboard_killer.platform.stub import UnsupportedPointer


class WindowsPointerBackend(UnsupportedPointer):
    def __init__(self) -> None:
        super().__init__("windows")
