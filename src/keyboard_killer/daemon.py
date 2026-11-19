"""Combined dual-camera gaze, pop-click, and voice-routing daemon."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
from evdev import ecodes
from rich.console import Console

from keyboard_killer.audio.active_listener import ActiveVoiceListener
from keyboard_killer.audio.pop_detector import PopTriggerStateMachine
from keyboard_killer.audio.stt_engine import UnifiedAudioService
from keyboard_killer.config import AppConfig, get_display_layout
from keyboard_killer.input.click_harvest import EvdevMouseClickHarvester
from keyboard_killer.input.pointer import DryRunPointerBackend, PointerBackend
from keyboard_killer.platform.linux.hotkeys import EvdevHotkeyMonitor, resolve_hotkey_code
from keyboard_killer.platform.linux.pointer import UInputManager
from keyboard_killer.ipc.firefox_bridge import FirefoxDOMBridge
from keyboard_killer.ipc.state import FLAG_FACE_DETECTED, SharedGazeReader
from keyboard_killer.router.dispatcher import VoiceCommandRouter
from keyboard_killer.router.gaze_vlm_clicker import GazeVLMClicker
from keyboard_killer.routing.llm import LLMAgent
from keyboard_killer.voice.stt.factory import create_stt_engine
from keyboard_killer.vision.calibrator import (
    StereoGazeModel,
    append_harvested_calibration_samples,
)
from keyboard_killer.vision.cursor_gimbal import (
    GazeGimbalController,
    GimbalStepResult,
    run_vision_loop,
)
from keyboard_killer.platform.linux.accessibility import LinuxDesktopAccessibilityBridge
from keyboard_killer.platform.linux.screen import monitor_at_point
from keyboard_killer.vision.online_learner import OnlineGazeLearner
from keyboard_killer.vision.ui_detector import GazeUIDetector


def run_combined_daemon(
    config: AppConfig,
    calib_path: Path | None = None,
    enable_vision: bool = True,
    enable_audio: bool = True,
    dictate_unprefixed: bool = False,
    dry_run: bool = False,
    duration_sec: float | None = None,
    console: Console | None = None,
) -> None:
    """Run vision gaze tracking, acoustic pop-click, and voice STT in one process."""
    out = console or Console()
    if not enable_vision and not enable_audio:
        raise ValueError("At least one of vision or audio must be enabled.")

    layout = get_display_layout()
    model: StereoGazeModel | None = None
    if enable_vision:
        if calib_path is None or not calib_path.exists():
            raise FileNotFoundError(
                f"Calibration file not found: {calib_path}. Run `uv run keyboard-killer calibrate` first."
            )
        model = StereoGazeModel.load(calib_path, strip_pose_translation=True)
    pointer: PointerBackend = (
        DryRunPointerBackend(layout.total_width, layout.total_height)
        if dry_run
        else UInputManager(layout.total_width, layout.total_height)
    )

    latest_gaze_lock = threading.Lock()
    latest_gaze: tuple[float, float] | None = None
    latest_vlm_state: tuple[float, float, np.ndarray | None] | None = None
    latest_attention: tuple[bool, float] = (False, 0.0)
    active_controller: GazeGimbalController | None = None
    firefox_bridge: FirefoxDOMBridge | None = None

    def _on_vision_step(step: GimbalStepResult) -> None:
        nonlocal latest_gaze, latest_vlm_state, latest_attention
        with latest_gaze_lock:
            latest_gaze = (step.cursor_x, step.cursor_y)
            latest_vlm_state = (step.gaze_x, step.gaze_y, step.stereo_feat)
            latest_attention = (bool(step.flags & FLAG_FACE_DETECTED), float(step.yaw_deg))
        if firefox_bridge is not None and firefox_bridge.is_connected:
            mon = monitor_at_point(layout, step.gaze_x, step.gaze_y)
            firefox_bridge.broadcast_gaze(
                step.gaze_x,
                step.gaze_y,
                mon_x=mon.x,
                mon_y=mon.y,
                mon_w=mon.width,
                mon_h=mon.height,
            )

    def _inproc_gaze() -> tuple[float, float] | None:
        with latest_gaze_lock:
            return latest_gaze

    def _inproc_vlm_state() -> tuple[float, float, np.ndarray | None] | None:
        with latest_gaze_lock:
            return latest_vlm_state

    def _is_user_attending() -> bool:
        with latest_gaze_lock:
            face_visible, yaw_deg = latest_attention
        return face_visible and abs(yaw_deg) < config.attention_max_yaw_deg

    def _on_controller_ready(ctrl: GazeGimbalController) -> None:
        nonlocal active_controller
        active_controller = ctrl

    def _on_before_click(tx: int, ty: int) -> None:
        if active_controller is not None:
            active_controller.warp_and_hold(float(tx), float(ty), hold_sec=0.25)

    gaze_reader: SharedGazeReader | None = None
    audio_svc: UnifiedAudioService | None = None
    ptt_mon: EvdevHotkeyMonitor | None = None
    active_listener: ActiveVoiceListener | None = None
    stt = None
    event_stop = threading.Event()
    event_thread: threading.Thread | None = None
    vlm_clicker: GazeVLMClicker | None = None
    mouse_harvester: EvdevMouseClickHarvester | None = None

    def _current_gaze_xy_fallback() -> tuple[float, float]:
        st = _inproc_vlm_state()
        if st is not None:
            return float(st[0]), float(st[1])
        gz = _inproc_gaze()
        if gz is not None:
            return float(gz[0]), float(gz[1])
        mon = layout.primary
        return float(mon.x + mon.width * 0.5), float(mon.y + mon.height * 0.5)

    try:
        # Automatic online learning and passive click harvesting are disabled by default so
        # look-then-speak saccades never degrade calibration. Use "recenter gaze" for manual snap.
        if config.firefox_bridge_enabled:
            firefox_bridge = FirefoxDOMBridge(port=config.firefox_bridge_port)
            firefox_bridge.start()
            out.print(
                f"[green]Firefox DOM bridge listening on ws://127.0.0.1:{firefox_bridge.bound_port}[/green]"
            )

        if enable_audio:
            gaze_provider: Callable[[], tuple[float, float] | None] | None = (
                _inproc_gaze if enable_vision else None
            )
            if not enable_vision:
                try:
                    gaze_reader = SharedGazeReader()
                    out.print("[green]Attached to external gaze shared memory.[/green]")
                except FileNotFoundError:
                    out.print("[dim]Vision loop not active; gaze coordinates unavailable.[/dim]")

            if config.gaze_vlm_enabled:
                detector = GazeUIDetector()
                desktop_atspi = LinuxDesktopAccessibilityBridge()

                vlm_clicker = GazeVLMClicker(
                    detector=detector,
                    model_name=config.gaze_vlm_model,
                    layout=layout,
                    crop_w=config.gaze_crop_w,
                    crop_h=config.gaze_crop_h,
                    keyword_threshold=config.gaze_keyword_threshold,
                    firefox_bridge=firefox_bridge,
                    desktop_atspi=desktop_atspi,
                )
                def _warm_vlm() -> None:
                    assert vlm_clicker is not None
                    if not vlm_clicker.warm_model_in_vram():
                        out.print(
                            f"[yellow]Warning: Ollama '{config.gaze_vlm_model}' warmup failed "
                            "(check `ollama.service` / OLLAMA_FLASH_ATTENTION=0). "
                            "Tier-0 DOM & Tier-1 OCR still active.[/yellow]"
                        )

                threading.Thread(
                    target=_warm_vlm,
                    name="vlm-warmup",
                    daemon=True,
                ).start()

            llm = LLMAgent(
                backend=config.llm_backend,
                ollama_model=config.ollama_model,
                gemini_model=config.gemini_model,
            )
            def _on_recenter_gaze() -> str:
                if model is None:
                    return "Vision loop not active."
                st = _inproc_vlm_state()
                if st is None or st[2] is None:
                    return "No current face/eye observation to recenter."
                mon = monitor_at_point(layout, st[0], st[1])
                cx = mon.x + mon.width * 0.5
                cy = mon.y + mon.height * 0.5
                dx, dy = model.recenter_to(cx, cy, st[2])
                return f"Recentered gaze to monitor center ({cx:.0f}, {cy:.0f}) with offset=({dx:+.0f}, {dy:+.0f})px"

            router = VoiceCommandRouter(
                pointer=pointer,
                llm_agent=llm,
                gaze_reader=gaze_reader,
                gaze_provider=gaze_provider,
                dictate_unprefixed=dictate_unprefixed,
                gaze_vlm_clicker=vlm_clicker,
                gaze_state_provider=_inproc_vlm_state if enable_vision else None,
                on_before_click=_on_before_click,
                firefox_bridge=firefox_bridge,
                on_recenter_gaze=_on_recenter_gaze if enable_vision else None,
            )

            def _prewarm_gaze_vlm() -> None:
                if vlm_clicker is None:
                    return
                st = _inproc_vlm_state()
                if st is not None:
                    vlm_clicker.on_speech_start_prewarm(st[0], st[1], st[2])
                elif gaze_reader is not None:
                    snap = gaze_reader.read()
                    if snap is not None:
                        vlm_clicker.on_speech_start_prewarm(snap.x, snap.y, None)
                else:
                    mon = layout.primary
                    vlm_clicker.on_speech_start_prewarm(
                        mon.x + mon.width * 0.5, mon.y + mon.height * 0.5, None
                    )

            stt_selection = create_stt_engine(config)
            stt = stt_selection.engine
            if stt_selection.fell_back:
                out.print(f"[yellow]STT fallback: {stt_selection.fallback_reason}[/yellow]")
            out.print(
                f"STT: {stt.status().name} ({stt.status().device}/{stt.status().compute_type})"
            )

            if config.always_on_voice:
                active_listener = ActiveVoiceListener(
                    on_speech_onset=_prewarm_gaze_vlm,
                    is_user_attending=_is_user_attending if enable_vision else None,
                    whisper_model_size=config.whisper_model,
                    enable_pop_click=config.enable_pop_click,
                    require_command_prefix=(config.require_command_prefix and not dictate_unprefixed),
                    audio_device=config.audio_device,
                    stt_engine=stt,
                )
                out.print(
                    f"[green]ActiveVoiceListener ready ({active_listener.device}/{active_listener.compute_type}) "
                    f"| LLM={llm.backend} | pop_click={config.enable_pop_click}[/green]"
                )

                def _drain_voice_events() -> None:
                    assert active_listener is not None
                    while not event_stop.is_set():
                        try:
                            ev = active_listener.events.get(timeout=0.05)
                        except queue.Empty:
                            continue
                        if ev.kind == "pop_click":
                            out.print("[green]POP (75ms-vetoed) -> BTN_LEFT[/green]")
                            pointer.click(ecodes.BTN_LEFT)
                        elif ev.kind == "voice_command":
                            out.print(
                                f"[cyan]Voice ({ev.confidence:.2f}):[/cyan] {ev.transcript!r}"
                            )
                            try:
                                res = router.dispatch(ev.transcript)
                                out.print(f"  -> [bold]{res.route}[/bold]: {res.detail}")
                            except Exception as exc:
                                out.print(f"  -> [red]Dispatch error: {exc}[/red]")

                active_listener.start()
                event_thread = threading.Thread(
                    target=_drain_voice_events,
                    name="voice-event-dispatcher",
                    daemon=True,
                )
                event_thread.start()
            else:
                info = stt.status()
                out.print(
                    f"[green]STT ready ({info.device}/{info.compute_type}) | LLM={llm.backend}[/green]"
                )

                pop = PopTriggerStateMachine(
                    min_peak=config.pop_min_peak,
                    min_rms=config.pop_min_rms,
                )

                def _on_pop() -> None:
                    out.print("[green]POP -> BTN_LEFT[/green]")
                    pointer.click(ecodes.BTN_LEFT)

                def _on_utterance(audio: np.ndarray) -> None:
                    t0 = time.perf_counter()
                    transcript = stt.transcribe(audio)
                    dt_ms = (time.perf_counter() - t0) * 1000.0
                    if not transcript:
                        return
                    out.print(f"[cyan]STT ({dt_ms:.0f}ms):[/cyan] {transcript!r}")
                    try:
                        res = router.dispatch(transcript)
                        out.print(f"  -> [bold]{res.route}[/bold]: {res.detail}")
                    except Exception as exc:
                        out.print(f"  -> [red]Dispatch error: {exc}[/red]")

                audio_svc = UnifiedAudioService(
                    pop_detector=pop,
                    on_pop=_on_pop,
                    on_utterance=_on_utterance,
                    device=config.audio_device,
                )

                def _on_ptt_press() -> None:
                    audio_svc.begin_ptt()
                    _prewarm_gaze_vlm()

                hotkey_code = resolve_hotkey_code(config.ptt_hotkey)
                ptt_mon = EvdevHotkeyMonitor(
                    hotkey_code=hotkey_code,
                    on_press=_on_ptt_press,
                    on_release=audio_svc.end_ptt,
                )
                n_devs = ptt_mon.start()
                if n_devs == 0:
                    out.print(
                        f"[yellow]Warning: no readable /dev/input/event* device has {config.ptt_hotkey}. "
                        "Pop-click active, PTT hotkey unavailable.[/yellow]"
                    )
                else:
                    out.print(
                        f"PTT={config.ptt_hotkey} ({n_devs} device(s)) | "
                        f"Pop(peak>={config.pop_min_peak:.2f}, rms>={config.pop_min_rms:.3f})"
                    )
                audio_svc.start()

        if enable_vision:
            assert model is not None
            gaze_mode = (
                "eye-mouse cursor control"
                if config.gaze_cursor_control
                else "internal gaze guide (physical mouse untouched)"
            )
            out.print(
                f"Starting gaze loop ({gaze_mode}, dry_run={dry_run}, "
                f"RMSE X={model.rmse_x:.1f}px Y={model.rmse_y:.1f}px). Press Ctrl+C to stop."
            )
            run_vision_loop(
                config=config,
                model=model,
                dry_run=dry_run,
                duration_sec=duration_sec,
                pointer=pointer,
                on_step=_on_vision_step,
                on_controller_ready=_on_controller_ready,
            )
        else:
            start_t = time.monotonic()
            while True:
                if duration_sec is not None and (time.monotonic() - start_t) >= duration_sec:
                    break
                time.sleep(0.1)
    except KeyboardInterrupt:
        out.print("\nStopped.")
    finally:
        event_stop.set()
        if mouse_harvester is not None:
            mouse_harvester.stop()
        if event_thread is not None and event_thread.is_alive():
            event_thread.join(timeout=1.0)
        if active_listener is not None:
            active_listener.stop()
        if stt is not None:
            stt.close()
        if ptt_mon is not None:
            ptt_mon.stop()
        if audio_svc is not None:
            audio_svc.stop()
        if vlm_clicker is not None:
            if config.harvest_calib and calib_path is not None:
                harvested = vlm_clicker.pop_harvested_samples()
                if harvested:
                    added = append_harvested_calibration_samples(
                        calib_path, harvested, config.cam1_by_id, config.cam2_by_id
                    )
                    if added > 0:
                        out.print(
                            f"[green]Saved {added} harvested voice-click calibration sample(s).[/green]"
                        )
            vlm_clicker.shutdown()
        if firefox_bridge is not None:
            firefox_bridge.stop()
        if gaze_reader is not None:
            gaze_reader.close()
        pointer.close()
