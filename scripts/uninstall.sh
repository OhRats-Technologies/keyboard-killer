#!/bin/sh
# Uninstall user-local Keyboard-Killer launcher, desktop entry, icons, and AppImage.
# Pass --purge to also remove user config, cache, calibration, and history data.
set -eu

BIN_DIR="${KEYBOARD_KILLER_BIN_DIR:-${HOME}/.local/bin}"
DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
CACHE_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}"
DESKTOP_DIR="${DATA_HOME}/applications"
ICON_DIR="${DATA_HOME}/icons/hicolor"
APP_DIR="${KEYBOARD_KILLER_APPIMAGE_DIR:-${DATA_HOME}/keyboard-killer}"

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/uninstall.sh [--purge]

Removes the user-local keyboard-killer command, desktop entry, icons, and
any installed AppImage. Calibration, settings, models, and history are kept
unless --purge is passed.
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

pkill -f "python.*keyboard_killer\.main" 2>/dev/null || true

rm -f "${BIN_DIR}/keyboard-killer"
rm -f "${DESKTOP_DIR}/keyboard-killer.desktop"
rm -f "${ICON_DIR}/scalable/apps/keyboard-killer.svg"
rm -f "${ICON_DIR}/256x256/apps/keyboard-killer.png"
rm -f "${APP_DIR}/Keyboard-Killer.AppImage"

if [ "$PURGE" -eq 1 ]; then
  rm -rf "${APP_DIR}" "${CONFIG_HOME}/keyboard-killer" "${CACHE_HOME}/keyboard-killer"
  echo "Purged Keyboard-Killer binaries and user data."
else
  echo "Uninstalled Keyboard-Killer (user settings, calibration, and models kept; pass --purge to remove)."
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q "$DESKTOP_DIR" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q "$ICON_DIR" 2>/dev/null || true
fi
