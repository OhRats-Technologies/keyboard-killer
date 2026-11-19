# Keyboard-Killer

Dual-camera stereo gaze, acoustic pop-click, and low-latency voice control for the Linux desktop, with an optional bring-your-own AI provider. Camera, speech, and history stay on the machine.

## Install (Linux)

Build the self-contained packages:

```bash
uv run python packaging/linux/build_deb.py --appimage
```

Install the `.deb` (the AppImage is portable):

```bash
sudo apt install ./dist/keyboard-killer_0.1.0_amd64.deb
```

The `.deb` installs the app under `/opt/keyboard-killer`, the `/dev/uinput` udev rule, a desktop entry, and an optional systemd user unit. Open **Keyboard-Killer** from the application menu. No terminal, `uv`, or JSON editing is required for first run.

First run is a guided setup: permissions → two cameras → calibration → speech model → optional AI provider → practice → ready.

> The package bundles a relocatable Python runtime. This build was produced and smoke-tested on Arch Linux (KDE Wayland); installing the `.deb` on Ubuntu/Debian and the full camera/calibration/uinput path are hardware checks that have not been performed here.

## CLI fallback

The CLI and daemon remain the debugging surface and stay valid:

```bash
uv run keyboard-killer doctor
uv run keyboard-killer doctor --json
uv run keyboard-killer check-system
uv run keyboard-killer models status
uv run keyboard-killer providers status
uv run keyboard-killer ui
uv run keyboard-killer run --dry-run
uv run keyboard-killer run-voice --seconds 5 --dry-run
```

`--dry-run` logs actions without emitting `/dev/uinput` events. `check-system` is the legacy hardware probe; `doctor` is the full report shared with the UI.

## Speech-to-text

The default is **NVIDIA Parakeet TDT 0.6B v2** through `onnx-asr`, CPU int8. `faster-whisper base.en` (CPU int8) is the automatic fallback. The Parakeet files are pinned by revision, size, and SHA-256 and downloaded with resume, verification, atomic promote, and a receipt:

```bash
uv run keyboard-killer models download stt
uv run keyboard-killer models verify stt
```

Measured on this machine (CPU int8) with 12 synthesized command phrases and 40 real human command clips from a public command-speech dataset:

| Engine | TTS exact / WER | TTS median / p95 | Real exact / WER | Real median / p95 | Real RTF |
|---|---|---|---|---|---|
| Parakeet TDT 0.6B v2 | 100% / 0.000 | 170 ms / 198 ms | 92.5% / 0.025 | 245 ms / 260 ms | 0.049 |
| faster-whisper base.en | 100% / 0.000 | 313 ms / 338 ms | 92.5% / 0.058 | 319 ms / 347 ms | 0.067 |

The command phrases were TTS-generated because no human desktop-command recordings were available; the real-human set is smart-home command speech, not desktop commands. Natural human desktop commands remain untested. See [docs/features/stt.md](docs/features/stt.md).

## AI providers (optional)

One provider interface covers:

- **OpenAI-compatible** endpoints and presets: OpenAI, OpenRouter, Groq, Together, Gemini's OpenAI-compatible endpoint, LM Studio, llama.cpp, vLLM, Ollama `/v1`.
- **Ollama** with status, pull, and warm progress.
- **ACP agents** over stdio (OpenCode, Claude Agent, Codex, Gemini CLI, Cline, and the registry list). The agent runs in an empty temporary directory; the client advertises no filesystem or terminal capabilities and rejects every permission request. The agent owns authentication.

API keys live in the OS keychain (`keyring`), never in `settings.json`, logs, or `doctor` output.

```bash
uv run keyboard-killer providers presets
uv run keyboard-killer providers set-key openai
uv run keyboard-killer providers acp
uv run keyboard-killer providers status
```

The gaze VLM role requires image input and explicit consent before screenshots may leave the device. Deterministic pointer and voice commands never depend on a provider.

## Development checks

```bash
uv run pytest
uv run pytest tests/ui -q          # headless Qt, no display needed
```

Hardware checks are manual and not claimed as working: two cameras together, calibration, `/dev/uinput` injection, screen capture, and an acoustic utterance through the microphone. `doctor` reports their current state.

## Layout

See [docs/file-map.md](docs/file-map.md), [docs/architecture.md](docs/architecture.md), and [docs/decisions.md](docs/decisions.md).
