"""Compound and trailing voice command splitting for one-breath workflows."""

from __future__ import annotations

import re
import time
from typing import Callable, TypeVar

T = TypeVar("T")

_TRAILING_ENTER_RE = re.compile(
    r"^(.+?)\s*(?:,\s*|\s+(?:and|then)\s+|\s+)(press\s+enter|hit\s+enter|press\s+return|hit\s+return)\s*$",
    re.IGNORECASE,
)

_TRAILING_SUBMIT_WITH_CONJ_RE = re.compile(
    r"^(.+?)\s*(?:,\s*|\s+(?:and|then)\s+)(submit|enter)\s*$",
    re.IGNORECASE,
)

_TYPE_AND_CLICK_CONJ_RE = re.compile(
    r"^((?:type|write|dictate)\s+.+?)\s*(?:,\s*|\s+(?:and|then)\s+)((?:click|press|tap|select|open)\s+.+)$",
    re.IGNORECASE,
)

_TYPE_BARE_CLICK_RE = re.compile(
    r"^((?:type|write|dictate)\s+.+?)\s+(click\s+(?:\S+\s*){1,3})$",
    re.IGNORECASE,
)


def split_trailing_voice_command(transcript: str) -> list[str]:
    """Split a one-breath compound utterance into ordered command segments.

    Examples:
    - ``"type rust vs zig into search bar and press enter"``
      -> ``["type rust vs zig into search bar", "press enter"]``
    - ``"type hello world click submit"``
      -> ``["type hello world", "click submit"]``
    - ``"click search bar and type mechanical keyboards and press enter"``
      -> ``["click search bar and type mechanical keyboards", "press enter"]``
    """
    text = (transcript or "").strip()
    if not text:
        return []

    parts: list[str] = [text]

    # 1. Peel off trailing "press enter" / "hit enter" / "and submit" first.
    m_enter = _TRAILING_ENTER_RE.match(parts[0])
    if m_enter:
        head = m_enter.group(1).strip().rstrip(",")
        tail = "press enter"
        if head:
            parts = [head, tail]
    else:
        m_sub = _TRAILING_SUBMIT_WITH_CONJ_RE.match(parts[0])
        if m_sub:
            head = m_sub.group(1).strip().rstrip(",")
            tail_word = m_sub.group(2).strip().lower()
            tail = "press enter" if tail_word == "enter" else "click submit"
            if head:
                parts = [head, tail]

    # 2. If the head is a typing command followed by a click command, split it too.
    head = parts[0]
    m_conj_click = _TYPE_AND_CLICK_CONJ_RE.match(head)
    if m_conj_click:
        type_part = m_conj_click.group(1).strip().rstrip(",")
        click_part = m_conj_click.group(2).strip()
        if type_part and click_part:
            parts = [type_part, click_part, *parts[1:]]
    else:
        m_bare_click = _TYPE_BARE_CLICK_RE.match(head)
        if m_bare_click:
            type_part = m_bare_click.group(1).strip().rstrip(",")
            click_part = m_bare_click.group(2).strip()
            if type_part and click_part:
                parts = [type_part, click_part, *parts[1:]]

    return [p for p in parts if p]


def execute_compound_voice_commands(
    transcript: str,
    dispatch_single: Callable[[str], T],
    pause_between_s: float = 0.12,
) -> tuple[list[str], list[T]]:
    """Split ``transcript`` and run ``dispatch_single`` on each segment in order."""
    segments = split_trailing_voice_command(transcript)
    if not segments:
        return [], []

    results: list[T] = []
    for idx, seg in enumerate(segments):
        if idx > 0 and pause_between_s > 0:
            time.sleep(pause_between_s)
        results.append(dispatch_single(seg))
    return segments, results
