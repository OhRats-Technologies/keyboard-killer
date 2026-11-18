"""CUDA faster-whisper STT engine, evdev PTT monitor, and unified PipeWire audio loop."""

from __future__ import annotations

import ctypes
import glob
import logging
import os
import selectors
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import numpy as np
import sounddevice as sd
from evdev import InputDevice, ecodes

from keyboard_killer.audio.pop_detector import PopEvalResult, PopTriggerStateMachine

logger = logging.getLogger(__name__)

_CUDA_LIB_SEARCH_DIRS = (
    "/usr/local/lib/ollama/cuda_v12",
    "/usr/local/cuda/lib64",
    "/usr/local/cuda-12/lib64",
)


def _preload_cuda_libs() -> None:
    """Preload system/ollama CUDA 12 cuBLAS libraries so ctranslate2 finds them automatically."""
    loaded_names: set[str] = set()
    for base_dir in _CUDA_LIB_SEARCH_DIRS:
        if not os.path.isdir(base_dir):
            continue
        for lib_name in ("libcublasLt.so.12", "libcublas.so.12"):
            if lib_name in loaded_names:
                continue
            candidate = os.path.join(base_dir, lib_name)
            if os.path.exists(candidate):
                try:
                    ctypes.CDLL(candidate, mode=ctypes.RTLD_GLOBAL)
                    loaded_names.add(lib_name)
                except OSError as exc:
                    logger.debug("Could not preload %s: %s", candidate, exc)


def resolve_hotkey_code(key_name: str) -> int:
    """Resolve an evdev key name (e.g. `KEY_RIGHTCTRL`, `BTN_SIDE`) to its numeric code."""
    normalized = key_name.strip().upper()
    if not normalized.startswith(("KEY_", "BTN_")):
        normalized = f"KEY_{normalized}"
    code = getattr(ecodes, normalized, None)
    if code is None:
        raise ValueError(f"Unknown evdev hotkey name: {key_name!r}")
    return int(code)


class WhisperSTTEngine:
    """VRAM-resident faster-whisper speech-to-text engine."""

    def __init__(
        self,
        model_size: str = "base.en",
        device: str = "cuda",
        compute_type: str = "float16",
        language: str = "en",
    ) -> None:
        _preload_cuda_libs()
        from faster_whisper import WhisperModel

        self.model_size = model_size
        self.language = language
        self.device = device
        self.compute_type = compute_type

        try:
            self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        except Exception as exc:
            if device != "cpu":
                logger.warning("Whisper CUDA init failed (%s); falling back to CPU int8.", exc)
                self.device = "cpu"
                self.compute_type = "int8"
                self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
            else:
                raise

        # Warm up CUDA/cuBLAS kernels once at startup so first utterance is fast.
        self.transcribe(np.zeros(16000, dtype=np.float32))

    def transcribe(self, audio_f32: np.ndarray) -> str:
        """Transcribe 16kHz mono float32 audio into clean text."""
        arr = np.asarray(audio_f32, dtype=np.float32).reshape(-1)
        if arr.size < 2800:  # <175ms
            return ""
        if float(np.max(np.abs(arr))) < 1e-4:
            return ""
        segments, _ = self._model.transcribe(
            arr,
            language=self.language,
            beam_size=1,
            best_of=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()


class EvdevHotkeyMonitor:
    """Monitors physical `/dev/input/event*` devices for a Wayland-agnostic PTT key."""

    def __init__(
        self,
        hotkey_code: int,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        self.hotkey_code = int(hotkey_code)
        self._on_press = on_press
        self._on_release = on_release
        self._devices: list[InputDevice] = []
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, name="evdev-ptt-monitor", daemon=True)
        self._pressed = False

    def start(self) -> int:
        """Open matching readable input devices and start listening; returns device count."""
        for path in sorted(glob.glob("/dev/input/event*")):
            if not os.access(path, os.R_OK):
                continue
            try:
                dev = InputDevice(path)
            except OSError:
                continue
            if dev.name.startswith("keyboard-killer"):
                dev.close()
                continue
            caps = dev.capabilities()
            keys = caps.get(ecodes.EV_KEY, [])
            if self.hotkey_code in keys:
                self._devices.append(dev)
            else:
                dev.close()

        if self._devices:
            self._thread.start()
        return len(self._devices)

    def _run(self) -> None:
        sel = selectors.DefaultSelector()
        for dev in self._devices:
            sel.register(dev, selectors.EVENT_READ)
        try:
            while not self._stop_event.is_set():
                events = sel.select(timeout=0.1)
                for key, _ in events:
                    dev: InputDevice = key.fileobj  # type: ignore[assignment]
                    try:
                        for ev in dev.read():
                            if ev.type == ecodes.EV_KEY and ev.code == self.hotkey_code:
                                if ev.value == 1 and not self._pressed:
                                    self._pressed = True
                                    self._on_press()
                                elif ev.value == 0 and self._pressed:
                                    self._pressed = False
                                    self._on_release()
                    except OSError:
                        continue
        finally:
            sel.close()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        for dev in self._devices:
            try:
                dev.close()
            except OSError:
                pass
        self._devices.clear()


class UnifiedAudioService:
    """Single 16kHz PipeWire stream feeding both PopDetector and PTT Whisper capture."""

    def __init__(
        self,
        pop_detector: PopTriggerStateMachine,
        on_pop: Callable[[], None],
        on_utterance: Callable[[np.ndarray], None] | None = None,
        on_eval: Callable[[PopEvalResult], None] | None = None,
        device: int | str | None = None,
        sample_rate: int = 16000,
        chunk_samples: int = 256,
        pre_roll_chunks: int = 10,
        release_unmute_delay_sec: float = 0.10,
    ) -> None:
        self.pop_detector = pop_detector
        self._on_pop = on_pop
        self._on_utterance = on_utterance
        self._on_eval = on_eval
        self.device = device
        self.sample_rate = int(sample_rate)
        self.chunk_samples = int(chunk_samples)
        self.release_unmute_delay_sec = float(release_unmute_delay_sec)

        self._lock = threading.Lock()
        self._recording_ptt = False
        self._pre_roll: deque[np.ndarray] = deque(maxlen=pre_roll_chunks)
        self._ptt_chunks: list[np.ndarray] = []
        self._unmute_timer: threading.Timer | None = None
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.chunk_samples,
            device=self.device,
            latency="low",
            callback=self._audio_callback,
        )
        self._stream.start()

    def begin_ptt(self) -> None:
        """Engage PTT mute lock on PopDetector and begin buffering speech + pre-roll."""
        with self._lock:
            if self._unmute_timer is not None:
                self._unmute_timer.cancel()
                self._unmute_timer = None
            self.pop_detector.set_muted(True)
            self._ptt_chunks = list(self._pre_roll)
            self._recording_ptt = True

    def end_ptt(self) -> None:
        """Finish PTT recording, dispatch captured audio, and unmute pop detector after key-snap debounce."""
        with self._lock:
            self._recording_ptt = False
            chunks = self._ptt_chunks
            self._ptt_chunks = []

            def _unmute() -> None:
                self.pop_detector.set_muted(False)

            self._unmute_timer = threading.Timer(self.release_unmute_delay_sec, _unmute)
            self._unmute_timer.daemon = True
            self._unmute_timer.start()

        if chunks and self._on_utterance is not None:
            audio = np.concatenate(chunks)
            threading.Thread(
                target=self._on_utterance,
                args=(audio,),
                name="stt-worker",
                daemon=True,
            ).start()

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        _time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logger.debug("Audio callback status: %s", status)
        chunk = indata[:, 0].copy()
        res = self.pop_detector.process_chunk(chunk)
        if self._on_eval is not None:
            self._on_eval(res)
        if res.triggered:
            self._on_pop()

        with self._lock:
            if self._recording_ptt:
                self._ptt_chunks.append(chunk)
            else:
                self._pre_roll.append(chunk)

    def stop(self) -> None:
        with self._lock:
            if self._unmute_timer is not None:
                self._unmute_timer.cancel()
                self._unmute_timer = None
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
