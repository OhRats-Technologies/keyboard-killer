"""Sub-10ms deterministic voice command router and prefix-based AI dispatcher."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from evdev import ecodes

from keyboard_killer.input.pointer import DryRunPointerBackend, PointerBackend
from keyboard_killer.ipc.firefox_bridge import FirefoxDOMBridge
from keyboard_killer.ipc.state import SharedGazeReader
from keyboard_killer.router.command_splitter import execute_compound_voice_commands
from keyboard_killer.router.gaze_vlm_clicker import GazeVLMClicker, parse_voice_click_command
from keyboard_killer.router.llm_agent import LLMAgent
from keyboard_killer.router.scroll_handler import parse_voice_scroll_command

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of routing a spoken transcript."""

    route: str
    transcript: str
    detail: str
    payload: dict[str, Any] | None = None


def _normalize_command(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", text.lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


class VoiceCommandRouter:
    """Routes transcripts between <10ms deterministic actions and prefix-routed AI workflows."""

    def __init__(
        self,
        pointer: PointerBackend,
        llm_agent: LLMAgent | None = None,
        gaze_reader: SharedGazeReader | None = None,
        gaze_provider: Callable[[], tuple[float, float] | None] | None = None,
        browser_callback: Callable[[dict[str, Any]], None] | None = None,
        dictate_unprefixed: bool = False,
        gaze_vlm_clicker: GazeVLMClicker | None = None,
        gaze_state_provider: Callable[[], tuple[float, float, np.ndarray | None] | None]
        | None = None,
        on_before_click: Callable[[int, int], None] | None = None,
        firefox_bridge: FirefoxDOMBridge | None = None,
        on_recenter_gaze: Callable[[], str] | None = None,
    ) -> None:
        self.pointer = pointer
        self.llm_agent = llm_agent
        self.gaze_reader = gaze_reader
        self.gaze_provider = gaze_provider
        self.browser_callback = browser_callback
        self.dictate_unprefixed = dictate_unprefixed
        self.gaze_vlm_clicker = gaze_vlm_clicker
        self.gaze_state_provider = gaze_state_provider
        self.on_before_click = on_before_click
        self.on_recenter_gaze = on_recenter_gaze
        self.firefox_bridge = (
            firefox_bridge
            if firefox_bridge is not None
            else (gaze_vlm_clicker.firefox_bridge if gaze_vlm_clicker is not None else None)
        )
        self._window_cycle_step = 0
        self._window_cycle_last_t = 0.0

        self._media_map: dict[str, tuple[str, int]] = {
            "pause": ("pause", ecodes.KEY_PLAYPAUSE),
            "pause video": ("pause", ecodes.KEY_PLAYPAUSE),
            "pause the video": ("pause", ecodes.KEY_PLAYPAUSE),
            "play": ("play", ecodes.KEY_PLAYPAUSE),
            "play video": ("play", ecodes.KEY_PLAYPAUSE),
            "play the video": ("play", ecodes.KEY_PLAYPAUSE),
            "resume": ("resume", ecodes.KEY_PLAYPAUSE),
            "resume video": ("resume", ecodes.KEY_PLAYPAUSE),
            "mute": ("mute", ecodes.KEY_MUTE),
            "mute video": ("mute", ecodes.KEY_MUTE),
            "mute audio": ("mute", ecodes.KEY_MUTE),
            "unmute": ("unmute", ecodes.KEY_MUTE),
            "unmute video": ("unmute", ecodes.KEY_MUTE),
            "unmute audio": ("unmute", ecodes.KEY_MUTE),
            "fullscreen": ("fullscreen", ecodes.KEY_F),
            "full screen": ("fullscreen", ecodes.KEY_F),
            "exit fullscreen": ("exit_fullscreen", ecodes.KEY_ESC),
            "exit full screen": ("exit_fullscreen", ecodes.KEY_ESC),
        }

        # Require explicit "click here" / "click now" / "left click" / "tap" for in-place clicks.
        # Bare "click" is ignored so truncated utterances never blind-click random spots.
        self._deterministic_map: dict[str, tuple[str, Callable[[], None]]] = {
            "click here": ("click_left", lambda: self.pointer.click(ecodes.BTN_LEFT)),
            "click now": ("click_left", lambda: self.pointer.click(ecodes.BTN_LEFT)),
            "tap": ("click_left", lambda: self.pointer.click(ecodes.BTN_LEFT)),
            "tap here": ("click_left", lambda: self.pointer.click(ecodes.BTN_LEFT)),
            "left click": ("click_left", lambda: self.pointer.click(ecodes.BTN_LEFT)),
            "right click": ("click_right", lambda: self.pointer.click(ecodes.BTN_RIGHT)),
            "middle click": ("click_middle", lambda: self.pointer.click(ecodes.BTN_MIDDLE)),
            "double click": ("double_click", lambda: self.pointer.double_click(ecodes.BTN_LEFT)),
            "scroll down": ("scroll_down", lambda: self.pointer.scroll(dy=-4)),
            "scroll up": ("scroll_up", lambda: self.pointer.scroll(dy=4)),
            "page down": ("page_down", lambda: self.pointer.key_combo(ecodes.KEY_PAGEDOWN)),
            "page up": ("page_up", lambda: self.pointer.key_combo(ecodes.KEY_PAGEUP)),
            "go back": ("nav_back", lambda: self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_LEFT)),
            "back": ("nav_back", lambda: self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_LEFT)),
            "go forward": (
                "nav_forward",
                lambda: self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_RIGHT),
            ),
            "forward": (
                "nav_forward",
                lambda: self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_RIGHT),
            ),
            "copy": ("copy", lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_C)),
            "paste": ("paste", lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_V)),
            "cut": ("cut", lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_X)),
            "undo": ("undo", lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_Z)),
            "redo": (
                "redo",
                lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTSHIFT, ecodes.KEY_Z),
            ),
            "select all": ("select_all", lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_A)),
            "close tab": ("close_tab", lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_W)),
            "new tab": ("new_tab", lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_T)),
            "next tab": ("next_tab", lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_TAB)),
            "previous tab": (
                "prev_tab",
                lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTSHIFT, ecodes.KEY_TAB),
            ),
            "prev tab": (
                "prev_tab",
                lambda: self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTSHIFT, ecodes.KEY_TAB),
            ),
            "switch tab": ("os_window_next", self._cycle_next_window),
            "switch window": ("os_window_next", self._cycle_next_window),
            "switch app": ("os_window_next", self._cycle_next_window),
            "cycle window": ("os_window_next", self._cycle_next_window),
            "alt tab": (
                "os_alt_tab",
                lambda: self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_TAB),
            ),
            "next window": ("os_window_next", self._cycle_next_window),
            "previous window": ("os_window_prev", self._cycle_prev_window),
            "prev window": ("os_window_prev", self._cycle_prev_window),
            "last window": ("os_window_prev", self._cycle_prev_window),
            "switch back": (
                "os_alt_shift_tab",
                lambda: self.pointer.key_combo(
                    ecodes.KEY_LEFTALT, ecodes.KEY_LEFTSHIFT, ecodes.KEY_TAB
                ),
            ),
            "switch same app": (
                "os_alt_grave",
                lambda: self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_GRAVE),
            ),
            "next app window": (
                "os_alt_grave",
                lambda: self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_GRAVE),
            ),
            "alt grave": (
                "os_alt_grave",
                lambda: self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_GRAVE),
            ),
            "overview": ("os_overview", lambda: self.pointer.key_combo(ecodes.KEY_LEFTMETA)),
            "show windows": ("os_overview", lambda: self.pointer.key_combo(ecodes.KEY_LEFTMETA)),
            "activities": ("os_overview", lambda: self.pointer.key_combo(ecodes.KEY_LEFTMETA)),
            "next workspace": (
                "os_workspace_next",
                lambda: self.pointer.key_combo(
                    ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTALT, ecodes.KEY_RIGHT
                ),
            ),
            "workspace right": (
                "os_workspace_next",
                lambda: self.pointer.key_combo(
                    ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTALT, ecodes.KEY_RIGHT
                ),
            ),
            "previous workspace": (
                "os_workspace_prev",
                lambda: self.pointer.key_combo(
                    ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTALT, ecodes.KEY_LEFT
                ),
            ),
            "prev workspace": (
                "os_workspace_prev",
                lambda: self.pointer.key_combo(
                    ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTALT, ecodes.KEY_LEFT
                ),
            ),
            "workspace left": (
                "os_workspace_prev",
                lambda: self.pointer.key_combo(
                    ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTALT, ecodes.KEY_LEFT
                ),
            ),
            "enter": ("enter", lambda: self.pointer.key_combo(ecodes.KEY_ENTER)),
            "press enter": ("enter", lambda: self.pointer.key_combo(ecodes.KEY_ENTER)),
            "escape": ("escape", lambda: self.pointer.key_combo(ecodes.KEY_ESC)),
            "press escape": ("escape", lambda: self.pointer.key_combo(ecodes.KEY_ESC)),
            "tab": ("tab", lambda: self.pointer.key_combo(ecodes.KEY_TAB)),
            "space": ("space", lambda: self.pointer.key_combo(ecodes.KEY_SPACE)),
            "backspace": ("backspace", lambda: self.pointer.key_combo(ecodes.KEY_BACKSPACE)),
            "delete line": ("delete_line", self._delete_line),
        }

    def _delete_line(self) -> None:
        self.pointer.key_combo(ecodes.KEY_HOME)
        self.pointer.key_combo(ecodes.KEY_LEFTSHIFT, ecodes.KEY_END)
        self.pointer.key_combo(ecodes.KEY_BACKSPACE)

    def current_gaze_xy(self) -> tuple[float, float] | None:
        if self.gaze_provider is not None:
            pos = self.gaze_provider()
            if pos is not None:
                return pos
        if self.gaze_reader is None:
            return None
        snap = self.gaze_reader.read()
        if snap is None:
            return None
        return (snap.x, snap.y)

    def _current_gaze_and_feat(self) -> tuple[float, float, np.ndarray | None]:
        if self.gaze_state_provider is not None:
            state = self.gaze_state_provider()
            if state is not None:
                return state
        gaze_xy = self.current_gaze_xy()
        if gaze_xy is not None:
            return (gaze_xy[0], gaze_xy[1], None)
        if self.gaze_vlm_clicker is not None:
            return (
                self.gaze_vlm_clicker.screen_w * 0.5,
                self.gaze_vlm_clicker.screen_h * 0.5,
                None,
            )
        return (960.0, 540.0, None)

    def _execute_vlm_intent(self, raw: str, norm: str) -> DispatchResult | None:
        if self.gaze_vlm_clicker is None:
            return None
        intent = parse_voice_click_command(raw)
        if intent is None:
            return None
        if intent.action == "type_into" and re.match(r"^(?:type|dictate)\s*:", raw, flags=re.IGNORECASE):
            return None
        if norm == "click this" and self.browser_callback is not None:
            return None

        gx, gy, feat = self._current_gaze_and_feat()
        res = self.gaze_vlm_clicker.resolve_voice_target(raw, gx, gy, feat)
        if res is None:
            return DispatchResult(
                "gaze_vlm_miss",
                raw,
                f"No UI target found for {intent.target_query!r}",
            )

        resolved_intent, (tx, ty), tier_info = res
        if self.on_before_click is not None:
            self.on_before_click(tx, ty)
        self.pointer.move_absolute(tx, ty)
        if (
            self.firefox_bridge is not None
            and self.firefox_bridge.is_connected
            and hasattr(self.firefox_bridge, "send_pointer_anchor")
        ):
            self.firefox_bridge.send_pointer_anchor(tx, ty)
        if not isinstance(self.pointer, DryRunPointerBackend):
            time.sleep(0.025)

        # Tier-0 Firefox DOM or Tier-0.5 Desktop AT-SPI already executed the click when invoked=True.
        already_executed = tier_info.startswith("Tier-0 Firefox DOM (") or (
            resolved_intent.action == "click"
            and tier_info.startswith("Tier-0.5 Desktop AT-SPI (")
            and "invoked=True)" in tier_info
        )
        if not already_executed:
            if resolved_intent.action == "click":
                self.pointer.click(ecodes.BTN_LEFT)
            elif resolved_intent.action == "double_click":
                self.pointer.double_click(ecodes.BTN_LEFT)
            elif resolved_intent.action == "right_click":
                self.pointer.click(ecodes.BTN_RIGHT)
            elif resolved_intent.action == "type_into":
                self.pointer.click(ecodes.BTN_LEFT)
                if not isinstance(self.pointer, DryRunPointerBackend):
                    time.sleep(0.025)
                self.pointer.type_text(resolved_intent.text_to_type)

        return DispatchResult(
            "gaze_vlm_click",
            raw,
            f"{tier_info} -> ({tx}, {ty})",
            payload={
                "action": resolved_intent.action,
                "x": tx,
                "y": ty,
                "tier": tier_info,
                "text": resolved_intent.text_to_type,
            },
        )

    def _cycle_next_window(self) -> None:
        """Rotate through ALL open windows without MRU ping-pong.

        In an MRU list [A, B, C, D], picking index 1 (Alt+Escape / Alt+Tab) swaps A <-> B forever,
        whereas picking the tail (Shift+Alt+Escape: cycle-windows-backward) rotates the entire
        ring [D, A, B, C] -> [C, D, A, B] -> [B, C, D, A] -> [A, B, C, D], visiting every window!
        """
        self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_LEFTSHIFT, ecodes.KEY_ESC)

    def _cycle_prev_window(self) -> None:
        """Jump back to the immediately preceding window in MRU order (Alt+Escape)."""
        self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_ESC)

    _APP_SEARCH_ALIASES: dict[str, str] = {
        "vscode": "visual studio code",
        "vs code": "visual studio code",
        "code": "visual studio code",
        "chrome": "google chrome",
    }

    def _focus_os_app_via_overview(self, app_name: str) -> None:
        """Focus or launch an OS window/app in GNOME Shell via Super + search + Enter."""
        query = self._APP_SEARCH_ALIASES.get(app_name.strip().lower(), app_name.strip())
        self.pointer.key_combo(ecodes.KEY_LEFTMETA)
        if not isinstance(self.pointer, DryRunPointerBackend):
            time.sleep(0.18)
        self.pointer.type_text(query)
        if not isinstance(self.pointer, DryRunPointerBackend):
            time.sleep(0.15)
        self.pointer.key_combo(ecodes.KEY_ENTER)

    def _handle_tab_or_window_command(self, norm: str, raw: str) -> DispatchResult | None:
        ordinal_keys = {
            "1": ecodes.KEY_1,
            "first": ecodes.KEY_1,
            "one": ecodes.KEY_1,
            "2": ecodes.KEY_2,
            "second": ecodes.KEY_2,
            "two": ecodes.KEY_2,
            "3": ecodes.KEY_3,
            "third": ecodes.KEY_3,
            "three": ecodes.KEY_3,
            "4": ecodes.KEY_4,
            "fourth": ecodes.KEY_4,
            "four": ecodes.KEY_4,
            "5": ecodes.KEY_5,
            "fifth": ecodes.KEY_5,
            "five": ecodes.KEY_5,
            "6": ecodes.KEY_6,
            "sixth": ecodes.KEY_6,
            "six": ecodes.KEY_6,
            "7": ecodes.KEY_7,
            "seventh": ecodes.KEY_7,
            "seven": ecodes.KEY_7,
            "8": ecodes.KEY_8,
            "eighth": ecodes.KEY_8,
            "eight": ecodes.KEY_8,
        }

        # 0. New browser tab: "new tab", "open a new tab", "new tab <url/query>"
        m_new_tab = re.match(
            r"^(?:(?:open|create)\s+(?:a\s+)?)?new\s+tab(?:\s+(?:(?:to|for|with|on)\s+)?(.+))?$",
            raw.strip().rstrip(".,!?"),
            flags=re.IGNORECASE,
        )
        if m_new_tab:
            target_q = (m_new_tab.group(1) or "").strip() or None
            if self.firefox_bridge is not None and self.firefox_bridge.is_connected:
                if self.firefox_bridge.new_tab(target_q):
                    detail = f"Firefox new tab ({target_q})" if target_q else "Firefox new tab"
                    return DispatchResult("tab_new", raw, detail)
            if target_q is None:
                self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_T)
                return DispatchResult("deterministic", raw, "new_tab")

        # 0.5. Close active OS window ("close window", "close this window", "close app")
        if norm in (
            "close window",
            "close this window",
            "close the window",
            "close app",
            "close this app",
            "close the app",
            "close application",
            "alt f4",
        ):
            self.pointer.key_combo(ecodes.KEY_LEFTALT, ecodes.KEY_F4)
            return DispatchResult("deterministic", raw, "close_window")

        # 0.6. Explicit next/previous window cycling: strip leading fillers ("a", "the", "and",
        # "uh", "go to", "switch to", "move to") AND catch common Whisper beam=1 near-homophones
        # ("text window", "net window", "nest window", "ex window") so they NEVER open OS search!
        stripped_win = re.sub(
            r"^(?:(?:uh|um|and|a|an|the|to|go\s+to|switch\s+to|move\s+to)\s+)+",
            "",
            norm,
        )
        if stripped_win in (
            "next window",
            "other window",
            "another window",
            "text window",
            "net window",
            "nest window",
            "neck window",
            "necks window",
            "ex window",
        ):
            self._cycle_next_window()
            return DispatchResult("deterministic", raw, "os_window_next")

        if stripped_win in (
            "previous window",
            "prev window",
            "last window",
        ):
            self._cycle_prev_window()
            return DispatchResult("deterministic", raw, "os_window_prev")

        # 1. Explicit OS app/window focus: "switch window to X", "focus X", "open app X", or "antigravity window"
        _forbidden_app_tokens = {
            "close",
            "open",
            "new",
            "show",
            "this",
            "that",
            "next",
            "previous",
            "prev",
            "last",
            "other",
            "another",
            "cycle",
            "switch",
            "sync",
            "align",
            "minimize",
            "maximize",
        }
        m_os = re.match(
            r"^(?:switch\s+(?:window|app)\s+(?:to\s+)?|focus\s+(?:window\s+|app\s+|on\s+)?|open\s+app\s+)(.+)$",
            norm,
        )
        if m_os:
            target = re.sub(r"\s+(?:window|app)$", "", m_os.group(1).strip()).strip()
            target_tokens = set(target.split())
            if target and not (target_tokens & _forbidden_app_tokens):
                self._focus_os_app_via_overview(target)
                return DispatchResult("os_window_switch", raw, f"OS switch/focus: {target}")

        m_app_suffix = re.match(r"^(?:the\s+)?(.+?)\s+(?:window|app)$", norm)
        if m_app_suffix and norm not in self._deterministic_map:
            target = m_app_suffix.group(1).strip()
            target_tokens = set(target.split())
            if target and not (target_tokens & _forbidden_app_tokens) and len(target.split()) <= 2:
                self._focus_os_app_via_overview(target)
                return DispatchResult("os_window_switch", raw, f"OS switch/focus: {target}")

        # 2. Explicit browser tab switch: "switch browser tab to X", "go to tab X", "tab 2", "second tab"
        browser_target: str | None = None
        m_br = re.match(
            r"^(?:switch\s+browser\s+tab\s+(?:to\s+)?|go\s+to\s+tab\s+|open\s+tab\s+|tab\s+)(.+)$",
            norm,
        )
        if m_br:
            browser_target = m_br.group(1).strip()
        elif re.match(r"^(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|last)\s+tab$", norm):
            browser_target = norm.rsplit(" ", 1)[0].strip()

        if browser_target:
            if self.firefox_bridge is not None and self.firefox_bridge.is_connected:
                title = self.firefox_bridge.switch_tab(browser_target)
                if title is not None:
                    return DispatchResult("tab_switch", raw, f"Firefox tab: {title}")
            if browser_target in ordinal_keys:
                self.pointer.key_combo(ecodes.KEY_LEFTALT, ordinal_keys[browser_target])
                return DispatchResult("deterministic", raw, f"tab_ordinal:{browser_target}")
            return DispatchResult("tab_miss", raw, f"No matching browser tab: {browser_target}")

        # 3. Ambiguous "switch to X" or "switch tab to X": prefer matching Firefox tab if connected, else OS app!
        m_ambig = re.match(
            r"^(?:switch\s+(?:tab\s+)?to\s+|switch\s+tab\s+|go\s+to\s+)(.+)$",
            norm,
        )
        if m_ambig:
            target = re.sub(r"\s+tab$", "", m_ambig.group(1).strip()).strip()
            if target in ("next", "next tab"):
                self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_TAB)
                return DispatchResult("deterministic", raw, "next_tab")
            if target in ("prev", "previous", "previous tab"):
                self.pointer.key_combo(ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTSHIFT, ecodes.KEY_TAB)
                return DispatchResult("deterministic", raw, "prev_tab")
            if target and self.firefox_bridge is not None and self.firefox_bridge.is_connected:
                title = self.firefox_bridge.switch_tab(target)
                if title is not None:
                    return DispatchResult("tab_switch", raw, f"Firefox tab: {title}")
            if target:
                self._focus_os_app_via_overview(target)
                return DispatchResult("os_window_switch", raw, f"OS switch/focus: {target}")

        return None

    def dispatch(self, transcript: str) -> DispatchResult:
        """Route a transcribed utterance, supporting one-breath compound trailing commands."""
        pause_s = 0.0 if isinstance(self.pointer, DryRunPointerBackend) else 0.12
        _, results = execute_compound_voice_commands(
            transcript,
            self._dispatch_single,
            pause_between_s=pause_s,
        )
        if not results:
            return self._dispatch_single(transcript)
        if len(results) == 1:
            return results[0]
        return DispatchResult(
            results[-1].route,
            transcript.strip(),
            " -> ".join(f"{r.route}:{r.detail}" for r in results),
            payload={"steps": [r.__dict__ for r in results]},
        )

    def _dispatch_single(self, transcript: str) -> DispatchResult:
        """Route a single transcribed utterance into deterministic, Gaze-VLM, Type, Terminal, or Browser actions."""
        raw = transcript.strip()
        if not raw:
            if self.gaze_vlm_clicker is not None:
                self.gaze_vlm_clicker.clear_pending_snapshot()
            return DispatchResult("empty", raw, "Ignored empty transcript.")

        norm = _normalize_command(raw)

        # 1. Zero-latency deterministic control layer
        if norm == "click":
            if self.gaze_vlm_clicker is not None:
                self.gaze_vlm_clicker.clear_pending_snapshot()
            return DispatchResult(
                "ignored",
                raw,
                "Ignored bare 'click' (say 'click <target>' or 'click here').",
            )

        if norm in self._media_map:
            if self.gaze_vlm_clicker is not None:
                self.gaze_vlm_clicker.clear_pending_snapshot()
            dom_action, fallback_key = self._media_map[norm]
            if self.firefox_bridge is not None and self.firefox_bridge.is_connected:
                if self.firefox_bridge.execute_media_action(dom_action):
                    return DispatchResult("media_dom", raw, f"Firefox DOM media: {dom_action}")
            self.pointer.key_combo(fallback_key)
            return DispatchResult("deterministic", raw, f"media_key:{dom_action}")

        if norm not in ("switch tab", "switch window", "switch app", "cycle window", "next window", "previous window", "prev window", "last window"):
            self._window_cycle_step = 0

        # Explicit manual window-origin sync ("sync window" / "align window") for floating Wayland Firefox
        # Must run BEFORE _handle_tab_or_window_command so "<app> window" doesn't treat "sync" as an app name!
        if norm in ("sync window", "align window"):
            if self.gaze_vlm_clicker is not None:
                self.gaze_vlm_clicker.clear_pending_snapshot()
            gx, gy, _ = self._current_gaze_and_feat()
            tx, ty = int(gx), int(gy)
            if (
                self.firefox_bridge is not None
                and self.firefox_bridge.is_connected
                and hasattr(self.firefox_bridge, "send_pointer_anchor")
            ):
                self.firefox_bridge.send_pointer_anchor(tx, ty)
            self.pointer.move_absolute(tx, ty)
            if not isinstance(self.pointer, DryRunPointerBackend):
                time.sleep(0.02)
                self.pointer.move_relative(1.0, 0.0)
            return DispatchResult("sync_window", raw, f"Anchored Firefox window origin at ({tx}, {ty})")

        tab_res = self._handle_tab_or_window_command(norm, raw)
        if tab_res is not None:
            if self.gaze_vlm_clicker is not None:
                self.gaze_vlm_clicker.clear_pending_snapshot()
            return tab_res

        # Voice command: "save icon as <name>" or "remember icon as <name>"
        if norm.startswith(("save icon as ", "remember icon as ")) and self.gaze_vlm_clicker is not None:
            self.gaze_vlm_clicker.clear_pending_snapshot()
            icon_name = re.split(r"\bas\b", raw, maxsplit=1, flags=re.IGNORECASE)[1].strip(" .,!?\"'")
            gx, gy, _ = self._current_gaze_and_feat()
            cx, cy = int(gx), int(gy)
            det = self.gaze_vlm_clicker.detector
            x0, y0 = max(0, cx - 22), max(0, cy - 22)
            if det._capture_fn is not None:
                patch = det._capture_fn(x0, y0, 44, 44)
            else:
                patch = det.capturer.capture_rect(x0, y0, 44, 44)
            det.icon_bank.save_icon_patch(icon_name, patch)
            return DispatchResult("save_icon", raw, f"Learned visual icon template for '{icon_name}'")

        # Explicit manual gaze recenter command ("recenter gaze" / "calibrate center")
        if norm in ("recenter gaze", "recenter", "calibrate center"):
            if self.gaze_vlm_clicker is not None:
                self.gaze_vlm_clicker.clear_pending_snapshot()
            if self.on_recenter_gaze is not None:
                detail = self.on_recenter_gaze()
                return DispatchResult("recenter_gaze", raw, detail)
            return DispatchResult("recenter_gaze", raw, "No active gaze model to recenter.")

        # Gaze-aware Firefox DOM scrolling & gaze-warped native app scrolling
        scroll_cmd = parse_voice_scroll_command(raw)
        if scroll_cmd is not None:
            if self.gaze_vlm_clicker is not None:
                self.gaze_vlm_clicker.clear_pending_snapshot()
            gx, gy, _ = self._current_gaze_and_feat()
            firefox_active = (
                self.gaze_vlm_clicker._is_firefox_active()
                if self.gaze_vlm_clicker is not None
                else True
            )
            if (
                firefox_active
                and self.firefox_bridge is not None
                and self.firefox_bridge.is_connected
                and hasattr(self.firefox_bridge, "scroll_page")
            ):
                if self.firefox_bridge.scroll_page(
                    gaze_x=gx,
                    gaze_y=gy,
                    direction=scroll_cmd.direction,
                    amount=scroll_cmd.amount,
                    continuous=scroll_cmd.continuous,
                ):
                    return DispatchResult(
                        "scroll_dom",
                        raw,
                        f"Firefox DOM scroll ({scroll_cmd.direction}, amount={scroll_cmd.amount}, continuous='{scroll_cmd.continuous}')",
                    )
            has_live_gaze = (
                self.gaze_state_provider is not None or self.gaze_provider is not None
            )
            if norm not in self._deterministic_map or has_live_gaze:
                if scroll_cmd.wheel_ticks != 0:
                    if has_live_gaze:
                        self.pointer.move_absolute(int(gx), int(gy))
                    if scroll_cmd.direction in ("left", "right"):
                        self.pointer.scroll(dx=scroll_cmd.wheel_ticks, dy=0)
                    else:
                        self.pointer.scroll(dx=0, dy=scroll_cmd.wheel_ticks)
                return DispatchResult(
                    "scroll",
                    raw,
                    f"scroll:{scroll_cmd.direction}:{scroll_cmd.amount}:{scroll_cmd.continuous}",
                )

        if norm in self._deterministic_map:
            if self.gaze_vlm_clicker is not None:
                self.gaze_vlm_clicker.clear_pending_snapshot()
            action_name, fn = self._deterministic_map[norm]
            fn()
            return DispatchResult("deterministic", raw, action_name)

        # 1.5. Gaze-Foveated Voice + Vision Grounding (Tier-1 OCR -> Tier-2 gemma4:e4b -> Tier-3 Fullscreen)
        vlm_res = self._execute_vlm_intent(raw, norm)
        if vlm_res is not None:
            return vlm_res

        if self.gaze_vlm_clicker is not None:
            self.gaze_vlm_clicker.clear_pending_snapshot()

        # 2. Prefix: "Type ..." -> Direct raw text injection via uinput
        m_type = re.match(r"^(?:type|dictate)\b[\s,.:;-]*(.+)$", raw, flags=re.IGNORECASE)
        if m_type:
            text_body = m_type.group(1).strip()
            self.pointer.type_text(text_body)
            return DispatchResult("type", raw, f"Typed {len(text_body)} chars")

        # 3. Prefix: "Terminal ..." -> Generate bash command and place onto prompt (no Enter)
        m_term = re.match(r"^(?:terminal|shell|bash)\b[\s,.:;-]*(.+)$", raw, flags=re.IGNORECASE)
        if m_term:
            instruction = m_term.group(1).strip()
            if self.llm_agent is None:
                return DispatchResult("error", raw, "No LLMAgent configured for Terminal route.")
            cmd = self.llm_agent.generate_shell_command(instruction)
            self.pointer.type_text(cmd)
            return DispatchResult("terminal", raw, cmd, payload={"command": cmd})

        # 4. Prefix: "Browser ..." (or gaze-contextual queries like "What's this" / "Summarize this")
        m_browser = re.match(
            r"^(?:browser|firefox|web)\b[\s,.:;-]*(.+)$",
            raw,
            flags=re.IGNORECASE,
        )
        is_gaze_prompt = bool(
            re.match(
                r"^(?:what(?:'?s| is) this|summarize this|fill this|click this)\b",
                norm,
            )
        )
        if m_browser or is_gaze_prompt:
            instruction = m_browser.group(1).strip() if m_browser else raw
            gaze_xy = self.current_gaze_xy()
            payload: dict[str, Any] = {
                "type": "browser_command",
                "instruction": instruction,
                "gaze": {"x": gaze_xy[0], "y": gaze_xy[1]} if gaze_xy else None,
            }
            if self.browser_callback is not None:
                self.browser_callback(payload)
            return DispatchResult("browser", raw, instruction, payload=payload)

        # 5. Unprefixed fallback
        if self.dictate_unprefixed:
            self.pointer.type_text(raw + " ")
            return DispatchResult("type_fallback", raw, f"Dictated {len(raw)} chars")

        return DispatchResult(
            "unmatched",
            raw,
            "No prefix matched (say 'Type ...', 'Terminal ...', 'Browser ...', or a command).",
        )
