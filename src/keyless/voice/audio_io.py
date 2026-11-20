"""PortAudio capture loop shared by pop detection and push-to-talk STT."""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

from keyless.audio.pop_detector import PopEvalResult, PopTriggerStateMachine

logger = logging.getLogger(__name__)


class UnifiedAudioService:
    """Single 16kHz PipeWire stream feeding both PopDetector and PTT speech capture."""

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
        self._stream: Any = None

    def start(self) -> None:
        import sounddevice as sd

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
        status: Any,
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
