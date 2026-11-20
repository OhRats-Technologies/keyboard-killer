"""macOS Permission service seam (stub in this build)."""

from keyless.platform.stub import UnsupportedPermissionService


class macOSPermissionService(UnsupportedPermissionService):
    def __init__(self) -> None:
        super().__init__("macos")
