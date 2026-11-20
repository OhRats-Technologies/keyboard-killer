#!/bin/sh
# Keyless Linux installer.
#
#   curl -fsSL https://keyless.ohrats.party/install.sh | sh
#
# Debian/Ubuntu: installs the self-contained .deb with apt.
# Other distributions or no sudo: installs the AppImage for the current user.
set -eu

REPO="OhRats-Technologies/keyless"
RELEASE_BASE="${KEYLESS_RELEASE_BASE:-https://github.com/OhRats-Technologies/keyless/releases/latest/download}"
ASSET_DEB="keyless_amd64.deb"
ASSET_APPIMAGE="keyless-x86_64.AppImage"
SUMS="SHA256SUMS"
APP_DIR="${KEYLESS_APPIMAGE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/keyless}"
BIN_DIR="${KEYLESS_BIN_DIR:-$HOME/.local/bin}"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/keyless-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

say() { printf '%s\n' "$*"; }
fail() { printf 'keyless: %s\n' "$*" >&2; exit 1; }

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
if [ "${KEYLESS_APPIMAGE:-0}" != "1" ] && command -v apt-get >/dev/null 2>&1; then
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
    fail "apt could not install the package; set KEYLESS_APPIMAGE=1 to install the AppImage instead"
  say "Installed. Open Keyless from the application menu, or run: keyless ui"
  exit 0
fi

say "Downloading ${ASSET_APPIMAGE}..."
download "${RELEASE_BASE}/${ASSET_APPIMAGE}" "$TMP/$ASSET_APPIMAGE" || fail "could not download the AppImage"
verify "$ASSET_APPIMAGE"

mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"
install -m 0755 "$TMP/$ASSET_APPIMAGE" "$APP_DIR/Keyless.AppImage"
cat > "$BIN_DIR/keyless" <<EOF
#!/bin/sh
exec "$APP_DIR/Keyless.AppImage" "\$@"
EOF
chmod 0755 "$BIN_DIR/keyless"
cat > "$DESKTOP_DIR/keyless.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Keyless
Comment=Gaze and voice control
Exec=$APP_DIR/Keyless.AppImage ui
Icon=keyless
Terminal=false
Categories=Utility;Accessibility;
EOF

say "Installed Keyless to $APP_DIR/Keyless.AppImage"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say "Add $BIN_DIR to PATH to run the keyless command, or open the app menu entry." ;;
esac
say "Open Keyless from the application menu, or run: $BIN_DIR/keyless ui"
