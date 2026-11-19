"""Per-OS application paths (D4) with a single environment override.

Every config, data, cache, log, model, calibration, history, and download path in
the application is derived from :class:`AppPaths`.  ``KEYBOARD_KILLER_HOME``
re-roots all four roots below one directory for tests and portable installs.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

HOME_ENV = "KEYBOARD_KILLER_HOME"
APP_DIR_NAME = "keyboard-killer"
MACOS_DIR_NAME = "Keyboard-Killer"
WINDOWS_DIR_NAME = "Keyboard-Killer"


def _xdg(env: Mapping[str, str], var: str, fallback: Path) -> Path:
    value = env.get(var)
    return Path(value).expanduser() if value else fallback


def _host_platform(platform_name: str | None = None) -> str:
    name = platform_name or sys.platform
    if name.startswith("linux"):
        return "linux"
    if name == "darwin":
        return "macos"
    if name.startswith("win"):
        return "windows"
    return name


@dataclass(frozen=True)
class AppPaths:
    """Resolved application roots and every file derived from them."""

    config_dir: Path
    data_dir: Path
    cache_dir: Path
    logs_dir: Path
    platform: str = "linux"
    home: Path | None = None

    # --- configuration -------------------------------------------------
    @property
    def settings_path(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def onboarding_path(self) -> Path:
        return self.config_dir / "onboarding.json"

    @property
    def calibration_dir(self) -> Path:
        return self.config_dir / "calibration"

    @property
    def calibration_path(self) -> Path:
        """Default stereo calibration file.

        D5 keys calibration by camera pair + monitor; the single legacy file name
        stays until camera identity lands so existing calibration keeps working.
        """
        return self.calibration_dir / "stereo_calib.json"

    @property
    def calibration_dataset_path(self) -> Path:
        return self.calibration_dir / "calib_dataset.npz"

    # --- user data -----------------------------------------------------
    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def providers_status_path(self) -> Path:
        return self.models_dir / "providers.json"

    @property
    def custom_models_dir(self) -> Path:
        return self.models_dir / "custom"

    @property
    def history_dir(self) -> Path:
        return self.data_dir / "history"

    @property
    def history_db_path(self) -> Path:
        return self.history_dir / "actions.db"

    # --- cache ---------------------------------------------------------
    @property
    def downloads_dir(self) -> Path:
        return self.cache_dir / "downloads"

    @property
    def landmarker_path(self) -> Path:
        return self.cache_dir / "face_landmarker.task"

    @property
    def icon_cache_dir(self) -> Path:
        return self.cache_dir / "icon_cache"

    # --- logs ----------------------------------------------------------
    @property
    def log_path(self) -> Path:
        return self.logs_dir / "app.jsonl"

    # --- one-time migration -------------------------------------------
    @property
    def migration_marker_path(self) -> Path:
        return self.config_dir / "migration-v1.json"

    @property
    def legacy_settings_path(self) -> Path:
        return self.config_dir / "config.json"

    @property
    def legacy_calibration_path(self) -> Path:
        return self.config_dir / "stereo_calib.json"

    @property
    def legacy_calibration_dataset_path(self) -> Path:
        return self.config_dir / "calib_dataset.npz"

    def ensure_directories(self) -> None:
        for directory in (
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.logs_dir,
            self.calibration_dir,
            self.models_dir,
            self.history_dir,
            self.downloads_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> dict[str, str]:
        return {
            "config": str(self.config_dir),
            "data": str(self.data_dir),
            "cache": str(self.cache_dir),
            "logs": str(self.logs_dir),
        }


def default_paths(
    env: Mapping[str, str] | None = None,
    *,
    platform_name: str | None = None,
    home: Path | None = None,
) -> AppPaths:
    """Resolve application roots for the host OS or for an explicit test platform."""
    environ = os.environ if env is None else env
    host = _host_platform(platform_name)
    home_dir = Path(home).expanduser() if home is not None else Path.home()
    override = environ.get(HOME_ENV)

    if override:
        root = Path(override).expanduser()
        return AppPaths(
            config_dir=root / "config",
            data_dir=root / "data",
            cache_dir=root / "cache",
            logs_dir=root / "logs",
            platform=host,
            home=root,
        )

    if host == "macos":
        support = home_dir / "Library" / "Application Support" / MACOS_DIR_NAME
        return AppPaths(
            config_dir=support,
            data_dir=support,
            cache_dir=home_dir / "Library" / "Caches" / MACOS_DIR_NAME,
            logs_dir=home_dir / "Library" / "Logs" / MACOS_DIR_NAME,
            platform=host,
            home=home_dir,
        )

    if host == "windows":
        roaming = Path(environ.get("APPDATA", home_dir / "AppData" / "Roaming"))
        local = Path(environ.get("LOCALAPPDATA", home_dir / "AppData" / "Local"))
        data = local / WINDOWS_DIR_NAME
        return AppPaths(
            config_dir=roaming / WINDOWS_DIR_NAME,
            data_dir=data,
            cache_dir=data / "cache",
            logs_dir=data / "logs",
            platform=host,
            home=home_dir,
        )

    config_home = _xdg(environ, "XDG_CONFIG_HOME", home_dir / ".config")
    data_home = _xdg(environ, "XDG_DATA_HOME", home_dir / ".local" / "share")
    state_home = _xdg(environ, "XDG_STATE_HOME", home_dir / ".local" / "state")
    return AppPaths(
        config_dir=config_home / APP_DIR_NAME,
        data_dir=data_home / APP_DIR_NAME,
        cache_dir=_xdg(environ, "XDG_CACHE_HOME", home_dir / ".cache") / APP_DIR_NAME,
        logs_dir=state_home / APP_DIR_NAME / "logs",
        platform=host,
        home=home_dir,
    )


def current_paths() -> AppPaths:
    """Paths for the running process; cheap to call and safe to re-resolve."""
    return default_paths()
