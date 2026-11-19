"""Configuration schema, OS paths, and migrations."""

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
    DisplayLayout,
    MonitorInfo,
    discover_v4l_by_id,
    get_display_layout,
    parse_mutter_display_state,
)

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
    "DisplayLayout",
    "MonitorInfo",
    "discover_v4l_by_id",
    "get_display_layout",
    "parse_mutter_display_state",
]
