"""Linux permission rows derived from the same probes as `doctor`."""

from __future__ import annotations

from keyless.platform.base import PermissionInfo, PermissionState
from keyless.platform.capabilities import (
    probe_audio,
    probe_hotkeys,
    probe_pointer,
    probe_screen,
)


class LinuxPermissionService:
    def check(self, need: str) -> PermissionInfo:
        name = need.lower()
        if name in ("input_injection", "uinput", "pointer"):
            info = probe_pointer()
        elif name in ("hotkey", "input"):
            info = probe_hotkeys()
        elif name in ("microphone", "audio"):
            info = probe_audio()
        elif name in ("screen_capture", "screen"):
            info = probe_screen()
        else:
            return PermissionInfo(
                name=need,
                state=PermissionState.UNKNOWN,
                detail=f"Unknown permission: {need}",
                action="See `keyless doctor`.",
            )
        return PermissionInfo(
            name=need,
            state=info.permission or (
                PermissionState.GRANTED
                if info.status.value == "available"
                else PermissionState.UNKNOWN
            ),
            detail=info.detail,
            action=info.action,
        )
