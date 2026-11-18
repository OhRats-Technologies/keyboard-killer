"""AT-SPI2 Linux desktop accessibility bridge for non-browser apps and OS popups.

Supports direct ``gi.repository.Atspi`` when PyGObject is installed in the
active Python environment, and automatically falls back to a persistent
``/usr/bin/python3`` JSON-line worker when running inside an isolated venv
(e.g. ``uv``) that lacks system ``gi`` bindings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import json
import logging
import os
import re
import subprocess
import sys
import threading
from typing import Any

try:
    from rapidfuzz import fuzz as _rf_fuzz
except ImportError:  # pragma: no cover - optional fallback
    _rf_fuzz = None

logger = logging.getLogger(__name__)

_DESKTOP_STOP_WORDS = {
    "the",
    "a",
    "an",
    "on",
    "in",
    "into",
    "to",
    "at",
    "for",
    "of",
    "button",
    "btn",
    "icon",
    "tab",
    "menu",
    "item",
    "box",
    "field",
    "bar",
    "window",
    "app",
    "click",
    "press",
    "tap",
    "select",
    "open",
}

_NUMBER_WORD_MAP = {
    "one": "1",
    "first": "1",
    "1st": "1",
    "two": "2",
    "second": "2",
    "2nd": "2",
    "three": "3",
    "third": "3",
    "3rd": "3",
    "four": "4",
    "fourth": "4",
    "4th": "4",
    "five": "5",
    "fifth": "5",
    "5th": "5",
    "six": "6",
    "sixth": "6",
    "6th": "6",
    "seven": "7",
    "seventh": "7",
    "7th": "7",
    "eight": "8",
    "eighth": "8",
    "8th": "8",
    "nine": "9",
    "ninth": "9",
    "9th": "9",
    "ten": "10",
    "tenth": "10",
    "10th": "10",
    "eleven": "11",
    "eleventh": "11",
    "11th": "11",
    "twelve": "12",
    "twelfth": "12",
    "12th": "12",
}

_ORDINAL_WORDS = (
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
)

_INTERACTIVE_ROLES = {
    "push button",
    "toggle button",
    "check box",
    "radio button",
    "menu item",
    "check menu item",
    "radio menu item",
    "menu",
    "page tab",
    "combo box",
    "text",
    "entry",
    "password text",
    "spin button",
    "link",
    "list item",
    "tree item",
    "icon",
    "slider",
}

_EDITABLE_ROLES = {
    "text",
    "entry",
    "password text",
    "combo box",
    "spin button",
}

_LIST_OR_MENU_ROLES = {
    "list item",
    "tree item",
    "menu item",
    "check menu item",
    "radio menu item",
    "icon",
}


@dataclass
class DesktopAccessibleNode:
    """Interactive widget discovered via AT-SPI2."""

    node_id: str
    role: str
    name: str
    description: str
    cx: int
    cy: int
    w: int
    h: int
    is_editable: bool = False
    is_clickable: bool = True
    aliases: list[str] = field(default_factory=list)
    ordinal: int = 0


def _fuzzy_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if _rf_fuzz is not None:
        return float(max(_rf_fuzz.token_set_ratio(a, b), _rf_fuzz.partial_ratio(a, b)))
    return difflib.SequenceMatcher(None, a, b).ratio() * 100.0


def _normalize_query_tokens(query: str) -> list[str]:
    tokens = [
        _NUMBER_WORD_MAP.get(t, t)
        for t in re.split(r"\W+", query.lower())
        if t and t not in _DESKTOP_STOP_WORDS
    ]
    return tokens


class _AtspiDirectEngine:
    """In-process AT-SPI2 engine using ``gi.repository.Atspi``."""

    def __init__(self) -> None:
        import gi  # type: ignore

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # type: ignore

        self._Atspi = Atspi
        Atspi.init()
        self._node_refs: dict[str, Any] = {}

    def _get_active_app_and_window(self) -> tuple[Any | None, Any | None, str, str]:
        Atspi = self._Atspi
        desktop = Atspi.get_desktop(0)
        if desktop is None:
            return None, None, "", ""

        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            app_name = app.get_name() or ""
            for j in range(app.get_child_count()):
                win = app.get_child_at_index(j)
                if win is None:
                    continue
                try:
                    states = win.get_state_set()
                    if states is not None and states.contains(Atspi.StateType.ACTIVE):
                        return app, win, app_name, (win.get_name() or "")
                except Exception:
                    continue
        return None, None, "", ""

    def get_active_window_info(self) -> dict[str, str]:
        _, _, app_name, win_title = self._get_active_app_and_window()
        return {"app_name": app_name, "window_title": win_title}

    def scan_active_window(self, max_nodes: int = 160, max_depth: int = 14) -> list[dict[str, Any]]:
        Atspi = self._Atspi
        app, active_win, _, _ = self._get_active_app_and_window()
        if app is None or active_win is None:
            self._node_refs.clear()
            return []

        roots = [active_win]
        # Also include showing popups/menus/dialogs belonging to the active app.
        for j in range(app.get_child_count()):
            sibling = app.get_child_at_index(j)
            if sibling is None or sibling == active_win:
                continue
            try:
                states = sibling.get_state_set()
                if states is not None and states.contains(Atspi.StateType.SHOWING):
                    roots.append(sibling)
            except Exception:
                continue

        self._node_refs.clear()
        nodes: list[dict[str, Any]] = []
        ordinal_counter = 0
        seq = 0

        def walk(acc: Any, depth: int) -> None:
            nonlocal ordinal_counter, seq
            if len(nodes) >= max_nodes or depth > max_depth or acc is None:
                return

            try:
                states = acc.get_state_set()
                if states is not None:
                    if not states.contains(Atspi.StateType.VISIBLE) or not states.contains(
                        Atspi.StateType.SHOWING
                    ):
                        return
            except Exception:
                return

            role_name = ""
            try:
                role_name = (acc.get_role_name() or "").lower()
            except Exception:
                pass

            name = ""
            description = ""
            try:
                name = (acc.get_name() or "").strip()
            except Exception:
                pass
            try:
                description = (acc.get_description() or "").strip()
            except Exception:
                pass

            is_editable = role_name in _EDITABLE_ROLES
            if states is not None and states.contains(Atspi.StateType.EDITABLE):
                is_editable = True

            has_action = False
            try:
                action_iface = acc.get_action_iface()
                has_action = action_iface is not None and action_iface.get_n_actions() > 0
            except Exception:
                pass

            if role_name in _INTERACTIVE_ROLES or has_action or is_editable:
                try:
                    comp = acc.get_component_iface()
                    if comp is not None:
                        rect = comp.get_extents(Atspi.CoordType.SCREEN)
                        w, h = int(rect.width), int(rect.height)
                        if w > 4 and h > 4 and (name or description or is_editable):
                            cx = int(rect.x + w // 2)
                            cy = int(rect.y + h // 2)
                            seq += 1
                            node_id = f"atspi_{seq}"
                            aliases: list[str] = []
                            if name:
                                aliases.append(name.lower())
                            if description and description.lower() != name.lower():
                                aliases.append(description.lower())

                            ordinal = 0
                            if role_name in _LIST_OR_MENU_ROLES and ordinal_counter < 12:
                                ordinal_counter += 1
                                ordinal = ordinal_counter
                                ord_word = _ORDINAL_WORDS[ordinal - 1]
                                aliases.extend(
                                    [
                                        f"result {ordinal}",
                                        f"{ord_word} result",
                                        f"option {ordinal}",
                                        f"{ord_word} option",
                                        f"item {ordinal}",
                                        f"{ord_word} item",
                                        str(ordinal),
                                    ]
                                )

                            self._node_refs[node_id] = acc
                            nodes.append(
                                {
                                    "node_id": node_id,
                                    "role": role_name,
                                    "name": name,
                                    "description": description,
                                    "cx": cx,
                                    "cy": cy,
                                    "w": w,
                                    "h": h,
                                    "is_editable": is_editable,
                                    "is_clickable": bool(has_action or role_name in _INTERACTIVE_ROLES),
                                    "aliases": aliases,
                                    "ordinal": ordinal,
                                }
                            )
                except Exception:
                    pass

            try:
                child_count = min(acc.get_child_count(), 120)
                for idx in range(child_count):
                    if len(nodes) >= max_nodes:
                        break
                    walk(acc.get_child_at_index(idx), depth + 1)
            except Exception:
                pass

        for root in roots:
            walk(root, 0)
            if len(nodes) >= max_nodes:
                break

        return nodes

    def invoke(self, node_id: str, action: str = "click") -> dict[str, Any]:
        acc = self._node_refs.get(node_id)
        if acc is None:
            return {"ok": False, "invoked": False}

        if action == "type":
            try:
                comp = acc.get_component_iface()
                if comp is not None:
                    comp.grab_focus()
                    return {"ok": True, "invoked": False}
            except Exception:
                pass
            return {"ok": True, "invoked": False}

        try:
            action_iface = acc.get_action_iface()
            if action_iface is not None and action_iface.get_n_actions() > 0:
                if action_iface.do_action(0):
                    return {"ok": True, "invoked": True}
        except Exception:
            pass

        try:
            comp = acc.get_component_iface()
            if comp is not None:
                comp.grab_focus()
        except Exception:
            pass
        return {"ok": True, "invoked": False}


class _AtspiSubprocessWorker:
    """Thread-safe JSON-line client for ``/usr/bin/python3 desktop_atspi.py --worker``."""

    def __init__(self, python_bin: str = "/usr/bin/python3") -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        if not os.path.exists(python_bin):
            return
        try:
            self._proc = subprocess.Popen(
                [python_bin, os.path.abspath(__file__), "--worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            resp = self.call("ping")
            if not resp or not resp.get("ok"):
                self.close()
        except Exception as exc:
            logger.debug("AT-SPI worker startup failed: %s", exc)
            self.close()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def call(self, method: str, **params: Any) -> dict[str, Any] | None:
        with self._lock:
            if not self.alive or self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
                return None
            try:
                payload = json.dumps({"method": method, "params": params})
                self._proc.stdin.write(payload + "\n")
                self._proc.stdin.flush()
                line = self._proc.stdout.readline()
                if not line:
                    return None
                return json.loads(line)
            except Exception as exc:
                logger.debug("AT-SPI worker call '%s' failed: %s", method, exc)
                return None

    def close(self) -> None:
        with self._lock:
            if self._proc is not None:
                try:
                    if self._proc.stdin:
                        self._proc.stdin.close()
                    self._proc.terminate()
                    self._proc.wait(timeout=1.0)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None


class LinuxDesktopAccessibilityBridge:
    """AT-SPI2 bridge for inspecting and clicking native Linux desktop apps."""

    def __init__(self, python_bin: str = "/usr/bin/python3") -> None:
        self._direct: _AtspiDirectEngine | None = None
        self._worker: _AtspiSubprocessWorker | None = None

        try:
            self._direct = _AtspiDirectEngine()
            logger.info("LinuxDesktopAccessibilityBridge initialized in-process.")
        except Exception:
            self._worker = _AtspiSubprocessWorker(python_bin=python_bin)
            if self._worker.alive:
                logger.info("LinuxDesktopAccessibilityBridge initialized via %s worker.", python_bin)
            else:
                self._worker = None
                logger.warning("AT-SPI2 unavailable; desktop accessibility fallback disabled.")

    @property
    def available(self) -> bool:
        """Return True if AT-SPI2 backend is ready."""
        return self._direct is not None or (self._worker is not None and self._worker.alive)

    def get_active_window_info(self) -> tuple[str, str]:
        """Return ``(app_name, window_title)`` of the currently focused desktop window."""
        if self._direct is not None:
            info = self._direct.get_active_window_info()
            return info.get("app_name", ""), info.get("window_title", "")
        if self._worker is not None:
            resp = self._worker.call("get_active_window_info")
            if resp and resp.get("ok"):
                data = resp.get("data") or {}
                return str(data.get("app_name", "")), str(data.get("window_title", ""))
        return "", ""

    def scan_active_window(self, max_nodes: int = 160) -> list[DesktopAccessibleNode]:
        """Return interactive widgets inside the active window and its popups."""
        raw_nodes: list[dict[str, Any]] = []
        if self._direct is not None:
            raw_nodes = self._direct.scan_active_window(max_nodes=max_nodes)
        elif self._worker is not None:
            resp = self._worker.call("scan_active_window", max_nodes=max_nodes)
            if resp and resp.get("ok"):
                raw_nodes = list(resp.get("data") or [])

        return [
            DesktopAccessibleNode(
                node_id=str(n["node_id"]),
                role=str(n.get("role", "")),
                name=str(n.get("name", "")),
                description=str(n.get("description", "")),
                cx=int(n.get("cx", 0)),
                cy=int(n.get("cy", 0)),
                w=int(n.get("w", 0)),
                h=int(n.get("h", 0)),
                is_editable=bool(n.get("is_editable", False)),
                is_clickable=bool(n.get("is_clickable", True)),
                aliases=[str(a) for a in (n.get("aliases") or [])],
                ordinal=int(n.get("ordinal", 0)),
            )
            for n in raw_nodes
        ]

    def _invoke(self, node_id: str, action: str = "click") -> bool:
        if self._direct is not None:
            res = self._direct.invoke(node_id, action=action)
            return bool(res.get("invoked", False))
        if self._worker is not None:
            resp = self._worker.call("invoke", node_id=node_id, action=action)
            if resp and resp.get("ok"):
                return bool((resp.get("data") or {}).get("invoked", False))
        return False

    def match_and_execute(
        self,
        target_query: str,
        nodes: list[DesktopAccessibleNode] | None = None,
        gaze_x: float | None = None,
        gaze_y: float | None = None,
        action: str = "click",
        threshold: float = 78.0,
    ) -> tuple[DesktopAccessibleNode, bool] | None:
        """Match ``target_query`` to an accessible widget and optionally invoke it.

        Returns ``(matched_node, invoked_via_dbus)`` or ``None``.
        """
        candidates = nodes if nodes is not None else self.scan_active_window()
        if not candidates:
            return None

        tokens = _normalize_query_tokens(target_query)
        if not tokens:
            return None
        normalized_query = " ".join(tokens)

        best_node: DesktopAccessibleNode | None = None
        best_score = -1.0

        for node in candidates:
            alias_pool = list(node.aliases)
            if node.name and node.name.lower() not in alias_pool:
                alias_pool.append(node.name.lower())
            if node.description and node.description.lower() not in alias_pool:
                alias_pool.append(node.description.lower())
            if node.is_editable:
                alias_pool.extend(["search", "search bar", "input", "text box"])

            node_best = 0.0
            for alias in alias_pool:
                if not alias:
                    continue
                if alias == normalized_query:
                    score = 100.0
                else:
                    score = _fuzzy_ratio(normalized_query, alias)
                if score > node_best:
                    node_best = score

            if node_best < threshold:
                continue

            combined = node_best
            if action == "type" and node.is_editable:
                combined += 8.0
            if gaze_x is not None and gaze_y is not None:
                dist = ((node.cx - gaze_x) ** 2 + (node.cy - gaze_y) ** 2) ** 0.5
                combined += max(0.0, 10.0 - dist / 80.0)

            if combined > best_score:
                best_score = combined
                best_node = node

        if best_node is None:
            return None

        invoked = self._invoke(best_node.node_id, action=action)
        return best_node, invoked

    def close(self) -> None:
        """Shut down the worker subprocess if active."""
        if self._worker is not None:
            self._worker.close()
            self._worker = None


def _run_worker() -> None:  # pragma: no cover - executed under /usr/bin/python3
    engine = _AtspiDirectEngine()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method = req.get("method")
            params = req.get("params") or {}
            if method == "ping":
                resp = {"ok": True}
            elif method == "get_active_window_info":
                resp = {"ok": True, "data": engine.get_active_window_info()}
            elif method == "scan_active_window":
                resp = {"ok": True, "data": engine.scan_active_window(**params)}
            elif method == "invoke":
                resp = {"ok": True, "data": engine.invoke(**params)}
            else:
                resp = {"ok": False, "error": f"Unknown method: {method}"}
        except Exception as exc:
            resp = {"ok": False, "error": str(exc)}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    if "--worker" in sys.argv:
        _run_worker()
