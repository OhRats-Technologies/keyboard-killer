"""Unit tests for Underdog Woof local TTS catalog, speaker queue, and assistant routing."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np

from keyless.audio.active_listener import ActiveVoiceListener
from keyless.config.paths import HOME_ENV, default_paths
from keyless.input.pointer import DryRunPointerBackend
from keyless.models.catalog import UNDERDOG_WOOF, get_asset, tts_assets
from keyless.models.receipts import FileReceipt, ModelReceipt
from keyless.models.store import ModelStore, sha256_file
from keyless.router.dispatcher import VoiceCommandRouter
from keyless.voice.tts import WoofSpeaker, clean_text_for_speech, tts_report


def test_underdog_woof_catalog_is_pinned() -> None:
    asset = get_asset("underdog-woof")
    assert asset is UNDERDOG_WOOF
    assert asset in tts_assets()
    assert len(asset.revision) == 40
    assert asset.license == "underdog-non-commercial-1.0"
    assert asset.total_bytes > 40_000_000
    assert all(len(f.sha256) == 64 and f.bytes > 0 for f in asset.files)


def test_clean_text_for_speech_strips_markdown_and_urls() -> None:
    raw = "Here is **bold** `code` [link text](https://example.com) and https://foo.bar\n```bash\nls -la\n```"
    cleaned = clean_text_for_speech(raw)
    assert "**" not in cleaned
    assert "`" not in cleaned
    assert "https://" not in cleaned
    assert "link text" in cleaned
    assert "bold" in cleaned


def test_tts_report_detects_missing_and_ready_asset(tmp_path) -> None:
    paths = default_paths(env={HOME_ENV: str(tmp_path)}, platform_name="linux")
    paths.ensure_directories()
    missing = tts_report(paths)
    assert missing["ready"] is False
    assert missing["state"] == "missing"

    store = ModelStore(paths)
    asset_dir = store.asset_dir(UNDERDOG_WOOF)
    receipts: dict[str, FileReceipt] = {}
    for item in UNDERDOG_WOOF.files:
        f = asset_dir / item.name
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("wb") as fp:
            fp.truncate(item.bytes)
    ModelReceipt(
        asset=UNDERDOG_WOOF.id,
        engine=UNDERDOG_WOOF.engine,
        quantization=UNDERDOG_WOOF.quantization,
        revision=UNDERDOG_WOOF.revision,
        files={item.name: FileReceipt(item.name, item.bytes, item.sha256) for item in UNDERDOG_WOOF.files},
    ).write_atomic(store.receipt_path(UNDERDOG_WOOF))

    ready = tts_report(paths, verify_hashes=False)
    assert ready["ready"] is True
    assert ready["voices"] == ["juno", "mira"]


def test_woof_speaker_queues_and_toggles_speaking_callback(tmp_path) -> None:
    paths = default_paths(env={HOME_ENV: str(tmp_path)}, platform_name="linux")
    events: list[tuple[str, str]] = []
    speaking_states: list[bool] = []
    played = threading.Event()

    def _fake_synth(text: str, voice: str) -> np.ndarray:
        events.append((voice, text))
        return np.zeros(2400, dtype=np.float32)

    def _fake_play(samples: np.ndarray, sr: int, stop_event: threading.Event) -> None:
        assert sr == 24000
        assert samples.size == 2400
        played.set()

    speaker = WoofSpeaker(
        paths=paths,
        voice="mira",
        enabled=True,
        on_speaking_changed=speaking_states.append,
        synth_fn=_fake_synth,
        play_fn=_fake_play,
    )
    try:
        assert speaker.speak("Hello **world**") is True
        assert played.wait(timeout=2.0)
        deadline = time.monotonic() + 1.0
        while speaker.is_speaking and time.monotonic() < deadline:
            time.sleep(0.02)
        assert events == [("mira", "Hello world")]
        assert True in speaking_states
    finally:
        speaker.close()


def test_assistant_voice_route_and_listener_mute() -> None:
    class _FakeLLM:
        def answer_question(self, question: str, *, context: str = "") -> str:
            return f"Spoken answer for {question}."

    router = VoiceCommandRouter(pointer=DryRunPointerBackend(1920, 1080), llm_agent=_FakeLLM())  # type: ignore[arg-type]
    res = router.dispatch("ask what is my linux kernel")
    assert res.route == "assistant"
    assert res.detail == "Spoken answer for what is my linux kernel."
    assert res.payload.get("spoken") is True

    fake_stt = SimpleNamespace(device="cpu", compute_type="int8", close=lambda: None)
    listener = ActiveVoiceListener(stt_engine=fake_stt)  # type: ignore[arg-type]
    assert listener._muted is False
    listener.set_muted(True)
    assert listener._muted is True
