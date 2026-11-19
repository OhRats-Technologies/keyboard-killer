"""XDG autostart entry for the Linux UI."""

from __future__ import annotations

from pathlib import Path

from keyboard_killer.config.paths import AppPaths, current_paths

DESKTOP = """[Desktop Entry]
Type=Application
Name=Keyboard-Killer
Comment=Start Keyboard-Killer in the tray
Exec=/usr/bin/keyboard-killer ui
Icon=keyboard-killer
Terminal=false
X-GNOME-Autostart-enabled=true
"""


class XdgAutostart:
    def __init__(self, paths: AppPaths | None = None, command: str = "/usr/bin/keyboard-killer ui") -> None:
        self.paths = paths or current_paths()
        self.command = command
        self.path = self.paths.autostart_path

    def enabled(self) -> bool:
        return self.path.exists()

    def enable(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = DESKTOP.replace("/usr/bin/keyboard-killer ui", self.command)
        self.path.write_text(content)

    def disable(self) -> None:
        self.path.unlink(missing_ok=True)
