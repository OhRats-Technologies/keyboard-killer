"""Natural-language scroll command parser with Firefox DOM + Wayland uinput fallback."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ScrollCommand:
    """Parsed scroll direction, step size, or continuous auto-scroll action."""

    direction: str = "down"   # "down", "up", "left", "right", "top", "bottom"
    amount: str = "normal"    # "small" (25%), "normal" (58%), "large" (88%)
    continuous: str = ""      # "", "start", "stop", "faster", "slower"
    wheel_ticks: int = -6     # Fallback /dev/uinput REL_WHEEL ticks


def parse_voice_scroll_command(transcript: str) -> ScrollCommand | None:
    """Parse voice scroll commands into structured parameters."""
    cmd = transcript.lower().strip(" .,!?\"'")

    # 1. Continuous hands-free reading auto-scroll
    if cmd in ("stop scrolling", "stop scroll", "freeze scroll"):
        return ScrollCommand(continuous="stop", wheel_ticks=0)
    if cmd in ("scroll faster", "faster scroll"):
        return ScrollCommand(continuous="faster", wheel_ticks=0)
    if cmd in ("scroll slower", "slower scroll"):
        return ScrollCommand(continuous="slower", wheel_ticks=0)
    if cmd in (
        "auto scroll",
        "auto scroll down",
        "start scrolling",
        "keep scrolling",
        "keep scrolling down",
    ):
        return ScrollCommand(direction="down", continuous="start", wheel_ticks=0)
    if cmd in ("auto scroll up", "keep scrolling up"):
        return ScrollCommand(direction="up", continuous="start", wheel_ticks=0)

    # 2. Jump to top / bottom
    if cmd in ("scroll to top", "scroll top", "go to top", "top of page"):
        return ScrollCommand(direction="top", amount="large", wheel_ticks=25)
    if cmd in ("scroll to bottom", "scroll bottom", "go to bottom", "bottom of page"):
        return ScrollCommand(direction="bottom", amount="large", wheel_ticks=-25)

    # 3. Page Up / Page Down
    if cmd in ("page down", "next page"):
        return ScrollCommand(direction="down", amount="large", wheel_ticks=-11)
    if cmd in ("page up", "previous page"):
        return ScrollCommand(direction="up", amount="large", wheel_ticks=11)

    # 4. Directional scrolls with optional modifier ("scroll down [a little / a lot]")
    m = re.match(
        r"^scroll\s+(down|up|left|right)(?:\s+(a\s+little|a\s+bit|slightly|small|a\s+lot|much|fast|far|more))?$",
        cmd,
    )
    if m:
        direction = m.group(1)
        mod = (m.group(2) or "").strip()
        if mod in ("a little", "a bit", "slightly", "small"):
            amount = "small"
            ticks = 3
        elif mod in ("a lot", "much", "fast", "far", "more"):
            amount = "large"
            ticks = 11
        else:
            amount = "normal"
            ticks = 6

        sign = 1 if direction in ("up", "left") else -1
        return ScrollCommand(direction=direction, amount=amount, wheel_ticks=sign * ticks)

    return None
