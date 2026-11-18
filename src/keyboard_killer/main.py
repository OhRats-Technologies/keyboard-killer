"""CLI entrypoint for keyboard-killer (Phase 1 vision, calibration, and diagnostics)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import cv2
import numpy as np
import typer
from rich.console import Console
from rich.table import Table

from keyboard_killer.config import (
    DEFAULT_CALIB_PATH,
    DEFAULT_CONFIG_PATH,
    AppConfig,
    discover_v4l_by_id,
    get_display_layout,
)
from keyboard_killer.daemon import run_combined_daemon
from keyboard_killer.vision.calibrator import run_calibration_ui
from keyboard_killer.vision.landmarks import CameraObservation
from keyboard_killer.vision.stereo_capture import StereoCapture

app = typer.Typer(
    name="keyboard-killer",
    help="Dual-camera stereo gaze tracking, pop-click detection, and voice/AI control.",
    no_args_is_help=True,
)
console = Console()


def _draw_overlay(
    frame: np.ndarray,
    obs: CameraObservation | None,
    label: str,
    fps: float,
    skew_ms: float,
) -> np.ndarray:
    out = frame.copy()
    luma = float(frame.mean())
    cv2.putText(
        out,
        f"{label} | {fps:.1f} fps | luma {luma:.0f} | skew {skew_ms:.1f} ms",
        (16, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.70,
        (80, 255, 120) if obs is not None else (80, 140, 255),
        2,
        cv2.LINE_AA,
    )
    if obs is None:
        cv2.putText(
            out,
            "NO FACE DETECTED",
            (16, 68),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (80, 80, 255),
            2,
            cv2.LINE_AA,
        )
        return out

    for pt in obs.eye_corners_px:
        cv2.circle(out, pt, 3, (255, 220, 0), -1, cv2.LINE_AA)
    cv2.circle(out, obs.iris_a_px, 4, (0, 255, 120), -1, cv2.LINE_AA)
    cv2.circle(out, obs.iris_b_px, 4, (0, 255, 120), -1, cv2.LINE_AA)
    cv2.circle(out, obs.nose_px, 5, (0, 165, 255), -1, cv2.LINE_AA)

    cv2.putText(
        out,
        f"irisA=({obs.iris_a_dx:+.2f},{obs.iris_a_dy:+.2f}) "
        f"irisB=({obs.iris_b_dx:+.2f},{obs.iris_b_dy:+.2f}) "
        f"yaw={obs.yaw:+.2f} blink={obs.max_blink:.2f}",
        (16, 68),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (240, 240, 240),
        1,
        cv2.LINE_AA,
    )
    return out


@app.command("check-system")
def check_system() -> None:
    """Inspect deterministic /dev/v4l/by-id/ cameras, /dev/uinput access, and Mutter layout."""
    cfg = AppConfig.load()
    layout = get_display_layout()
    by_ids = discover_v4l_by_id()

    console.print("[bold]Detected /dev/v4l/by-id/ capture devices:[/bold]")
    for dev in by_ids:
        console.print(f"  • {dev} -> {os.path.realpath(dev)}")
    if not by_ids:
        console.print("  [red]No /dev/v4l/by-id/*-video-index0 devices found.[/red]")

    table = Table(title="Configured Camera Bindings")
    table.add_column("Slot")
    table.add_column("By-ID Path")
    table.add_column("Status")
    for slot, path in [("cam1 (C270)", cfg.cam1_by_id), ("cam2 (CAM 313)", cfg.cam2_by_id)]:
        exists = Path(path).exists()
        status = f"[green]OK ({os.path.realpath(path)})[/green]" if exists else "[red]MISSING[/red]"
        table.add_row(slot, path, status)
    console.print(table)

    mon_table = Table(title=f"GNOME Mutter Layout (Total {layout.total_width}x{layout.total_height})")
    mon_table.add_column("Connector")
    mon_table.add_column("Origin")
    mon_table.add_column("Logical Size")
    mon_table.add_column("Scale")
    mon_table.add_column("Primary")
    for m in layout.monitors:
        mon_table.add_row(
            m.connector,
            f"({m.x}, {m.y})",
            f"{m.width}x{m.height}",
            f"{m.scale:.2f}",
            "yes" if m.is_primary else "no",
        )
    console.print(mon_table)

    uinput_ok = os.access("/dev/uinput", os.W_OK)
    if uinput_ok:
        console.print("[green]/dev/uinput is writable.[/green]")
    else:
        console.print(
            "[yellow]/dev/uinput is not writable by current user. "
            "Run `sudo ./scripts/setup_uinput_udev.sh` before `run-vision`.[/yellow]"
        )


@app.command("test-cams")
def test_cams(
    exp1: int | None = typer.Option(None, help="Cam1 (C270) manual exposure"),
    gain1: int | None = typer.Option(None, help="Cam1 (C270) sensor gain (0-255)"),
    exp2: int | None = typer.Option(None, help="Cam2 (CAM 313) manual exposure"),
    gain2: int | None = typer.Option(None, help="Cam2 (CAM 313) sensor gain (16-255)"),
    headless: bool = typer.Option(False, help="Run without opening a GUI preview window"),
    seconds: float = typer.Option(0.0, help="Auto-stop after N seconds (0 = until 'q'/ESC)"),
) -> None:
    """Verify locked 30Hz dual-camera MJPG capture and MediaPipe iris/head landmarks."""
    cfg = AppConfig.load()
    if exp1 is not None:
        cfg.cam1_exposure = exp1
    if gain1 is not None:
        cfg.cam1_gain = gain1
    if exp2 is not None:
        cfg.cam2_exposure = exp2
    if gain2 is not None:
        cfg.cam2_gain = gain2

    try:
        with StereoCapture(
            cam1_by_id=cfg.cam1_by_id,
            cam2_by_id=cfg.cam2_by_id,
            width=cfg.frame_width,
            height=cfg.frame_height,
            fps=cfg.fps,
            cam1_exposure=cfg.cam1_exposure,
            cam1_gain=cfg.cam1_gain,
            cam2_exposure=cfg.cam2_exposure,
            cam2_gain=cfg.cam2_gain,
            extract_landmarks=True,
            use_gpu=cfg.use_gpu,
            keep_images=True,
        ) as stereo:
            # Wait briefly for initial frames so delegates initialize before printing
            stereo.read_pair(timeout_s=2.0)
            for idx, (rep, delg) in enumerate(zip(stereo.reports, stereo.delegates), start=1):
                console.print(
                    f"[cyan]Cam{idx}[/cyan] [{delg}] {rep.resolved_dev} ({rep.fourcc} {rep.width}x{rep.height} "
                    f"@ {rep.fps:.1f}fps) | locked={rep.applied} | unsupported={rep.unsupported}"
                )

            start_t = time.monotonic()
            last_seq1 = -1
            last_seq2 = -1
            frames_checked = 0
            faces_cam1 = 0
            faces_cam2 = 0
            last_fps1 = 0.0
            last_fps2 = 0.0
            last_luma1 = 0.0
            last_luma2 = 0.0

            while True:
                if seconds > 0 and (time.monotonic() - start_t) >= seconds:
                    break
                pair = stereo.read_pair(timeout_s=1.0)
                if pair is None or pair.cam1.image is None or pair.cam2.image is None:
                    continue
                if pair.cam1.seq == last_seq1 and pair.cam2.seq == last_seq2:
                    time.sleep(0.004)
                    continue
                last_seq1 = pair.cam1.seq
                last_seq2 = pair.cam2.seq
                frames_checked += 1
                last_fps1 = pair.cam1.measured_fps
                last_fps2 = pair.cam2.measured_fps
                last_luma1 = float(pair.cam1.image.mean())
                last_luma2 = float(pair.cam2.image.mean())

                obs1 = pair.cam1.observation
                obs2 = pair.cam2.observation
                if obs1 is not None:
                    faces_cam1 += 1
                if obs2 is not None:
                    faces_cam2 += 1

                if not headless:
                    v1 = _draw_overlay(
                        pair.cam1.image, obs1, "Cam1 (C270)", pair.cam1.measured_fps, pair.skew_ms
                    )
                    v2 = _draw_overlay(
                        pair.cam2.image, obs2, "Cam2 (CAM 313)", pair.cam2.measured_fps, pair.skew_ms
                    )
                    v1_s = cv2.resize(v1, (800, 450))
                    v2_s = cv2.resize(v2, (800, 450))
                    combined = np.hstack([v1_s, v2_s])
                    cv2.imshow("Keyboard-Killer Dual-Cam Diagnostic (q/ESC to quit)", combined)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (27, ord("q")):
                        break

            console.print(
                f"Checked {frames_checked} paired frames | "
                f"measured fps: cam1={last_fps1:.2f}, cam2={last_fps2:.2f} | "
                f"luma: cam1={last_luma1:.1f}, cam2={last_luma2:.1f} | "
                f"faces: cam1={faces_cam1}/{frames_checked}, cam2={faces_cam2}/{frames_checked}"
            )
    finally:
        if not headless:
            cv2.destroyAllWindows()


@app.command("calibrate")
def calibrate(
    samples: int = typer.Option(22, help="Valid frames to capture per static anchor dot"),
    exp1: int | None = typer.Option(None, help="Cam1 manual exposure"),
    gain1: int | None = typer.Option(None, help="Cam1 sensor gain"),
    exp2: int | None = typer.Option(None, help="Cam2 manual exposure"),
    gain2: int | None = typer.Option(None, help="Cam2 sensor gain"),
    connector: str | None = typer.Option(None, help="Target monitor connector (default: primary)"),
    output: Path = typer.Option(DEFAULT_CALIB_PATH, help="Output calibration JSON path"),
) -> None:
    """Run hybrid stereo calibration (anchors + gliding sweeps + Left-Click pursuit trainer)."""
    cfg = AppConfig.load()
    if exp1 is not None:
        cfg.cam1_exposure = exp1
    if gain1 is not None:
        cfg.cam1_gain = gain1
    if exp2 is not None:
        cfg.cam2_exposure = exp2
    if gain2 is not None:
        cfg.cam2_gain = gain2
    cfg.save(DEFAULT_CONFIG_PATH)

    model = run_calibration_ui(
        config=cfg,
        calib_path=output,
        samples_per_point=samples,
        connector=connector,
    )
    console.print(
        f"[green]Calibration saved to {output}[/green] | "
        f"Train RMSE: X={model.rmse_x:.1f}px, Y={model.rmse_y:.1f}px | "
        f"CV RMSE: X={model.cv_rmse_x:.1f}px, Y={model.cv_rmse_y:.1f}px | "
        f"samples={model.n_train_samples}, rbf_centers={len(model.rbf_centers)}"
    )


def _apply_runtime_overrides(
    cfg: AppConfig,
    saccade_px: float | None = None,
    saccade_ms: float | None = None,
    gimbal_gain: float | None = None,
    exp1: int | None = None,
    gain1: int | None = None,
    exp2: int | None = None,
    gain2: int | None = None,
    hotkey: str | None = None,
    whisper_model: str | None = None,
    llm_backend: str | None = None,
    vlm_model: str | None = None,
    no_vlm: bool = False,
    ptt: bool = False,
    pop_click: bool | None = None,
    eye_mouse: bool | None = None,
    firefox_port: int | None = None,
    no_firefox_bridge: bool = False,
    pop_peak: float | None = None,
    pop_rms: float | None = None,
) -> AppConfig:
    if saccade_px is not None:
        cfg.saccade_threshold_px = saccade_px
    if saccade_ms is not None:
        cfg.saccade_hold_ms = saccade_ms
    if gimbal_gain is not None:
        cfg.gimbal_gain = gimbal_gain
    if exp1 is not None:
        cfg.cam1_exposure = exp1
    if gain1 is not None:
        cfg.cam1_gain = gain1
    if exp2 is not None:
        cfg.cam2_exposure = exp2
    if gain2 is not None:
        cfg.cam2_gain = gain2
    if hotkey is not None:
        cfg.ptt_hotkey = hotkey
    if whisper_model is not None:
        cfg.whisper_model = whisper_model
    if llm_backend is not None:
        cfg.llm_backend = llm_backend
    if vlm_model is not None:
        cfg.gaze_vlm_model = vlm_model
    if no_vlm:
        cfg.gaze_vlm_enabled = False
    if ptt:
        cfg.always_on_voice = False
    if pop_click is not None:
        cfg.enable_pop_click = pop_click
    if eye_mouse is not None:
        cfg.gaze_cursor_control = eye_mouse
    if firefox_port is not None:
        cfg.firefox_bridge_port = firefox_port
    if no_firefox_bridge:
        cfg.firefox_bridge_enabled = False
    if pop_peak is not None:
        cfg.pop_min_peak = pop_peak
    if pop_rms is not None:
        cfg.pop_min_rms = pop_rms
    return cfg


@app.command("run")
def run_all(
    calib_path: Path = typer.Option(DEFAULT_CALIB_PATH, help="Stereo calibration JSON path"),
    saccade_px: float | None = typer.Option(None, help="Saccade jump threshold in px"),
    saccade_ms: float | None = typer.Option(None, help="Saccade confirmation hold in ms"),
    gimbal_gain: float | None = typer.Option(None, help="Head-gimbal sensitivity multiplier"),
    exp1: int | None = typer.Option(None, help="Cam1 manual exposure"),
    gain1: int | None = typer.Option(None, help="Cam1 sensor gain"),
    exp2: int | None = typer.Option(None, help="Cam2 manual exposure"),
    gain2: int | None = typer.Option(None, help="Cam2 sensor gain"),
    hotkey: str | None = typer.Option(None, help="Evdev PTT hotkey (default KEY_RIGHTCTRL)"),
    whisper_model: str | None = typer.Option(None, help="faster-whisper model (default base.en)"),
    llm_backend: str | None = typer.Option(None, help="LLM backend: auto | ollama | gemini"),
    vlm_model: str | None = typer.Option(None, help="Ollama vision grounding model (default gemma4:e4b)"),
    no_vlm: bool = typer.Option(False, help="Disable gaze-foveated OCR/VLM voice clicking"),
    ptt: bool = typer.Option(False, help="Use Push-to-Talk hotkey instead of always-on VAD"),
    pop_click: bool | None = typer.Option(
        None,
        "--pop-click/--no-pop-click",
        help="Enable/disable 75ms speech-vetoed acoustic pop click",
    ),
    eye_mouse: bool | None = typer.Option(
        None,
        "--eye-mouse/--no-eye-mouse",
        help="Let gaze drive OS cursor (default: internal gaze guide only)",
    ),
    firefox_port: int | None = typer.Option(None, help="Firefox DOM WebSocket bridge port (default 8765)"),
    no_firefox_bridge: bool = typer.Option(False, help="Disable Tier-0 Firefox WebSocket DOM bridge"),
    pop_peak: float | None = typer.Option(None, help="Pop detector peak threshold"),
    pop_rms: float | None = typer.Option(None, help="Pop detector RMS threshold"),
    dictate_unprefixed: bool = typer.Option(False, help="Type unprefixed speech directly"),
    no_vision: bool = typer.Option(False, help="Disable dual-camera gaze tracking"),
    no_audio: bool = typer.Option(False, help="Disable pop-click & voice STT"),
    dry_run: bool = typer.Option(False, help="Run without emitting /dev/uinput events"),
    seconds: float | None = typer.Option(None, help="Optional run duration in seconds"),
) -> None:
    """Run the combined Dual-Cam Gaze + Always-On VAD/VLM Voice daemon."""
    if not no_vision and not calib_path.exists():
        console.print(f"[red]Calibration file not found: {calib_path}[/red]")
        console.print("Run `uv run keyboard-killer calibrate` first (or pass --no-vision).")
        raise typer.Exit(code=1)

    cfg = _apply_runtime_overrides(
        AppConfig.load(),
        saccade_px=saccade_px,
        saccade_ms=saccade_ms,
        gimbal_gain=gimbal_gain,
        exp1=exp1,
        gain1=gain1,
        exp2=exp2,
        gain2=gain2,
        hotkey=hotkey,
        whisper_model=whisper_model,
        llm_backend=llm_backend,
        vlm_model=vlm_model,
        no_vlm=no_vlm,
        ptt=ptt,
        pop_click=pop_click,
        eye_mouse=eye_mouse,
        firefox_port=firefox_port,
        no_firefox_bridge=no_firefox_bridge,
        pop_peak=pop_peak,
        pop_rms=pop_rms,
    )
    run_combined_daemon(
        config=cfg,
        calib_path=calib_path,
        enable_vision=not no_vision,
        enable_audio=not no_audio,
        dictate_unprefixed=dictate_unprefixed,
        dry_run=dry_run,
        duration_sec=seconds,
        console=console,
    )


@app.command("run-vision")
def run_vision(
    calib_path: Path = typer.Option(DEFAULT_CALIB_PATH, help="Stereo calibration JSON path"),
    saccade_px: float | None = typer.Option(None, help="Saccade jump threshold in px (default 110)"),
    saccade_ms: float | None = typer.Option(None, help="Saccade confirmation hold in ms (default 16)"),
    gimbal_gain: float | None = typer.Option(None, help="Head-gimbal sensitivity multiplier"),
    exp1: int | None = typer.Option(None, help="Cam1 manual exposure"),
    gain1: int | None = typer.Option(None, help="Cam1 sensor gain"),
    exp2: int | None = typer.Option(None, help="Cam2 manual exposure"),
    gain2: int | None = typer.Option(None, help="Cam2 sensor gain"),
    eye_mouse: bool = typer.Option(
        True,
        "--eye-mouse/--no-eye-mouse",
        help="Drive OS cursor with gaze+gimbal (default True for run-vision)",
    ),
    dry_run: bool = typer.Option(False, help="Run without writing to /dev/uinput"),
    seconds: float | None = typer.Option(None, help="Optional run duration in seconds"),
) -> None:
    """Run only the 30Hz dual-camera stereo gaze + 60Hz head-gimbal cursor loop."""
    if not calib_path.exists():
        console.print(f"[red]Calibration file not found: {calib_path}[/red]")
        console.print("Run `uv run keyboard-killer calibrate` first.")
        raise typer.Exit(code=1)

    cfg = _apply_runtime_overrides(
        AppConfig.load(),
        saccade_px=saccade_px,
        saccade_ms=saccade_ms,
        gimbal_gain=gimbal_gain,
        exp1=exp1,
        gain1=gain1,
        exp2=exp2,
        gain2=gain2,
        eye_mouse=eye_mouse,
    )
    run_combined_daemon(
        config=cfg,
        calib_path=calib_path,
        enable_vision=True,
        enable_audio=False,
        dry_run=dry_run,
        duration_sec=seconds,
        console=console,
    )


@app.command("test-audio")
def test_audio(
    seconds: float = typer.Option(5.0, help="Seconds to monitor live mic for pops & plosive rejection"),
    min_peak: float | None = typer.Option(None, help="Minimum high-pass peak threshold (default 0.25)"),
    min_rms: float | None = typer.Option(None, help="Minimum chunk RMS threshold (default 0.032)"),
    transcribe: bool = typer.Option(False, help="Also test CUDA faster-whisper transcription on captured audio"),
) -> None:
    """Test PipeWire mic stream, plosive-gated lip-pop detector, and optional Whisper CUDA STT."""
    import numpy as np

    from keyboard_killer.audio.pop_detector import PopEvalResult, PopTriggerStateMachine
    from keyboard_killer.audio.stt_engine import UnifiedAudioService, WhisperSTTEngine

    cfg = AppConfig.load()
    pop = PopTriggerStateMachine(
        min_peak=min_peak if min_peak is not None else cfg.pop_min_peak,
        min_rms=min_rms if min_rms is not None else cfg.pop_min_rms,
    )
    recorded: list[np.ndarray] = []
    max_stats = {"peak": 0.0, "rms": 0.0}

    def _on_pop() -> None:
        console.print("[bold green]POP! -> click triggered[/bold green]")

    def _on_eval(res: PopEvalResult) -> None:
        if res.peak > max_stats["peak"]:
            max_stats["peak"] = res.peak
        if res.rms > max_stats["rms"]:
            max_stats["rms"] = res.rms
        if res.rejected_plosive:
            console.print(f"[yellow]Rejected plosive/speech[/yellow] (rms={res.rms:.3f})")

    svc = UnifiedAudioService(
        pop_detector=pop,
        on_pop=_on_pop,
        on_eval=_on_eval,
        device=cfg.audio_device,
    )
    if transcribe:
        svc.begin_ptt()
        svc.pop_detector.set_muted(False)

    console.print(
        f"Listening on mic for {seconds:.1f}s (min_peak={pop.min_peak:.2f}, min_rms={pop.min_rms:.3f})..."
    )
    svc.start()
    try:
        t0 = time.monotonic()
        while (time.monotonic() - t0) < seconds:
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if transcribe:
            recorded = list(svc._ptt_chunks)
        svc.stop()

    console.print(
        f"Pop triggers: [green]{pop.total_triggers}[/green] | "
        f"Rejected plosives: [yellow]{pop.total_rejected_plosives}[/yellow] | "
        f"Max peak={max_stats['peak']:.3f}, Max rms={max_stats['rms']:.3f}, Floor={pop._noise_floor:.4f}"
    )
    if transcribe and recorded:
        console.print(f"Loading faster-whisper ({cfg.whisper_model}) on CUDA...")
        stt = WhisperSTTEngine(model_size=cfg.whisper_model)
        text = stt.transcribe(np.concatenate(recorded))
        console.print(f"STT ({stt.device}/{stt.compute_type}): [cyan]{text!r}[/cyan]")


@app.command("run-voice")
def run_voice(
    hotkey: str | None = typer.Option(None, help="Evdev PTT hotkey (default KEY_RIGHTCTRL)"),
    whisper_model: str | None = typer.Option(None, help="faster-whisper model (default base.en)"),
    llm_backend: str | None = typer.Option(None, help="LLM backend: auto | ollama | gemini"),
    pop_peak: float | None = typer.Option(None, help="Pop detector peak threshold (default 0.25)"),
    pop_rms: float | None = typer.Option(None, help="Pop detector RMS threshold (default 0.032)"),
    dictate_unprefixed: bool = typer.Option(False, help="Type unprefixed speech directly"),
    dry_run: bool = typer.Option(False, help="Log actions without emitting /dev/uinput events"),
    seconds: float | None = typer.Option(None, help="Optional run duration in seconds"),
) -> None:
    """Run only the sub-45ms pop-click + evdev PTT faster-whisper voice router."""
    cfg = _apply_runtime_overrides(
        AppConfig.load(),
        hotkey=hotkey,
        whisper_model=whisper_model,
        llm_backend=llm_backend,
        pop_peak=pop_peak,
        pop_rms=pop_rms,
    )
    run_combined_daemon(
        config=cfg,
        calib_path=None,
        enable_vision=False,
        enable_audio=True,
        dictate_unprefixed=dictate_unprefixed,
        dry_run=dry_run,
        duration_sec=seconds,
        console=console,
    )


if __name__ == "__main__":
    app()
