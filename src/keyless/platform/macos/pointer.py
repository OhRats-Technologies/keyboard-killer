"""macOS Pointer backend seam (stub in this build)."""

from keyless.platform.stub import UnsupportedPointer


class macOSPointerBackend(UnsupportedPointer):
    def __init__(self) -> None:
        super().__init__("macos")
