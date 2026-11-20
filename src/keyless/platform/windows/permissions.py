"""Windows Permission service seam (stub in this build)."""

from keyless.platform.stub import UnsupportedPermissionService


class WindowsPermissionService(UnsupportedPermissionService):
    def __init__(self) -> None:
        super().__init__("windows")
