"""UI session controller: starts/stops the existing daemon without rewriting it."""

from __future__ import annotations

import signal
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from keyboard_killer.config.paths import AppPaths, current_paths


class SessionState(str, Enum):
    STOPPED = "stopped"
    LISTENING = "listening"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class SessionStatus:
    state: SessionState = SessionState.STOPPED
    detail: str = ""


class SessionController(Protocol):
    @property
    def status(self) -> SessionStatus: ...

    def start(self) -> SessionStatus: ...

    def pause(self) -> SessionStatus: ...

    def quit(self) -> None: ...


class NullController:
    """Used by tests and screenshots; records requested transitions."""

    def __init__(self) -> None:
        self._status = SessionStatus()
        self.history: list[str] = []

    @property
    def status(self) -> SessionStatus:
        return self._status

    def start(self) -> SessionStatus:
        self.history.append("start")
        self._status = SessionStatus(SessionState.LISTENING, "Dry run; no daemon process.")
        return self._status

    def pause(self) -> SessionStatus:
        self.history.append("pause")
        self._status = SessionStatus(SessionState.PAUSED, "Paused.")
        return self._status

    def quit(self) -> None:
        self.history.append("quit")
        self._status = SessionStatus(SessionState.STOPPED, "Stopped.")


class DaemonController:
    """Spawn ``python -m keyboard_killer.main run`` as a child process.

    Pause stops the child (the daemon has no pause IPC yet) and resume starts it
    again.  This keeps the engine untouched and the CLI as the fallback.
    """

    def __init__(
        self,
        *,
        paths: AppPaths | None = None,
        extra_args: list[str] | None = None,
        python: str | None = None,
    ) -> None:
        self.paths = paths or current_paths()
        self.extra_args = list(extra_args or ["run"])
        self.python = python or sys.executable
        self._proc: subprocess.Popen[bytes] | None = None
        self._log: object | None = None
        self._status = SessionStatus()

    @property
    def status(self) -> SessionStatus:
        if self._proc is not None and self._proc.poll() is not None:
            code = self._proc.returncode
            self._proc = None
            self._close_log()
            self._status = SessionStatus(
                SessionState.ERROR,
                f"Daemon exited with code {code}. See {self.paths.logs_dir / 'daemon.log'}.",
            )
        return self._status

    def start(self) -> SessionStatus:
        if not self.paths.calibration_path.exists():
            self._status = SessionStatus(
                SessionState.ERROR,
                f"Calibration is missing. Run setup first ({self.paths.calibration_path}).",
            )
            return self._status
        if self._proc is not None and self._proc.poll() is None:
            self._status = SessionStatus(SessionState.LISTENING, "Already running.")
            return self._status
        self.paths.ensure_directories()
        log_path = self.paths.logs_dir / "daemon.log"
        args = list(self.extra_args)
        try:
            from keyboard_killer.config.schema import AppConfig

            backend = AppConfig.load(self.paths.settings_path).camera_backend
            if backend == "pipewire" and "--camera-backend" not in args:
                args.extend(["--camera-backend", "pipewire"])
        except Exception:
            pass
        try:
            log = log_path.open("ab")
            self._log = log
            self._proc = subprocess.Popen(
                [self.python, "-m", "keyboard_killer.main", *args],
                cwd=str(Path(__file__).resolve().parents[3]),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            self._close_log()
            self._status = SessionStatus(SessionState.ERROR, f"Could not start: {exc}")
            return self._status
        self._status = SessionStatus(SessionState.LISTENING, "Listening.")
        return self._status

    def pause(self) -> SessionStatus:
        if self._proc is not None and self._proc.poll() is None:
            # SIGINT lets the CLI unwind its context managers and release the
            # camera and uinput device; SIGTERM skipped that cleanup.
            self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=4.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2.0)
        self._proc = None
        self._close_log()
        self._status = SessionStatus(SessionState.PAUSED, "Paused (daemon stopped).")
        return self._status

    def _close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._log = None

    def quit(self) -> None:
        self.pause()
