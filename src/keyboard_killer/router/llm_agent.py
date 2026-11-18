"""Local Ollama & Gemini API structured client for Terminal and Browser voice commands."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    m = re.match(r"^```[a-zA-Z0-9_-]*\n(.*?)\n```$", cleaned, flags=re.DOTALL)
    if m:
        return m.group(1).strip()
    return cleaned.strip("`").strip()


class LLMAgent:
    """Unified async/sync client for Ollama (local) or Gemini API (cloud)."""

    def __init__(
        self,
        backend: str = "auto",
        ollama_url: str = "http://127.0.0.1:11434",
        ollama_model: str = "qwen2.5-coder:7b",
        gemini_model: str = "gemini-2.5-flash",
        gemini_api_key: str | None = None,
        timeout_sec: float = 20.0,
    ) -> None:
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self.gemini_model = gemini_model
        self.gemini_api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY", "")
        self.timeout_sec = float(timeout_sec)

        if backend == "auto":
            self.backend = "gemini" if self.gemini_api_key else "ollama"
        elif backend in ("ollama", "gemini"):
            self.backend = backend
        else:
            raise ValueError(f"Unsupported LLM backend: {backend!r}")

    def complete(self, system_prompt: str, user_prompt: str, json_mode: bool = False) -> str:
        """Execute a synchronous chat completion against the configured backend."""
        if self.backend == "gemini":
            return self._complete_gemini(system_prompt, user_prompt, json_mode=json_mode)
        return self._complete_ollama(system_prompt, user_prompt, json_mode=json_mode)

    def _complete_ollama(self, system_prompt: str, user_prompt: str, json_mode: bool) -> str:
        payload: dict[str, Any] = {
            "model": self.ollama_model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            payload["format"] = "json"
        with httpx.Client(timeout=self.timeout_sec) as client:
            resp = client.post(f"{self.ollama_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return str(data["message"]["content"]).strip()

    def _complete_gemini(self, system_prompt: str, user_prompt: str, json_mode: bool) -> str:
        if not self.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent"
        )
        gen_cfg: dict[str, Any] = {"temperature": 0.1}
        if json_mode:
            gen_cfg["responseMimeType"] = "application/json"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": gen_cfg,
        }
        with httpx.Client(timeout=self.timeout_sec) as client:
            resp = client.post(url, params={"key": self.gemini_api_key}, json=payload)
            resp.raise_for_status()
            data = resp.json()
        return str(data["candidates"][0]["content"]["parts"][0]["text"]).strip()

    def generate_shell_command(
        self,
        instruction: str,
        cwd: str | None = None,
        files_preview: list[str] | None = None,
    ) -> str:
        """Translate natural-language terminal request into a single bash command."""
        cur_dir = cwd or os.getcwd()
        if files_preview is None:
            try:
                files_preview = sorted(os.listdir(cur_dir))[:30]
            except OSError:
                files_preview = []
        system = (
            "You translate voice requests into a single concise Linux bash command. "
            "Return ONLY the raw bash command on one line with no explanation or markdown."
        )
        user = (
            f"Working directory: {cur_dir}\n"
            f"Files: {', '.join(files_preview)}\n"
            f"Request: {instruction}"
        )
        return _strip_code_fences(self.complete(system, user, json_mode=False))

    def plan_browser_action(
        self,
        instruction: str,
        gaze_xy: tuple[float, float] | None = None,
        dom_snapshot: str = "",
    ) -> dict[str, Any]:
        """Generate a structured Firefox extension tool call payload."""
        system = (
            "You control a Firefox WebExtension via structured JSON tool calls. "
            "Respond strictly with JSON containing keys `action` and `args`. "
            "Allowed actions: `click_at_gaze`, `click_selector`, `fill_input`, "
            "`summarize_near_gaze`, `search_tabs`, `scroll`."
        )
        gaze_txt = f"({gaze_xy[0]:.0f}, {gaze_xy[1]:.0f})" if gaze_xy else "unknown"
        user = (
            f"Gaze screen coordinates: {gaze_txt}\n"
            f"DOM snippet:\n{dom_snapshot[:4000]}\n\n"
            f"User request: {instruction}"
        )
        raw = _strip_code_fences(self.complete(system, user, json_mode=True))
        return dict(json.loads(raw))
