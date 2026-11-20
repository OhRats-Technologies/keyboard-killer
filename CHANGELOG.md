# Changelog

## [0.1.3] - 2026-11-20

- Head-movement-robust 3-D geometric gaze model (`geo_gaze.py`): casts per-camera
  eye rays onto physical monitor planes in a reference-monitor frame `M0` with
  head-aligned pose recovery, automatic multi-monitor placement from Linux DRM
  EDID millimetre dimensions, single-camera stereo fallback, and an online EKF +
  cursor-chase self-calibrator (`GeoGazeTracker`).
- Simplified 3-view desktop shell (`Home`, `Activity`, `Settings`) with inline
  4-step first-run onboarding (`Welcome`, `Devices`, `Calibration`, `Ready`),
  progressive disclosure for single-camera and permission states, friendly device
  and shortcut names, instant-apply settings, and working AI/ACP controls.
- Added fast local `./scripts/install.sh` and `./scripts/uninstall.sh` scripts
  for repository clones.

## [0.1.2] - 2026-11-20

- Control-room shell: grouped numbered rail, top status bar, large state
  headline, last heard/clicked columns, and a live instrument column.
- First launch opens the resumable setup story instead of a cold dashboard.
- Setup supports one camera (mono) or two (stereo) as equal, intentional paths;
  calibration, capture, doctor, and settings carry the chosen mode.
- Onboarding progress rail, grouped settings, readable diagnostics summary, and
  card-based camera/calibration/model surfaces.
- Calibration reports the measured RMSE and camera mode instead of only a file
  presence.
- Changelog now includes design values from the blind review: global pause,
  one-camera parity, plain-language state, and one primary action.

## [0.1.1] - 2026-11-20

- Desktop app now uses the OhRats UI kit: Inter/Space Mono, warm neutrals,
  sharp borders, one text-accent, light/dark tokens, and a grid home surface.
- Removed the faster-whisper fallback. Parakeet is the only STT engine; the
  speech step downloads the pinned model and no engine selector remains.
- Release assets are public on GitHub and mirrored on the landing site.

## [0.1.0] - 2026-11-20

First Linux product release.

- Path service, non-destructive migration, and one capability report shared by
  onboarding, settings, `doctor`, and the UI.
- Linux platform seams for uinput, evdev, V4L2, AT-SPI, screen capture, and
  Mutter layout.
- Parakeet TDT 0.6B v2 speech recognition with a pinned, resumable, SHA-256
  verified downloader and a faster-whisper fallback.
- Provider layer: OpenAI-compatible presets, Ollama, ACP agents, OS-keychain
  secrets, role capability checks, and remote-VLM consent.
- PySide6 shell with resumable onboarding, tray, settings, history, light/dark
  themes, and the GNOME Wayland tray/toast overlay fallback.
- `doctor` / `doctor --json`, Linux `.deb` and AppImage packaging, and a
  landing page at keyboard-killer.ohrats.party.
- Windows and macOS are capability stubs; no installers are published.
