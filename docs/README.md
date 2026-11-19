# Keyboard-Killer Docs

**Status:** research snapshot, revised 2026-11-19 (product direction: user-friendly like Hex, English-first, Parakeet STT, bring-your-own LLM with OpenAI-compatible and ACP providers). This describes a target organization; no code has been moved and no platform port has been implemented.

**Purpose:** keep the product, UI, model, and cross-platform research in one place so a new session starts from decisions and file maps instead of re-deriving them.

## What this snapshot contains

- A reference map of Hex's design system, onboarding, overlay, paths, and model downloader.
- A target repository tree and a current-to-target module map for the existing Linux code.
- A Linux/macOS/Windows capability, permission, overlay, and packaging matrix.
- Ten decisions with recommended defaults, including UI stack (PySide6), model ownership, GNOME overlay fallback, the LLM provider layer (OpenAI-compatible, Ollama, ACP), and Parakeet STT.
- The concerns register in `CONCERNS.md`, covering setup, runtime, privacy, layman UX, and the 2026-11-19 call.

## Read in order

1. [architecture.md](architecture.md) — goals, layers, data/model strategy, UI surfaces, migration order.
2. [file-map.md](file-map.md) — target repository tree and current-to-target module map.
3. [platform-matrix.md](platform-matrix.md) — Linux/macOS/Windows capabilities, permissions, overlays, packaging.
4. [decisions.md](decisions.md) — decisions and recommended defaults.
5. [../CONCERNS.md](../CONCERNS.md) — setup, runtime, privacy, usability, and layman-UX concerns from the current build and the 2026-11-19 call.

## Reference material

- `anomalyco/hex` @ `6435633` (read-only clone, not a dependency): design system, onboarding, capsules, app paths, pinned model downloader. Used as a reference for organization and outcomes, not for a port.
- Hex model store: `<data>/hex/models`, pinned Hugging Face artifacts, `.gguf.partial`, SHA-256 verification, `.gguf.verified.json` receipts, atomic promote.
- Hex LLM features: an already-running OpenCode service; Hex does not install or run an LLM.

### Hex reference map

| Path in `anomalyco/hex` | What to look at |
|---|---|
| `docs/design-system.md` | tokens, one accent, few actions, sentence case, keyboard focus, i18n |
| `src/rune_ui/onboarding_kit/` + `tests/golden/rune-onboarding-*.png` | setup story, permissions, practice, ready |
| `src/rune_ui/capture_capsule/` + `tests/golden/capture-capsule-*.png` | click-through overlay states: recording, processing, permission, saved |
| `src/app_paths.rs` | support/data/models paths per OS, Windows roaming migration |
| `src/transcription/artifacts.rs` | pinned model catalog: repo, revision, filename, bytes, SHA-256 |
| `src/transcription/models.rs` + `src/net/download.rs` | partial/resume, ranged pieces, verification, atomic promote, receipts |
| `docs/features/own-models.md` | found-in-HF-cache, imported, and in-place models |
| `docs/opencode-compatibility.md` | LLM features delegate to a running OpenCode service |

Snapshot: `6435633`. Re-clone read-only when needed; do not add Hex as a dependency.

## How to use this in a new session

1. Read `decisions.md` first. D2, D9, and D10 are settled for now; confirm D1 (UI stack) and D3 (overlay strategy) before UI work.
2. Start code with the STT swap (D10) and the provider interface (D9); both sit behind existing seams and need no UI.
3. Keep the current CLI and daemon behavior working while introducing platform seams.
4. Do not big-bang move files. Follow the migration order in `architecture.md` and keep changes reviewable.
5. Treat `CONCERNS.md` as the product backlog of observed problems; this docs set is the target organization.
