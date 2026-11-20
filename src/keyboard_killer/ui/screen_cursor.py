"""Launch and stop the click-through screen cursor overlay."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

from keyboard_killer.config.paths import AppPaths, current_paths


@dataclass(frozen=True)
class CursorStatus:
    running: bool
    detail: str = ""
    error: str = ""


class ScreenCursor:
    """Own the overlay child process; the overlay itself draws and reads gaze."""

    def __init__(
        self,
        *,
        paths: AppPaths | None = None,
        force_demo: bool = False,
        python: str | None = None,
    ) -> None:
        self.paths = paths or current_paths()
        self.force_demo = force_demo
        self.python = python or sys.executable
        self._proc: subprocess.Popen[bytes] | None = None
        self._log: object | None = None
        self._error = ""

    @staticmethod
    def availability(env: dict[str, str] | None = None) -> tuple[bool, str]:
        environ = os.environ if env is None else env
        if sys.platform != "linux":
            return False, "The screen cursor overlay is currently Linux-only."
        if not environ.get("DISPLAY"):
            return False, "No X11/XWayland display; use the in-app gaze view."
        return True, ""

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def error(self) -> str:
        return self._error

    def start(self) -> CursorStatus:
        if self.running:
            return CursorStatus(True, "Screen cursor running.")
        ok, reason = self.availability()
        if not ok:
            self._error = reason
            return CursorStatus(False, "", reason)
        self.paths.ensure_directories()
        log_path = self.paths.logs_dir / "gaze-cursor.log"
        try:
            log = log_path.open("ab")
            self._log = log
            env = dict(os.environ)
            # XWayland lets the overlay position itself and stay click-through;
            # a native Wayland client cannot place a top-level window globally.
            env["QT_QPA_PLATFORM"] = "xcb"
            command = [
                self.python,
                "-m",
                "keyboard_killer.ui.gaze_overlay",
                "--parent-pid",
                str(os.getpid()),
            ]
            if self.force_demo:
                command.append("--demo")
            self._proc = subprocess.Popen(
                command,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            self._error = f"Could not start the screen cursor: {exc}"
            self._close_log()
            return CursorStatus(False, "", self._error)
        self._error = ""
        return CursorStatus(True, "Screen cursor running.")

    def stop(self) -> CursorStatus:
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._close_log()
        return CursorStatus(False, "Screen cursor stopped.")

    def refresh(self) -> CursorStatus:
        """Report an unexpected overlay exit without losing the reason."""
        if self._proc is not None:
            code = self._proc.poll()
            if code is not None:
                self._proc = None
                self._close_log()
                self._error = (
                    f"Screen cursor exited with code {code}. "
                    f"See {self.paths.logs_dir / 'gaze-cursor.log'}."
                )
        if self._error and not self.running:
            return CursorStatus(False, "", self._error)
        return CursorStatus(self.running, "Screen cursor running." if self.running else "")

    def _close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._log = None

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass


__all__ = ["CursorStatus", "ScreenCursor"]
