#!/bin/sh
# Keyboard-Killer Linux installer.
#
#   curl -fsSL https://keyboard-killer.ohrats.party/install.sh | sh
#
# Debian/Ubuntu: installs the self-contained .deb with apt.
# Other distributions or no sudo: installs the AppImage for the current user.
set -eu

REPO="OhRats-Technologies/keyboard-killer"
RELEASE_BASE="${KEYBOARD_KILLER_RELEASE_BASE:-https://github.com/OhRats-Technologies/keyboard-killer/releases/latest/download}"
ASSET_DEB="keyboard-killer_amd64.deb"
ASSET_APPIMAGE="keyboard-killer-x86_64.AppImage"
SUMS="SHA256SUMS"
APP_DIR="${KEYBOARD_KILLER_APPIMAGE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/keyboard-killer}"
BIN_DIR="${KEYBOARD_KILLER_BIN_DIR:-$HOME/.local/bin}"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/keyboard-killer-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

say() { printf '%s\n' "$*"; }
fail() { printf 'keyboard-killer: %s\n' "$*" >&2; exit 1; }

case "$(uname -m)" in
  x86_64|amd64) ;;
  *) fail "this release supports x86_64 only; ARM64 packages are not published yet" ;;
esac

download() {
  curl -fL --retry 3 --connect-timeout 15 "$1" -o "$2"
}

say "Downloading release checksums..."
download "${RELEASE_BASE}/${SUMS}" "$TMP/$SUMS" ||
  fail "could not download ${RELEASE_BASE}/${SUMS}"

use_deb=0
if [ "${KEYBOARD_KILLER_APPIMAGE:-0}" != "1" ] && command -v apt-get >/dev/null 2>&1; then
  use_deb=1
fi

verify() {
  asset="$1"
  expected="$(awk -v name="$asset" '$2 == name { print $1 }' "$TMP/$SUMS")"
  [ -n "$expected" ] || fail "no checksum published for $asset"
  actual="$(sha256sum "$TMP/$asset" | awk '{print $1}')"
  [ "$actual" = "$expected" ] || fail "checksum mismatch for $asset"
}

if [ "$use_deb" = "1" ]; then
  say "Downloading ${ASSET_DEB}..."
  download "${RELEASE_BASE}/${ASSET_DEB}" "$TMP/$ASSET_DEB" || fail "could not download the .deb"
  verify "$ASSET_DEB"
  say "Installing the Debian package (apt will ask for your password if needed)..."
  sudo apt-get install -y --no-install-recommends "$TMP/$ASSET_DEB" ||
    fail "apt could not install the package; set KEYBOARD_KILLER_APPIMAGE=1 to install the AppImage instead"
  say "Installed. Open Keyboard-Killer from the application menu, or run: keyboard-killer ui"
  exit 0
fi

say "Downloading ${ASSET_APPIMAGE}..."
download "${RELEASE_BASE}/${ASSET_APPIMAGE}" "$TMP/$ASSET_APPIMAGE" || fail "could not download the AppImage"
verify "$ASSET_APPIMAGE"

mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"
install -m 0755 "$TMP/$ASSET_APPIMAGE" "$APP_DIR/Keyboard-Killer.AppImage"
cat > "$BIN_DIR/keyboard-killer" <<EOF
#!/bin/sh
exec "$APP_DIR/Keyboard-Killer.AppImage" "\$@"
EOF
chmod 0755 "$BIN_DIR/keyboard-killer"
cat > "$DESKTOP_DIR/keyboard-killer.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Keyboard-Killer
Comment=Gaze and voice control
Exec=$APP_DIR/Keyboard-Killer.AppImage ui
Icon=keyboard-killer
Terminal=false
Categories=Utility;Accessibility;
EOF

say "Installed Keyboard-Killer to $APP_DIR/Keyboard-Killer.AppImage"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say "Add $BIN_DIR to PATH to run the keyboard-killer command, or open the app menu entry." ;;
esac
say "Open Keyboard-Killer from the application menu, or run: $BIN_DIR/keyboard-killer ui"
