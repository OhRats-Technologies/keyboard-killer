"""Configuration schema, OS paths, and migrations."""

from keyboard_killer.config.migrations import MigrationReport, ensure_migrated
from keyboard_killer.config.paths import AppPaths, current_paths, default_paths
from keyboard_killer.config.schema import (
    CACHE_DIR,
    CONFIG_DIR,
    DEFAULT_CALIB_PATH,
    DEFAULT_CAM1_ID,
    DEFAULT_CAM2_ID,
    DEFAULT_CONFIG_PATH,
    DEFAULT_LANDMARKER_MODEL_PATH,
    FACE_LANDMARKER_URL,
    AppConfig,
)
from keyboard_killer.platform.base import DisplayLayout, MonitorInfo
from keyboard_killer.platform.linux.cameras import discover_v4l_by_id
from keyboard_killer.platform.linux.display import get_display_layout, parse_mutter_display_state

__all__ = [
    "CACHE_DIR",
    "CONFIG_DIR",
    "DEFAULT_CALIB_PATH",
    "DEFAULT_CAM1_ID",
    "DEFAULT_CAM2_ID",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_LANDMARKER_MODEL_PATH",
    "FACE_LANDMARKER_URL",
    "AppConfig",
    "AppPaths",
    "DisplayLayout",
    "MigrationReport",
    "MonitorInfo",
    "current_paths",
    "default_paths",
    "discover_v4l_by_id",
    "ensure_migrated",
    "get_display_layout",
    "parse_mutter_display_state",
]
