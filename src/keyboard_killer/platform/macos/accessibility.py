"""macOS Accessibility bridge seam (stub in this build)."""

from keyboard_killer.platform.stub import UnsupportedAccessibilityBridge


class macOSAccessibilityBridge(UnsupportedAccessibilityBridge):
    def __init__(self) -> None:
        super().__init__("macos")
