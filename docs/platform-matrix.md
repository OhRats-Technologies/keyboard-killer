# Platform Matrix

**Status:** research. Linux is the only implemented host today; macOS and Windows are targets. API names are the intended seams, not implemented code.

## Capability matrix

| Capability | Linux (current) | macOS (target) | Windows (target) | Shared contract |
|---|---|---|---|---|
| Move/click/scroll | `/dev/uinput` (evdev) | `CGEventPost` | `SendInput` | `PointerBackend` |
| Type text | uinput keymap + `wl-copy` for non-ASCII | `CGEventKeyboardSetUnicodeString` | `SendInput` + `KEYEVENTF_UNICODE` | `PointerBackend.type_text` |
| PTT hotkey | evdev `/dev/input/event*` | `CGEventTap` / `IOHIDManager` | `WH_KEYBOARD_LL` | `HotkeySource` |
| Cameras | V4L2 via OpenCV; `/dev/v4l/by-id` | AVFoundation | Media Foundation | `CameraSource` |
| Camera controls | V4L2 exposure/gain ioctls | AVFoundation exposure/focus | Media Foundation camera controls | `CameraControls` |
| Mic/audio | PipeWire/PulseAudio via PortAudio | CoreAudio via PortAudio | WASAPI via PortAudio | `AudioSource` |
| STT runtime | ONNX Runtime CPU int8; CUDA opt-in | ONNX Runtime CPU (CoreML untested) | ONNX Runtime CPU; DirectML/CUDA opt-in | `SttEngine` |
| Screen capture | GNOME Shell D-Bus / XDG portal | ScreenCaptureKit | Windows.Graphics.Capture / DXGI | `ScreenSource` |
| Accessibility tree | AT-SPI2 | `AXUIElement` | UI Automation | `AccessibilityBridge` |
| Tray/menu bar | StatusNotifierItem/AppIndicator | `NSStatusItem` | `Shell_NotifyIcon` | `Tray` |
| Autostart | XDG autostart / systemd user unit | SMAppService / LaunchAgent | Run key / startup task | `Autostart` |
| Single instance | Unix socket + lock | `NSRunningApplication` + lock | named mutex + socket | `SingleInstance` |
| Notifications | libnotify/portal | UserNotifications | toast/WinRT | `Notifier` |
| App paths | XDG | Application Support | AppData / LocalAppData | `AppPaths` |
| Browser DOM | Firefox WebExtension | same extension | same extension | `BrowserBridge` |

## Permission matrix

| Need | Linux | macOS | Windows |
|---|---|---|---|
| Camera | `/dev/video*` access (uaccess/plugdev) | TCC Camera | Settings › Privacy › Camera |
| Microphone | PipeWire/PulseAudio access | TCC Microphone | Settings › Privacy › Microphone |
| Screen capture | GNOME Screenshot D-Bus or ScreenCast/Screenshot portal | TCC Screen Recording | no prompt; capture indicator |
| Input injection | `/dev/uinput` write (udev rule) | TCC Accessibility | UIPI (elevated target may refuse) |
| Hotkey listening | `/dev/input/event*` read (input group) | TCC Input Monitoring | low-level hook (no prompt) |
| Accessibility tree | AT-SPI2 bus | TCC Accessibility | UI Automation (no prompt) |
| Autostart | XDG autostart | Login item / SMAppService | Startup task / Run key |
| Agent providers (ACP) | spawn agent subprocess (npx/uvx/binary) | same; hardened runtime must allow child processes | same; firewall unaffected (stdio) | `Provider` |
| Secret storage | Secret Service (libsecret) | Keychain | Credential Locker | `keyring` |
| Local bridge | none | local network permission in some sandboxes | Windows Firewall prompt for loopback listener |

## Linux notes

- **Input:** uinput virtual devices are the current path and should stay the default. Keep `DryRunPointerBackend` for tests and `--dry-run`.
- **Hotkeys:** evdev is global and works on Wayland, but requires read access to `/dev/input/event*`. The installer should explain this once; the UI should show a permission row, not a terminal warning.
- **Cameras:** `/dev/v4l/by-id` is the stable identity. Exposure/gain locking is V4L2-specific; expose it as `CameraControls`, with unsupported controls reported rather than fatal.
- **Screen:** try `org.gnome.Shell.Screenshot.ScreenshotArea`, fall back to `org.freedesktop.portal.Screenshot`; on denial, disable click-by-name with a permission row. Wayland capture is the runtime gate.
- **Accessibility:** AT-SPI worker is optional; if unavailable, the visual tiers still work and the capability report says so.
- **Overlay:** the hard case. A normal Wayland window cannot be made click-through with a portable API. Options: GNOME Shell extension (native, GNOME-versioned), XWayland input shape (works under XWayland, not native Wayland), or tray/toast only. See D3.
- **Packaging:** `.deb` and AppImage; include the udev rule; a `.desktop` file; optional systemd user unit. Flatpak is a later decision because the sandbox complicates uinput and portals.

## macOS notes

- **Input:** `CGEventPost` for absolute/relative moves, clicks, scroll, and Unicode typing. Requires Accessibility. Test against a non-elevated app and a secure input field; Secure Input can block synthesized keys.
- **Hotkeys:** `CGEventTap` in listen-only mode for PTT, or `IOHIDManager`. Requires Input Monitoring. Do not install a kernel extension.
- **Cameras:** AVFoundation device discovery (`uniqueID` is the stable identity); convert frames to the shared numpy format. Camera permission is TCC.
- **Audio:** CoreAudio through PortAudio; the existing `sounddevice` path should work with a device-selection layer.
- **Screen:** ScreenCaptureKit (`SCStream`) for regions/windows; Screen Recording permission. Provide a clear "Allow Screen Recording" state; denial disables visual grounding.
- **Accessibility:** `AXUIElement` + `AXObserver`; Accessibility permission. This replaces AT-SPI for native apps.
- **Tray:** `NSStatusItem`; menu shows Listening/Paused, Open, Pause, Calibrate, Quit.
- **Overlay:** `NSPanel` with non-activating style, status-bar level, `canJoinAllSpaces`/`fullScreenAuxiliary`; click-through except the pill. This is well-supported compared with GNOME Wayland.
- **Autostart:** `SMAppService` on macOS 13+, LaunchAgent fallback.
- **Packaging:** `.app` in a `.dmg`, codesigned and notarized, hardened runtime, usage strings for Camera/Microphone/Screen Recording/Accessibility/Input Monitoring. App Store sandbox is not a target.

## Windows notes

- **Input:** `SendInput` with absolute coordinates normalized to 0–65535 and `MOUSEEVENTF_VIRTUALDESK` for multi-monitor; Unicode typing with `KEYEVENTF_UNICODE`. UIPI blocks injection into higher-integrity processes; the UI should say so, not fail silently. Anti-cheat/AV may flag synthetic input; document it.
- **Hotkeys:** `WH_KEYBOARD_LL` needs a message loop; no admin prompt, but games may ignore it. Keep the hotkey thread separate from the UI.
- **Cameras:** Media Foundation (`IMFSourceReader`) or OpenCV MSMF; use the symbolic link/device path as identity, not the index.
- **Audio:** WASAPI through PortAudio; device selection and default-device changes must be handled live.
- **Screen:** Windows.Graphics.Capture (Win10 1903+) or DXGI Desktop Duplication; per-monitor DPI awareness is mandatory.
- **Accessibility:** UI Automation (`IUIAutomation`) for native apps; no prompt, but some apps expose poor trees.
- **Tray:** `Shell_NotifyIcon`; menu mirrors macOS.
- **Overlay:** layered window with `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`; per-monitor DPI; click-through except the pill. Do not take focus.
- **Packaging:** MSI or signed EXE installer; code signing (EV preferred); startup task; uninstaller. The local WebSocket bridge may trigger a firewall prompt; bind loopback only.
- **Autostart:** Startup task or Run key; expose it in Settings.

## Overlay specification (shared)

One logical spec, per-OS implementation:

- **Never take focus.** Only the pill's controls accept clicks; the rest is click-through.
- **States:** Idle/Listening, Processing, Heard (transcript), Clicked (target), Permission, Error.
- **Elements:** small status pill; gaze cursor/reticle; target highlight; click ring/confirmation; optional "Heard: …" line.
- **Placement:** near the gaze/target or a fixed screen edge; avoid covering the target; per-monitor aware.
- **Theme:** dark pill with high contrast; follows system appearance only where the OS supports it.
- **Dismiss:** automatically after a short hold; explicit close for persistent errors.
- **Accessibility:** do not steal keyboard focus; expose status through the tray/menu and, where possible, announcements.

## Packaging and CI

| OS | Packages | Signing | First-run |
|---|---|---|---|
| Linux | `.deb`, AppImage | repository key (optional) | udev rule, portal permission |
| macOS | `.dmg` `.app` | Developer ID + notarization | TCC prompts |
| Windows | MSI or EXE | Authenticode | privacy settings, firewall prompt |

- CI matrix: Linux + macOS + Windows for lint, unit, contract; UI goldens on Linux; packaging jobs per OS.
- Hardware tests remain manual: camera pair, GPU delegate, screen capture, input injection, overlay click-through.
- `doctor --json` must report the capability matrix and permission states on every OS.

## Open risks

1. GNOME Wayland click-through overlay (D3).
2. macOS TCC/notarization and secure input (D7).
3. Windows UIPI/anti-cheat/AV and signing (D7).
4. Camera identity and calibration across OSes (D5).
5. Model runtime/GPU coverage if the app owns GGUF files (D2).
