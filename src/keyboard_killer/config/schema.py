"""Configuration, deterministic camera discovery, and GNOME Wayland monitor layout."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CAM1_ID = "/dev/v4l/by-id/usb-046d_0825_4BEC6610-video-index0"
DEFAULT_CAM2_ID = (
    "/dev/v4l/by-id/usb-Sunplus_IT_Co_Live_Streamer_CAM_313_20200529002-video-index0"
)

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "keyboard-killer"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "keyboard-killer"

DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.json"
DEFAULT_CALIB_PATH = CONFIG_DIR / "stereo_calib.json"
DEFAULT_LANDMARKER_MODEL_PATH = CACHE_DIR / "face_landmarker.task"

FACE_LANDMARKER_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)


@dataclass(frozen=True)
class MonitorInfo:
    """Single logical monitor in the desktop layout."""

    connector: str
    x: int
    y: int
    width: int
    height: int
    physical_width: int
    physical_height: int
    scale: float
    is_primary: bool


@dataclass(frozen=True)
class DisplayLayout:
    """Combined multi-monitor desktop bounding box and active monitors."""

    monitors: tuple[MonitorInfo, ...]
    total_width: int
    total_height: int
    layout_mode: int = 1

    @property
    def primary(self) -> MonitorInfo:
        for mon in self.monitors:
            if mon.is_primary:
                return mon
        return self.monitors[0]


@dataclass
class AppConfig:
    """Persistent user configuration for keyboard-killer."""

    cam1_by_id: str = DEFAULT_CAM1_ID
    cam2_by_id: str = DEFAULT_CAM2_ID
    frame_width: int = 1280
    frame_height: int = 720
    fps: int = 30
    cam1_exposure: int = 300
    cam1_gain: int = 180
    cam2_exposure: int = 300
    cam2_gain: int = 160
    saccade_threshold_px: float = 110.0
    saccade_hold_ms: float = 16.0
    gimbal_gain: float = 2800.0
    one_euro_min_cutoff: float = 1.4
    one_euro_beta: float = 0.008
    blink_threshold: float = 0.55
    use_gpu: bool = True
    # Phase 2: Audio pop-click, CUDA faster-whisper, and LLM routing
    ptt_hotkey: str = "KEY_RIGHTCTRL"
    whisper_model: str = "base.en"
    llm_backend: str = "auto"
    ollama_model: str = "qwen2.5-coder:7b"
    gemini_model: str = "gemini-2.5-flash"
    pop_min_peak: float = 0.25
    pop_min_rms: float = 0.032
    audio_device: str | None = None
    # Phase 3: Gaze-Foveated Voice + Vision Grounding & Always-On VAD
    gaze_cursor_control: bool = False
    gaze_vlm_enabled: bool = True
    gaze_vlm_model: str = "gemma4:e4b"
    gaze_crop_w: int = 680
    gaze_crop_h: int = 480
    gaze_keyword_threshold: float = 82.0
    harvest_calib: bool = True
    always_on_voice: bool = True
    enable_pop_click: bool = False
    require_command_prefix: bool = True
    attention_max_yaw_deg: float = 38.0
    firefox_bridge_enabled: bool = True
    firefox_bridge_port: int = 8765

    def save(self, path: Path = DEFAULT_CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


def discover_v4l_by_id() -> list[str]:
    """Return sorted `/dev/v4l/by-id/*-video-index0` symlinks for capture devices."""
    by_id_dir = Path("/dev/v4l/by-id")
    if not by_id_dir.exists():
        return []
    return sorted(
        str(p)
        for p in by_id_dir.iterdir()
        if p.name.endswith("-video-index0") and p.is_symlink()
    )


def parse_mutter_display_state(gvariant_text: str) -> DisplayLayout:
    """Parse `org.gnome.Mutter.DisplayConfig.GetCurrentState` gdbus output."""
    # Extract current physical resolution per connector by scanning monitor blocks.
    phys_modes: dict[str, tuple[int, int]] = {}
    conn_pattern = re.compile(r"\(\('([^']+)',\s*'[^']*',\s*'[^']*',\s*'[^']*'\),\s*\[")
    current_mode_pattern = re.compile(
        r"\('[^']+',\s*(\d+),\s*(\d+),\s*[\d.]+,\s*[\d.]+,\s*\[[^\]]*\],\s*\{[^}]*'is-current':\s*<true>"
    )

    conn_matches = list(conn_pattern.finditer(gvariant_text))
    for idx, m in enumerate(conn_matches):
        connector = m.group(1)
        start = m.end()
        end = conn_matches[idx + 1].start() if idx + 1 < len(conn_matches) else len(gvariant_text)
        block = gvariant_text[start:end]
        mode_m = current_mode_pattern.search(block)
        if mode_m:
            phys_modes[connector] = (int(mode_m.group(1)), int(mode_m.group(2)))

    layout_mode_m = re.search(r"'layout-mode':\s*<(?:uint32\s+)?(\d+)>", gvariant_text)
    layout_mode = int(layout_mode_m.group(1)) if layout_mode_m else 1

    logical_pattern = re.compile(
        r"\((-?\d+),\s*(-?\d+),\s*([\d.]+),\s*(?:uint32\s+)?\d+,\s*(true|false),\s*"
        r"\[\('([^']+)',\s*'[^']*',\s*'[^']*',\s*'[^']*'\)"
    )

    monitors: list[MonitorInfo] = []
    for m in logical_pattern.finditer(gvariant_text):
        x = int(m.group(1))
        y = int(m.group(2))
        scale = float(m.group(3))
        is_primary = m.group(4) == "true"
        connector = m.group(5)
        phys_w, phys_h = phys_modes.get(connector, (1920, 1080))
        if layout_mode == 1 and scale > 0:
            logical_w = int(round(phys_w / scale))
            logical_h = int(round(phys_h / scale))
        else:
            logical_w, logical_h = phys_w, phys_h
        monitors.append(
            MonitorInfo(
                connector=connector,
                x=x,
                y=y,
                width=logical_w,
                height=logical_h,
                physical_width=phys_w,
                physical_height=phys_h,
                scale=scale,
                is_primary=is_primary,
            )
        )

    if not monitors:
        fallback = MonitorInfo(
            connector="DEFAULT",
            x=0,
            y=0,
            width=1920,
            height=1080,
            physical_width=1920,
            physical_height=1080,
            scale=1.0,
            is_primary=True,
        )
        return DisplayLayout(monitors=(fallback,), total_width=1920, total_height=1080)

    total_w = max(mon.x + mon.width for mon in monitors)
    total_h = max(mon.y + mon.height for mon in monitors)
    return DisplayLayout(
        monitors=tuple(monitors),
        total_width=total_w,
        total_height=total_h,
        layout_mode=layout_mode,
    )


def get_display_layout() -> DisplayLayout:
    """Query active GNOME Mutter logical monitor layout, with safe fallback."""
    try:
        res = subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.gnome.Mutter.DisplayConfig",
                "--object-path",
                "/org/gnome/Mutter/DisplayConfig",
                "--method",
                "org.gnome.Mutter.DisplayConfig.GetCurrentState",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=True,
        )
        return parse_mutter_display_state(res.stdout)
    except (OSError, subprocess.SubprocessError):
        return parse_mutter_display_state("")
