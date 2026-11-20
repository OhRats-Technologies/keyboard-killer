# Changelog

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
