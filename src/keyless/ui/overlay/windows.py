"""Windows overlay seam (stub in this Linux-first build)."""

from keyless.ui.overlay.base import NullOverlay


def create_overlay(*_args, **_kwargs) -> NullOverlay:
    return NullOverlay("Windows overlay is not implemented in this build.")
