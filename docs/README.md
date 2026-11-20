# Keyboard-Killer Docs

**Status:** implementation snapshot, 2026-11-20. Steps 1–5 and 8 of the migration are complete and verified; the Linux UI and packaging (step 6–7) are implemented. Windows and macOS are honest capability stubs. The Linux-first engine still runs as a CLI daemon.

**Purpose:** keep product, UI, model, provider, and cross-platform decisions in one place so a new session starts from shipped behavior and known gaps instead of re-deriving them.

## What shipped

- **Paths and capabilities**: one `AppPaths` service for config/data/cache/logs, `KEYBOARD_KILLER_HOME`, a non-destructive one-time migration, shared platform protocols, and one `CapabilityReport` for onboarding, settings, diagnostics, and `doctor`.
- **Platform seams**: uinput, evdev, V4L2, AT-SPI, screen capture, and Mutter layout live under `platform/linux/`. Windows and macOS expose tested stubs with `unsupported_platform` statuses.
- **STT**: `SttEngine` contract, Parakeet TDT 0.6B v2 (CPU int8 default, CUDA opt-in), pinned catalog/downloader/receipts, and faster-whisper `base.en` fallback. The default CUDA preload is gone.
- **Providers**: `Provider` contract; `openai_compat` with presets, `ollama`, and `acp`; ACP registry fetch with cached/offline fallback; OS-keychain secrets; role capability checks; remote-VLM consent.
- **Diagnostics**: `doctor` and `doctor --json` over the same report the UI uses.
- **UI**: PySide6 main window, tray, resumable onboarding, settings, SQLite history, light/dark tokens, and the D3 X11 overlay / GNOME Wayland tray-toast fallback.
- **Packaging**: a self-contained Linux `.deb` and AppImage with the udev rule, desktop entry, and optional systemd user unit, plus the [landing page](https://keyboard-killer.ohrats.party/) on the OhRats UI kit and a tag-driven release mirror.

## What is still open

- Natural human desktop-command recordings and the full hardware path (two cameras, calibration, uinput injection, screen capture) were not available; see `CONCERNS.md`.
- The original CLI daemon is not yet the UI's only engine host; the UI starts/stops the daemon as a child process, and a true pause IPC is still missing.
- Firefox extension installation/signing, a global GNOME Wayland overlay, macOS/Windows implementations, and notarization/signing are not done.

## Read in order

1. [architecture.md](architecture.md) — goals, layers, data/model strategy, UI surfaces, migration status.
2. [file-map.md](file-map.md) — target tree and migration status.
3. [platform-matrix.md](platform-matrix.md) — Linux/macOS/Windows capability status.
4. [decisions.md](decisions.md) — decisions, defaults, implementation status.
5. [features/stt.md](features/stt.md) — pinned model, downloader behavior, benchmark method and numbers.
6. [features/providers.md](features/providers.md) — provider adapters, ACP safety, role/consent rules.
7. [RELEASES.md](RELEASES.md) — tag-driven release, public mirror, and install smoke.
8. [../CONCERNS.md](../CONCERNS.md) — concerns with resolved/partial/open status.

## Reference material

- `anomalyco/hex` @ `6435633` (read-only clone, not a dependency): design system, onboarding, capsules, app paths, pinned model downloader. Used as a reference for organization and outcomes, not for a port.
- Hex model store: `<data>/hex/models`, pinned artifacts, `.partial`, SHA-256 verification, receipts, atomic promote.
- Hex LLM features: an already-running OpenCode service; Hex does not install or run an LLM.

### Hex reference map

| Path in `anomalyco/hex` | What was borrowed |
|---|---|
| `docs/design-system.md` | tokens, one accent, few actions, sentence case, keyboard focus |
| `src/rune_ui/onboarding_kit/` + goldens | setup story, permissions, practice, ready |
| `src/rune_ui/capture_capsule/` | click-through overlay states (implemented on X11; Wayland fallback) |
| `src/app_paths.rs` | support/data/models paths per OS |
| `src/transcription/artifacts.rs` | pinned model catalog: repo, revision, filename, bytes, SHA-256 |
| `src/transcription/models.rs` + `src/net/download.rs` | partial/resume, verification, atomic promote, receipts |
| `docs/features/own-models.md` | found/imported/in-place models (not implemented; app-owned only) |
| `docs/opencode-compatibility.md` | provider delegation without bundling an LLM |

Snapshot: `6435633`. Do not add Hex as a dependency.
