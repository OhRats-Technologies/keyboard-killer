"""Windows Camera source seam (stub in this build)."""

from keyless.platform.stub import UnsupportedCameraSource


class WindowsCameraSource(UnsupportedCameraSource):
    def __init__(self) -> None:
        super().__init__("windows")
