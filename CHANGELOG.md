# Changelog

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
