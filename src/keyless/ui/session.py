"""UI session controller: starts/stops the existing daemon without rewriting it."""

from __future__ import annotations

import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from keyless.config.paths import AppPaths, current_paths


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


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass(frozen=True)
class DaemonFailureReport:
    """User-friendly explanation and stripped log excerpt for a daemon failure."""

    summary: str
    hint: str
    code: int
    log_path: Path
    log_excerpt: str = ""

    def formatted_clipboard_text(self) -> str:
        parts = [f"Keyless Error: {self.summary}", f"Suggested Fix: {self.hint}", f"Exit Code: {self.code}", f"Log File: {self.log_path}"]
        if self.log_excerpt:
            parts.append("\n--- Recent Log ---\n" + self.log_excerpt)
        return "\n".join(parts)


def inspect_daemon_error(log_path: Path, code: int = 1) -> DaemonFailureReport:
    """Build a human-friendly failure summary and clean log excerpt from ``daemon.log``."""
    try:
        raw_lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        raw_lines = []
    clean_lines = [_ANSI_RE.sub("", line).rstrip() for line in raw_lines]
    non_empty = [line for line in clean_lines if line.strip()]
    tail_lines = non_empty[-35:]
    excerpt = "\n".join(tail_lines)
    tail_text = "\n".join(tail_lines)

    if "Failed to open V4L2 capture" in tail_text or "Camera is unavailable or busy" in tail_text:
        return DaemonFailureReport(
            summary="Camera is busy",
            hint="Close any open calibration or video call using the webcam, or turn on 'Share camera' in Settings.",
            code=code,
            log_path=log_path,
            log_excerpt=excerpt,
        )
    if "Camera symlink not found" in tail_text:
        return DaemonFailureReport(
            summary="Configured camera wasn't found",
            hint="Check the USB connection and pick your active camera in Settings.",
            code=code,
            log_path=log_path,
            log_excerpt=excerpt,
        )
    if "SttModelMissing" in tail_text or ("parakeet" in tail_text.lower() and "missing" in tail_text.lower()):
        return DaemonFailureReport(
            summary="Speech model isn't downloaded yet",
            hint="Open Settings and download the offline speech model.",
            code=code,
            log_path=log_path,
            log_excerpt=excerpt,
        )
    if "PortAudio" in tail_text or "Error opening InputStream" in tail_text:
        return DaemonFailureReport(
            summary="Microphone couldn't be opened",
            hint="Choose your microphone in Settings and run Check mic.",
            code=code,
            log_path=log_path,
            log_excerpt=excerpt,
        )
    if "PermissionError" in tail_text and "uinput" in tail_text.lower():
        return DaemonFailureReport(
            summary="Input permission is required",
            hint="Add your user to the input group or install the udev rule via Settings → Diagnostics.",
            code=code,
            log_path=log_path,
            log_excerpt=excerpt,
        )
    for line in reversed(tail_lines):
        for prefix in ("RuntimeError:", "FileNotFoundError:", "OSError:", "ValueError:"):
            if prefix in line:
                detail = line.split(prefix, 1)[1].strip()
                if detail:
                    return DaemonFailureReport(
                        summary=detail,
                        hint="View recent logs below or copy the error report for troubleshooting.",
                        code=code,
                        log_path=log_path,
                        log_excerpt=excerpt,
                    )
    return DaemonFailureReport(
        summary=f"Background service stopped (code {code})",
        hint="Inspect the recent logs or copy the diagnostic report.",
        code=code,
        log_path=log_path,
        log_excerpt=excerpt,
    )


def _extract_daemon_error(log_path: Path, code: int) -> str:
    """Summarize the underlying daemon error cleanly for end users."""
    rep = inspect_daemon_error(log_path, code)
    if rep.summary == "Camera is busy":
        return "Camera is currently in use by another app or calibration. Close it or enable 'Share camera' in Settings."
    return f"{rep.summary}. {rep.hint}"


class DaemonController:
    """Spawn ``python -m keyless.main run`` as a child process.

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
        self._last_failure: DaemonFailureReport | None = None

    @property
    def failure_report(self) -> DaemonFailureReport | None:
        return self._last_failure

    @property
    def status(self) -> SessionStatus:
        if self._proc is not None and self._proc.poll() is not None:
            code = int(self._proc.returncode or 1)
            self._proc = None
            self._close_log()
            report = inspect_daemon_error(self.paths.logs_dir / "daemon.log", code)
            self._last_failure = report
            self._status = SessionStatus(
                SessionState.ERROR,
                f"{report.summary}. {report.hint}",
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
            from keyless.config.schema import AppConfig

            backend = AppConfig.load(self.paths.settings_path).camera_backend
            if backend == "pipewire" and "--camera-backend" not in args:
                args.extend(["--camera-backend", "pipewire"])
        except Exception:
            pass
        try:
            log = log_path.open("ab")
            self._log = log
            self._proc = subprocess.Popen(
                [self.python, "-m", "keyless.main", *args],
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
