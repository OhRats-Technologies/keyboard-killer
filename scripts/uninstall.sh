#!/bin/sh
# Uninstall user-local Keyless launcher, desktop entry, icons, and AppImage.
# Pass --purge to also remove user config, cache, calibration, and history data.
set -eu

BIN_DIR="${KEYLESS_BIN_DIR:-${HOME}/.local/bin}"
DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
CACHE_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}"
DESKTOP_DIR="${DATA_HOME}/applications"
ICON_DIR="${DATA_HOME}/icons/hicolor"
APP_DIR="${KEYLESS_APPIMAGE_DIR:-${DATA_HOME}/keyless}"

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/uninstall.sh [--purge]

Removes the user-local keyless command, desktop entry, icons, and
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

pkill -f "python.*keyless\.main" 2>/dev/null || true

rm -f "${BIN_DIR}/keyless"
rm -f "${DESKTOP_DIR}/keyless.desktop"
rm -f "${ICON_DIR}/scalable/apps/keyless.svg"
rm -f "${ICON_DIR}/256x256/apps/keyless.png"
rm -f "${APP_DIR}/Keyless.AppImage"

if [ "$PURGE" -eq 1 ]; then
  rm -rf "${APP_DIR}" "${CONFIG_HOME}/keyless" "${CACHE_HOME}/keyless"
  echo "Purged Keyless binaries and user data."
else
  echo "Uninstalled Keyless (user settings, calibration, and models kept; pass --purge to remove)."
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q "$DESKTOP_DIR" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q "$ICON_DIR" 2>/dev/null || true
fi
