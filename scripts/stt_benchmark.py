"""Compare STT engines on WAV command recordings (accuracy + latency).

Manifest format: ``[{"wav": "path", "text": "expected words"}, ...]``.

Usage:
  uv run python scripts/stt_benchmark.py /tmp/bench/manifest.json --engine parakeet
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import wave
from pathlib import Path


def _read_wav(path: Path) -> tuple[object, int]:
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM")
        frames = handle.readframes(handle.getnframes())
        rate = handle.getframerate()
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    return audio, rate


def _normalize(text: str) -> str:
    import re

    cleaned = re.sub(r"[^a-z0-9' ]+", " ", text.lower())
    return " ".join(cleaned.split())


def _edit_distance(a: list[str], b: list[str]) -> int:
    previous = list(range(len(b) + 1))
    for i, token_a in enumerate(a, start=1):
        current = [i]
        for j, token_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (token_a != token_b),
                )
            )
        previous = current
    return previous[-1]


def _score(expected: str, actual: str) -> tuple[bool, float]:
    exp = _normalize(expected).split()
    act = _normalize(actual).split()
    exact = exp == act
    wer = _edit_distance(exp, act) / max(1, len(exp))
    return exact, wer


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[idx]


def run_engine(name: str, samples: list[dict[str, object]], config) -> dict[str, object]:
    from keyboard_killer.voice.stt.factory import create_stt_engine

    selection = create_stt_engine(config)
    engine = selection.engine
    status = engine.status()
    results = []
    latencies: list[float] = []
    durations: list[float] = []
    exact_hits = 0
    wer_total = 0.0
    for sample in samples:
        audio, rate = _read_wav(Path(str(sample["wav"])))
        if rate != 16000:
            raise ValueError(f"{sample['wav']}: expected 16 kHz, got {rate}")
        started = time.perf_counter()
        text = engine.transcribe(audio)
        latency_ms = (time.perf_counter() - started) * 1000.0
        exact, wer = _score(str(sample["text"]), text)
        exact_hits += int(exact)
        wer_total += wer
        latencies.append(latency_ms)
        durations.append(len(audio) / rate)
        results.append(
            {
                "id": sample.get("id", Path(str(sample["wav"])).stem),
                "expected": sample["text"],
                "actual": text,
                "exact": exact,
                "wer": wer,
                "latency_ms": latency_ms,
                "audio_s": len(audio) / rate,
            }
        )
    engine.close()
    total_audio = sum(durations)
    report = {
        "engine": name,
        "status": status.as_dict(),
        "fallback_reason": selection.fallback_reason,
        "samples": len(samples),
        "exact_match": exact_hits / max(1, len(samples)),
        "mean_wer": wer_total / max(1, len(samples)),
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else 0.0,
            "median": statistics.median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
            "min": min(latencies) if latencies else 0.0,
            "max": max(latencies) if latencies else 0.0,
        },
        "audio_s": total_audio,
        "real_time_factor": (sum(latencies) / 1000.0) / total_audio if total_audio else 0.0,
        "results": results,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--engine", choices=["parakeet"], default="parakeet")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    from keyboard_killer.config import AppConfig

    samples = json.loads(args.manifest.read_text())
    if not isinstance(samples, list) or not samples:
        raise SystemExit("Manifest must be a non-empty list")

    reports = []
    for engine_name in [args.engine]:
        config = AppConfig(stt_provider="cpu")
        print(f"Loading {engine_name}...")
        report = run_engine(engine_name, samples, config)
        reports.append(report)
        print(
            f"{engine_name:9s} exact={report['exact_match']:.1%} "
            f"WER={report['mean_wer']:.3f} "
            f"median={report['latency_ms']['median']:.0f}ms "
            f"p95={report['latency_ms']['p95']:.0f}ms "
            f"RTF={report['real_time_factor']:.3f}"
        )
        for item in report["results"]:
            mark = "OK " if item["exact"] else "ERR"
            print(f"  {mark} {item['id']}: {item['actual']!r} (expected {item['expected']!r})")

    payload = {"label": args.label, "manifest": str(args.manifest), "reports": reports}
    output = args.output or args.manifest.with_name(args.manifest.stem + "-results.json")
    output.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
