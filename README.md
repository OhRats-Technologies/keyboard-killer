# Keyboard-Killer

Dual-camera stereo gaze tracking, sub-45ms acoustic pop-click detection, and low-latency voice/AI control for Ubuntu GNOME Wayland.

## Phase 1: Dual-Camera Stereo Gaze & Split `/dev/uinput` Cursor

### 1. One-Time `/dev/uinput` Permissions
Grant your active desktop session write access to `/dev/uinput`:

```bash
sudo ./scripts/setup_uinput_udev.sh
```

### 2. Inspect Hardware & Display Layout
```bash
uv run keyboard-killer check-system
uv run keyboard-killer test-cams
```

### 3. Calibrate & Run Vision Loop
```bash
uv run keyboard-killer calibrate
uv run keyboard-killer run-vision
```

Use `--dry-run` to test without emitting `/dev/uinput` pointer events.

---

## Phase 2: Sub-45ms Pop-Click & CUDA Voice Router

### 1. Test Mic Pop Detector (and Plosive Rejection)
```bash
uv run keyboard-killer test-audio --seconds 5
```

### 2. Run Combined Vision + Voice Daemon (`Phase 1 + Phase 2`)
```bash
uv run keyboard-killer run
```

Or run subsystems independently (`uv run keyboard-killer run-vision`, `uv run keyboard-killer run-voice`).

- **Pop sound**: Instant `BTN_LEFT` click (~32–48ms, muted automatically while PTT is held).
- **Deterministic commands** (<10ms): `"click"`, `"right click"`, `"double click"`, `"scroll down"`, `"scroll up"`, `"copy"`, `"paste"`, `"undo"`, `"delete line"`, `"go back"`, `"enter"`, `"escape"`.
- **`"Type ..."`**: Types dictated text directly into the focused window via `/dev/uinput`.
- **`"Terminal ..."`**: Translates speech into a bash command via Ollama/Gemini and types it onto your terminal prompt without pressing Enter.
- **`"Browser ..."`**: Packages the instruction with live `(x, y)` gaze coordinates from shared memory.
