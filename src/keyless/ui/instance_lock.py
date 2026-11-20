"""Single-instance activation lock modeled on t3code's DesktopAppActivation."""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from keyless.config.paths import AppPaths, current_paths


def _socket_path(paths: AppPaths) -> Path:
    return paths.data_dir / "keyless-ui.sock"


def _ensure_private_dir(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "posix":
        return
    st = os.lstat(directory)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise OSError(f"{directory} is not a real directory.")
    if st.st_uid != os.getuid():
        raise OSError(f"{directory} is owned by another user.")
    os.chmod(directory, 0o700)


def try_activate_existing(paths: AppPaths | None = None, *, timeout: float = 0.6) -> bool:
    """Signal a running Keyless UI instance to raise its window; return True if active."""
    resolved = paths or current_paths()
    sock_path = _socket_path(resolved)
    if not sock_path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(sock_path))
            client.sendall(b'{"action":"activate"}\n')
            data = client.recv(1024)
            payload = json.loads(data.decode("utf-8", errors="replace").splitlines()[0])
            return bool(payload.get("ok"))
    except (OSError, json.JSONDecodeError, IndexError):
        return False


class SingleInstanceServer(QObject):
    """Listen on a private UNIX socket and emit ``activate_requested`` on second launch."""

    activate_requested = Signal()

    def __init__(self, paths: AppPaths | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.paths = paths or current_paths()
        self._sock_path = _socket_path(self.paths)
        self._server: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        _ensure_private_dir(self._sock_path.parent)
        if self._sock_path.exists():
            # Clean up stale socket left by an ungraceful exit.
            self._sock_path.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self._sock_path))
        if os.name == "posix":
            os.chmod(self._sock_path, 0o600)
        srv.listen(4)
        srv.settimeout(0.25)
        self._server = srv
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._accept_loop,
            name="keyless-instance-lock",
            daemon=True,
        )
        self._thread.start()
        return True

    def _accept_loop(self) -> None:
        while not self._stop.is_set() and self._server is not None:
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    conn.settimeout(0.5)
                    raw = conn.recv(2048).decode("utf-8", errors="replace").strip()
                    req = json.loads(raw) if raw else {}
                    if req.get("action") == "activate":
                        self.activate_requested.emit()
                        conn.sendall(b'{"ok":true}\n')
                    else:
                        conn.sendall(b'{"ok":false}\n')
                except Exception:
                    pass

    def close(self) -> None:
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None
        self._sock_path.unlink(missing_ok=True)
