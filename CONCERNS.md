# CONCERNS

Register of observed concerns for Keyboard-Killer, with the 2026-11-19 implementation status. The original concern text is preserved; resolved/partial/open reflects what shipped.

- Initial checkout: `45ea4e4` (2026-11-19). Implementation review: steps 1–8 on 2026-11-19.
- Sources: the original checkout, the 2026-11-19 usability call recording, the product plan in `docs/`, and the implementation tests/packaging evidence below.
- Reference: `anomalyco/hex` @ `6435633` (shallow clone, read-only; UI/onboarding comparison only).
- Architecture research and target file maps: [docs/README.md](docs/README.md).
- Severity: **Blocker** = prevents the documented first run on the target platform; **High** = blocks a core feature or yields wrong behavior; **Medium** = friction, degraded behavior, or misleading docs; **Low** = operational/polish.
- The original setup claims came from static inspection. The status column reflects actual runs on this Linux machine: 97 tests, `doctor --json`, a real ACP round-trip, and a built `.deb`/AppImage. Hardware-only checks (two cameras, calibration, uinput injection, screen capture) were not performed.

## Summary

| ID | Area | Concern | Severity | Status (2026-11-19) |
|---|---|---|---|---|
| C-01 | Setup | Camera binding is hard-coded to one specific C270 and one specific CAM 313 | High | Partial |
| C-02 | Setup | Calibration is a mandatory, GUI-only gate before vision | High (Blocker on headless) | Open |
| C-03 | Setup | `check-system` covers only cameras, uinput, and display | Medium | Resolved |
| C-04 | Setup | System dependencies (PortAudio, CUDA, AT-SPI, GUI, `gdbus`) are undocumented | High | Partial |
| C-05 | Setup | LLM/VLM provisioning is manual and implicit | High (full features) | Resolved |
| C-06 | Setup | Firefox extension has no install/package path | Medium | Open |
| C-07 | Setup | Screenshot permission is a hidden runtime prerequisite | High | Partial |
| C-08 | Setup | First run needs network for model downloads | Medium | Partial |
| C-09 | Setup | README assumes GPU; code silently falls back to CPU | Medium | Resolved |
| C-10 | Runtime | Headline features are off by default in `run` | Medium | Partial |
| C-11 | Runtime | `run-voice` still initializes the VLM stack and cannot enable pop-click from the CLI | Medium | Open |
| C-12 | Ops | No packaging, service, update, or uninstall path | Low | Resolved (Linux) |
| P-01 | Privacy | Always-on microphone by default | Medium | Open |
| P-02 | Privacy | Screen capture and click emission have no persistent indicator | Medium | Partial |
| U-01 | Usability | Named-target click missed in the recorded demo | High | Open |
| U-02 | Usability | Ordinal tab commands are ambiguous and slow to confirm | High | Open |
| U-03 | Usability | Target confirmation HUD exists only in Firefox | Medium | Partial |
| U-04 | Usability | VLM weight is a stated concern | Medium | Partial |
| U-05 | Evidence | No targeting benchmark or eval harness | Medium | Open |
| U-06 | Evidence | Demo used Chrome + Twitch; does not exercise the DOM tier | Medium | Open |
| U-07 | Evidence | Recording cannot support aim/jitter/drift assessment | Low | Open |

## Implementation status (2026-11-19)

This is the post-implementation view. "Resolved" means the shipped behavior removes the original failure; "Partial" means a real surface exists but a stated gap remains; "Open" means no fix shipped.

| Status | Concerns | Summary |
|---|---|---|
| **Resolved** | C-03, C-05, C-09, C-12, L-01, L-02, L-03, L-06, L-08, L-11, L-12, L-13 | `doctor` and one capability report; provider layer; CPU int8 default; Linux packaging; PySide6 shell; resumable onboarding; permissions rows; model/provider status; SQLite history; settings UI; practice/ready; `.deb`/AppImage |
| **Partial** | C-01, C-04, C-07, C-08, C-10, P-02, U-03, U-04, L-04, L-05, L-07, L-09, L-10, L-14 | Stable camera discovery/picker (defaults still old); dependency docs/packaging (hardware still needs checks); screen permission reported but first capture prompts; STT download with progress/fallback; settings expose defaults; tray/toast fallback (no persistent Wayland overlay); camera previews without face-found/quality; calibration still a separate OpenCV window; X11 overlay but Wayland fallback; pause stops the daemon; some daemon errors remain CLI strings; tokens/light/dark but no i18n or full accessibility audit |
| **Open** | C-02, C-06, C-11, P-01, U-01, U-02, U-05, U-06, U-07 | Calibration still a mandatory GUI gate; Firefox extension install/signing; `run-voice` still initializes the VLM; always-on mic default; named-target accuracy/tab ordinals/eval harness/demo validity not addressed |

### Evidence

- `uv run pytest`: 97 tests pass, including contract tests with fake OpenAI-compatible and fake ACP servers and headless Qt goldens.
- `uv run keyboard-killer doctor --json`: paths, cameras, audio, display, permissions, capabilities, STT (Parakeet ready), providers (Ollama unreachable is reported), keychain available.
- Real ACP round-trip: Claude Agent ACP returned `Hello.` in about 1.5 s after 1.3 s initialize.
- Packaging: self-contained `keyboard-killer_0.1.0_amd64.deb` (675.5 MB) and `Keyboard-Killer-0.1.0-x86_64.AppImage` (561.4 MB) built; bundled runtime `doctor`/UI smoke-tested; `.deb` structure verified.
- Hardware not proven: two cameras together, calibration, `/dev/uinput` synthetic clicks, screen capture, acoustic command recording. The microphone opened and the camera was discovered, but that is not a working end-to-end hardware test.
- The 12 command phrases used in the STT benchmark were TTS-generated; the 40 real human clips were smart-home commands. No natural human desktop-command corpus was available.

## Setup and onboarding

### C-01 — Camera binding is tied to one specific C270 and one specific CAM 313

- **Evidence:** `src/keyboard_killer/config.py:12-13` hard-codes by-id paths that include device serials; `config.py:66-67` uses them as defaults; `main.py:89-118` only reports the configured paths.
- **Impact:** any other camera — including another unit of the same model — shows as `MISSING` until `~/.config/keyboard-killer/config.json` is edited. There is no CLI flag or config command for camera IDs.
- **Severity:** High (blocker for non-default hardware).

### C-02 — Calibration is a mandatory, GUI-only gate before vision

- **Evidence:** `main.py:377-380` (`run`) and `main.py:435-437` (`run-vision`) exit when `stereo_calib.json` is absent; calibration opens a fullscreen OpenCV window (`calibrator.py:1281-1284`).
- **Impact:** vision cannot start on a headless/remote machine; `run --no-vision` (or the audio-only commands) bypasses the gate.
- **Severity:** High; Blocker on headless.

### C-03 — `check-system` covers only part of the prerequisites

- **Evidence:** `main.py:89-135` checks by-id devices, configured camera paths, Mutter layout, and `/dev/uinput` only.
- **Not checked:** microphone/PortAudio, PipeWire, Ollama/Gemini, model availability, screenshot portal, AT-SPI, Firefox extension, CUDA.
- **Impact:** the documented check can pass while voice, clicking, or model-dependent features fail later.
- **Severity:** Medium.

### C-04 — System dependencies are not documented

- **Evidence:** `pyproject.toml:6-20` lists Python packages only. `sounddevice` needs system PortAudio. `stt_engine.py:24-41` preloads CUDA 12 cuBLAS from Ollama/CUDA directories. `desktop_atspi.py:419-422` falls back to `/usr/bin/python3` with `gi`/Atspi. Calibration needs an OpenCV GUI. `config.py:212-230` calls `gdbus`.
- **Impact:** `uv sync` succeeds, then the app fails or degrades at runtime. There are no install notes for `libportaudio2`, `python3-gi`, `gir1.2-atspi-2.0`, CUDA libraries, or GUI libraries.
- **Severity:** High.

### C-05 — LLM/VLM provisioning is manual and implicit

- **Evidence:** `config.py:84-85` defaults to faster-whisper `base.en` and `llm_backend=auto`; `config.py:94` pins `gemma4:e4b`; `llm_agent.py:36-41` selects Gemini only when `GEMINI_API_KEY` is set, otherwise Ollama; `daemon.py:181-183` only warns when the VLM warm-up fails.
- **Impact:** the gaze VLM needs Ollama plus `gemma4:e4b`; Terminal/Browser LLM actions need either Ollama plus `qwen2.5-coder:7b` or a `GEMINI_API_KEY`. The project neither installs nor pulls any of these.
- **Severity:** High for full features.
- **Direction (2026-11-19):** STT becomes an app-owned Parakeet download; the LLM/VLM becomes bring-your-own through OpenAI-compatible, Ollama, and ACP providers. See [docs/decisions.md](docs/decisions.md) D2, D9, D10.

### C-06 — Firefox extension has no install/package path

- **Evidence:** `firefox_ext/manifest.json:2,9,12` (MV2, minimum Firefox 109); `firefox_ext/background.js:3` connects to `ws://127.0.0.1:8765`; `scripts/` contains only uinput and camera-locking helpers.
- **Impact:** the DOM tier requires manually loading the add-on as a temporary add-on; it disappears on Firefox restart. There is no Chrome extension, signing, or packaging.
- **Severity:** Medium.

### C-07 — Screenshot permission is a runtime prerequisite, not an install check

- **Evidence:** `ui_detector.py:125-155` tries `org.gnome.Shell.Screenshot.ScreenshotArea`; `ui_detector.py:191-230` falls back to `org.freedesktop.portal.Screenshot`; `ui_detector.py:302-304` raises when both fail.
- **Impact:** named-target clicks need a screenshot. On GNOME Wayland the shell call can be denied and the portal may prompt or deny; failure appears at click time as `Wayland screenshot failed...`.
- **Severity:** High.

### C-08 — First run needs network for model downloads

- **Evidence:** `landmarks.py:93-100` downloads `face_landmarker.task` (~3.6 MB); faster-whisper downloads `base.en` (~141 MB) on first use.
- **Impact:** an offline first run fails; the README does not mention either download.
- **Severity:** Medium.

### C-09 — README assumes GPU; code silently falls back to CPU

- **Evidence:** `config.py:81` sets `use_gpu=True`; `landmarks.py:187-212` falls back to CPU when the MediaPipe GPU delegate is unavailable; `stt_engine.py:65-85` tries CUDA float16, then falls back to CPU int8.
- **Impact:** the "sub-45 ms" and "CUDA" claims only hold on the GPU path. Startup does not fail without CUDA; it continues with different performance characteristics.
- **Severity:** Medium.

## Runtime and defaults

### C-10 — Headline features are off by default in `run`

- **Evidence:** `config.py:92` sets `gaze_cursor_control=False`; `config.py:100` sets `enable_pop_click=False`; `config.py:99` sets `always_on_voice=True`; `config.py:101` sets `require_command_prefix=True`; `main.py:356-365` leaves pop-click and eye-mouse at `None`.
- **Impact:** `run` neither moves the OS cursor nor pop-clicks unless flags are passed, while the README headlines both. The microphone is live by default.
- **Severity:** Medium.

### C-11 — `run-voice` still initializes the VLM stack and cannot enable pop-click from the CLI

- **Evidence:** `main.py:551-560` calls the daemon with vision disabled; `daemon.py:163-185` creates `GazeVLMClicker` and warms `gemma4:e4b` whenever `gaze_vlm_enabled` is true; `config.py:100` keeps pop-click off; `main.py:532-549` exposes no `--pop-click`, `--no-vlm`, `--vlm-model`, or `--no-firefox-bridge` options.
- **Impact:** "voice-only" still attempts to warm `gemma4:e4b` through Ollama and sets up screen/AT-SPI; if Ollama is absent it degrades with a warning rather than a hard failure. Pop-click cannot be enabled from that command. The same VLM initialization applies to `run --no-vision`.
- **Severity:** Medium.

### C-12 — No packaging, service, update, or uninstall path

- **Evidence:** `pyproject.toml` exposes a CLI script only; there is no `.deb`, AppImage, PyPI release configuration, systemd unit, or uninstall documentation. `scripts/` contains two setup helpers.
- **Impact:** install is a source checkout plus `uv`; autostart, logs, updates, and removal are manual.
- **Severity:** Low.

## Privacy and trust

### P-01 — Always-on microphone by default

- **Evidence:** `config.py:99` enables `always_on_voice`; `daemon.py:238-250` starts `ActiveVoiceListener` with VAD.
- **Impact:** VAD runs for the whole session and command-prefixed utterances are transcribed. The app has no visible recording indicator.
- **Severity:** Medium.

### P-02 — Screen capture and click emission have no persistent indicator

- **Evidence:** `gaze_vlm_clicker.py:211-251` captures the gaze ROI on speech onset; clicks are emitted through uinput. A HUD exists only in the Firefox content script (`content_script.js:78-121,612-615`).
- **Impact:** outside Firefox there is no on-screen sign that the app is capturing the screen or about to click.
- **Severity:** Medium.

## Usability evidence from the 2026-11-19 call

### U-01 — Named-target click missed in the recorded demo

- **Evidence:** 0:20 "Click Neon" → 0:21 "Oh, oh, that's not what I want"; at ~0:22 the shared window switches from Gemini to Canvas. No pointer is visible in the recording.
- **Impact:** the only demonstrated named-target click did not hit the requested target, and the operator rejected the result.
- **Severity:** High.

### U-02 — Ordinal tab commands are ambiguous and slow to confirm

- **Evidence:** 0:27 "Go to first... first tab"; 0:31 "Okay. First tab. Oh, second tab."; the first tab (Teams chat) appears at ~0:41, roughly 10 s after "first tab". "Next tab" changes at 0:04/0:10 look ~1 s.
- **Impact:** the operator corrects mid-command and cannot tell which ordinal was applied. The cause of the late switch is not visible (app execution vs. manual click).
- **Severity:** High.

### U-03 — Target confirmation HUD exists only in Firefox

- **Evidence:** the Firefox content script draws a green gaze box and a label badge (`content_script.js:78-121,612-615`). The call used Chrome, and no HUD or pointer is visible in any captured frame.
- **Impact:** on Chrome and native apps the user cannot see the recognized target before or after the click.
- **Severity:** Medium.

### U-04 — VLM weight is a stated concern

- **Evidence:** 1:01 "It uses a Gemma"; 1:06 "Oh, that's so heavy"; 1:18 "Gemma for it would still be big to do an action like that"; 2:21 "it's close."
- **Impact:** perceived latency/VRAM weight of the VLM is an explicit concern. The app warms and keeps the model in VRAM (`gaze_vlm_clicker.py:190-198`).
- **Severity:** Medium.

### U-05 — No targeting benchmark or eval harness

- **Evidence:** `tests/test_phase1.py`, `tests/test_phase2.py`, and `tests/test_phase3.py` are unit tests; the checkout has no accuracy/OSWorld-style benchmark. The reviewer raises auto-RL/OSWorld at 1:37-1:55.
- **Impact:** targeting accuracy is judged by ad-hoc demos; there is no repeatable measure across builds.
- **Severity:** Medium.

### U-06 — Demo used Chrome and Twitch; does not exercise the DOM tier

- **Evidence:** the call shows a Chrome window; the DOM bridge targets Firefox (`firefox_ext/`; `gaze_vlm_clicker.py` checks for Firefox before Tier-0). A GTA 6 Twitch stream plays alongside the shared window.
- **Impact:** the demo cannot validate the Tier-0 DOM path and runs under real-world audio/attention distraction.
- **Severity:** Medium (test validity).

### U-07 — Recording cannot support aim/jitter/drift assessment

- **Evidence:** the screen share ends at 0:43; the remaining 3:06 is audio over the participant avatar. No gaze cursor, reticle, camera feed, or click marker appears. The video starts mid-meeting (timer 28:30) and mid-sentence; "Neon" is ASR-uncertain.
- **Impact:** the recording cannot be used to evaluate pointing accuracy, jitter, drift, or the actual click position.
- **Severity:** Low (evidence quality).

## UI, onboarding, and layman usability (forward-looking)

**Your stated concern:** the product can do the job but does not feel like something a layman can install, start, trust, or recover. Hex is the reference for the *feeling*, not the code: a guided first run, compact overlays, plain copy, and a window that explains state. This section is not a proposal to port Hex or copy its identity.

**Observed in `anomalyco/hex` @ `6435633`** (read-only clone, not installed):

- `docs/design-system.md`: kit tokens, light/dark, one primary action per screen, few visible actions, sentence-case copy, keyboard focus, i18n.
- `src/rune_ui/onboarding_kit/` and `tests/golden/rune-onboarding-*.png`: "Setting up Hex" with MB progress and "Your voice never leaves it"; permission rows with Allow → Asking → Allowed, Open System Settings, denied/stale/restart states; "With Hex"/"Your turn" practice; "Hex is ready" with one instruction and where it lives.
- `src/rune_ui/capture_capsule/` and `tests/golden/capture-capsule-*.png`: a click-through top-of-screen capsule that never takes focus; recording (red dot, timer, stop), processing, permission ("Hex needs Screen Recording… Allow"), and saved (label + one action + close) states.
- `src/rune_ui/onboarding_kit/first_screen.rs` and `live.rs`: the privacy line, MB progress, resume, "Try again", "Restart Hex", "Reopen Hex".

**Concerns**

| ID | Concern | Severity | Status (2026-11-19) |
|---|---|---|---|
| L-01 | There is no product surface (only a CLI) | High | Resolved |
| L-02 | Install and first run are a terminal checklist | Blocker for layman | Resolved (Linux) |
| L-03 | Permissions and consent are not onboarded | High | Resolved |
| L-04 | Camera setup is invisible | High | Partial |
| L-05 | Calibration is a developer diagnostic | High | Partial |
| L-06 | Models and local services are invisible | High | Resolved |
| L-07 | No global aim/click feedback | High | Partial |
| L-08 | No "what did it just do?" history | Medium | Resolved |
| L-09 | No pause, mic, or screen state | High | Partial |
| L-10 | Errors are developer strings | High | Partial |
| L-11 | Settings are JSON and flags | Medium | Resolved |
| L-12 | No safe practice or ready handoff | High | Resolved |
| L-13 | Install is not an app install | High | Resolved (Linux) |
| L-14 | No visual identity or accessibility baseline | Medium | Partial |

### L-01 — There is no product surface

- **Evidence:** `pyproject.toml` exposes only the `keyboard-killer` CLI; `src/` has no app window, tray, or notifications; the only windows are `cv2.imshow` diagnostics (`main.py:225`, `calibrator.py:1281-1284`).
- **Layman impact:** there is nothing to open, look at, pause, or quit; the product is a terminal and a JSON file.
- **Expected:** one app icon/window (or tray) with state, pause, setup, settings, and quit.
- **Severity:** High.

### L-02 — Install and first run are a terminal checklist

- **Evidence:** README steps + `uv` + `setup_uinput_udev.sh` + camera JSON + `calibrate` + model pulls + Firefox add-on.
- **Layman impact:** a layman is unlikely to complete this; each failure is a different tool.
- **Expected:** one installer and a guided first run with progress, resume, and no JSON.
- **Severity:** Blocker for layman.

### L-03 — Permissions and consent are not onboarded

- **Evidence:** microphone, screen capture (`ui_detector.py:125-230`), `/dev/input` PTT, `/dev/uinput`, AT-SPI; failures appear later (`Wayland screenshot failed`, `no readable /dev/input/event*` warning).
- **Layman impact:** denial or absence is silent until a feature fails.
- **Expected:** permission rows with a reason, Allow/Open Settings, denied/stale/restart states, and a privacy line.
- **Severity:** High.

### L-04 — Camera setup is invisible

- **Evidence:** by-id defaults (`config.py:12-13`), `check-system` prints paths, `test-cams` opens an OpenCV window.
- **Layman impact:** wrong camera, no face, black preview; the fix is editing JSON.
- **Expected:** two live previews, face-found state, identify/swap cameras, exposure preview, pass/fail, continue.
- **Severity:** High.

### L-05 — Calibration is a developer diagnostic

- **Evidence:** fullscreen anchors/pursuits (`calibrator.py:1281-1284`); prints Train/CV RMSE (`main.py:270-272`); no quality gate or explanation.
- **Layman impact:** "look at the dots" with no sense of good/bad; it is a mandatory gate.
- **Expected:** guided calibration, progress, quality score, retry, per-monitor, and a clear reason.
- **Severity:** High.

### L-06 — Models and local services are invisible

- **Evidence:** Whisper `base.en` first-use download; Ollama `qwen2.5-coder:7b`/`gemma4:e4b`; VLM warm warning (`daemon.py:181-183`); no UI.
- **Layman impact:** long blank wait; failures only in logs; "it doesn't work".
- **Expected:** setup progress with MB, recommended model, retry/continue without, ready/missing/warming status, advanced picker.
- **Severity:** High.

### L-07 — No global aim/click feedback

- **Evidence:** HUD only in Firefox (`content_script.js`); Chrome/native apps have none; the 2026-11-19 call shows no pointer, reticle, or click marker.
- **Layman impact:** the user cannot see where they aim, what was recognized, or whether the click landed.
- **Expected:** a click-through overlay with gaze cursor, target highlight, click ripple/confirmation, and a short "Clicked Send"/"Heard: …" chip. It must never steal focus, and permission/error states should use the same pill.
- **Severity:** High.

### L-08 — No "what did it just do?" history

- **Evidence:** routes print to the console (`DispatchResult`); nothing is persisted or shown.
- **Layman impact:** the user cannot review, learn from, or retry an action.
- **Expected:** a recent-actions list (time, spoken command, target, result, latency, tier) with copy/retry and a local retention setting.
- **Severity:** Medium.

### L-09 — No pause, mic, or screen state

- **Evidence:** always-on VAD (`config.py:99`), screen capture on speech, no tray or indicator; Ctrl-C is the stop control.
- **Layman impact:** unclear when it listens or sees; no quick stop; trust suffers.
- **Expected:** a visible Listening/Paused state, a pause hotkey, mic and screen indicators, and a quit action.
- **Severity:** High.

### L-10 — Errors are developer strings

- **Evidence:** examples include `Wayland screenshot failed (both GNOME Shell ScreenshotArea and XDG Portal Screenshot failed)`, `Calibration file not found`, `No matching browser tab`, and `Whisper CUDA init failed`.
- **Layman impact:** the user cannot act on the error.
- **Expected:** plain language plus one action; technical detail under Advanced.
- **Severity:** High.

### L-11 — Settings are JSON and flags

- **Evidence:** `~/.config/keyboard-killer/config.json` and CLI flags; no settings surface.
- **Layman impact:** the user cannot change hotkey, sensitivity, device, or model without editing files.
- **Expected:** a small settings pane with sane defaults; changes apply live.
- **Severity:** Medium.

### L-12 — No safe practice or ready handoff

- **Evidence:** the first real action happens on the live desktop; there is no sandbox or ready screen.
- **Layman impact:** fear of the first click and no proof that the setup works.
- **Expected:** a "Your turn" practice in a contained card, success feedback, "You're ready", where it lives, and how to pause/quit.
- **Severity:** High.

### L-13 — Install is not an app install

- **Evidence:** source checkout + `uv` + Python 3.13 + sudo + system dependencies; no `.deb`, AppImage, installer, update check, or uninstall.
- **Layman impact:** the user cannot install or remove it like an app.
- **Expected:** one download/installer, an update check, and an uninstall path.
- **Severity:** High.

### L-14 — No visual identity or accessibility baseline

- **Evidence:** no app icon or tray icon, OpenCV diagnostic windows, no light/dark tokens, English-only copy.
- **Layman impact:** it feels like a script; some users cannot read or navigate it.
- **Expected:** one icon/wordmark, consistent surfaces, one accent per screen, sentence-case copy, light/dark, and basic contrast/keyboard access.
- **Severity:** Medium.

**Minimum layman path I'd expect:**

1. Install from one file; open one icon.
2. First run: welcome → two cameras with live previews → mic/screen/input permissions → model with MB progress → short practice → "You're ready".
3. Daily: a small always-on pill shows Listening/Paused and the last action; gaze and click feedback confirm the target; a pause hotkey exists.
4. Recovery: a plain-language banner with one action (Allow, Retry, Reopen, Use CPU); technical detail under Advanced.
5. Settings and history after the core: hotkey, sensitivity, devices, model, retention, recent actions.

**The layman version, in one sentence:** install it like an app, see a short setup that proves the camera, voice, and click work, then keep a small always-on indicator that says what it heard and where it clicked, with a pause button and plain-language recovery. Everything technical lives under Advanced.
