"""macOS capability report: all unimplemented seams are reported honestly."""

from __future__ import annotations

from keyless.platform.capabilities import CapabilityReport
from keyless.platform.stub import unsupported_capabilities


def detect() -> CapabilityReport:
    return unsupported_capabilities("macos")
