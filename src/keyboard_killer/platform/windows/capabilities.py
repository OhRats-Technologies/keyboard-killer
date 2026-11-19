"""Windows capability report: all unimplemented seams are reported honestly."""

from __future__ import annotations

from keyboard_killer.platform.capabilities import CapabilityReport
from keyboard_killer.platform.stub import unsupported_capabilities


def detect() -> CapabilityReport:
    return unsupported_capabilities("windows")
