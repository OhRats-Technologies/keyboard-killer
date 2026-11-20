# Changelog

## [0.1.1] - 2026-11-20

- **Translucent interactive calibration overlay** (`ui/calibration_overlay.py`): replaces the opaque fullscreen OpenCV blackout with a calm, frameless per-pixel translucent desktop overlay (XWayland-positioned on Linux Wayland), floating pill HUD controls, toggleable veil opacity, and live gaze + drag/spot-fix refinement directly over desktop apps.
- **On-device TTS via ConwayResearch/Underdog-Woof** (`voice/tts.py`, pinned in `models/catalog.py`): offline 24 kHz Juno/Mira voices with SHA-256 verification, spoken AI assistant answers (`ask ...` / `assistant ...`), automatic mic suppression while speaking, optional onboarding & calibration spoken help, and voice settings + attribution.
- **Automatic update checks & installer** (`updater.py`, `keyless update` CLI): modeled after `pingdotgg/t3code` with 4-hour background GitHub Releases checks, prerelease/stable channels, cleaned release notes, SHA-256 verification, Home banner, Tray action, Settings controls, and support for source/git, AppImage, and `.deb` updates.

## [0.1.0] - 2026-11-20

Initial Keyless release:

- Head-movement-robust 3-D geometric gaze model (`geo_gaze.py`): casts per-camera
  eye rays onto physical monitor planes in a reference-monitor frame `M0` with
  head-aligned pose recovery, automatic multi-monitor placement from Linux DRM
  EDID millimetre dimensions, single-camera stereo fallback, and an online EKF +
  cursor-chase self-calibrator (`GeoGazeTracker`).
- Streamlined 3-view desktop app (`Home`, `Activity`, `Settings`) with inline
  4-step setup (`Welcome`, `Devices`, `Calibration`, `Ready`), progressive
  disclosure, friendly camera & shortcut labels, and instant-apply settings.
- Local-first on-device speech recognition (Parakeet TDT 0.6B v2 int8), optional
  AI/ACP routing, Linux `.deb` and AppImage packaging, plus fast local
  `./scripts/install.sh` and `./scripts/uninstall.sh`.
