"""Release/version/installer checks that do not need GitHub or root."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"


def test_version_script_matches_pyproject() -> None:
    result = subprocess.run(
        [str(ROOT / "scripts" / "check-version.sh"), "0.1.0"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "0.1.0"
    branch = subprocess.run(
        [str(ROOT / "scripts" / "check-version.sh"), "0.1.0"],
        cwd=ROOT,
        env={**os.environ, "GITHUB_REF": "refs/heads/main", "GITHUB_REF_NAME": "main"},
        capture_output=True,
        text=True,
    )
    assert branch.returncode == 0


def test_landing_page_links_stable_assets() -> None:
    html = (SITE / "index.html").read_text()
    assert "ohrats.abf84a1e6ae1.css" in html
    assert 'class="site-nav"' in html
    assert "assets.ohrats.party/assets/logo" in html
    assert "install.sh" in html
    assert "keyboard-killer_amd64.deb" in html
    assert "keyboard-killer-x86_64.AppImage" in html
    assert "macOS is not available yet" in html
    assert "Windows is not available yet" in html


def test_appimage_installer_verifies_and_installs(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    asset = release / "keyboard-killer-x86_64.AppImage"
    asset.write_text("#!/bin/sh\nexit 0\n")
    asset.chmod(0o755)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (release / "SHA256SUMS").write_text(f"{digest}  keyboard-killer-x86_64.AppImage\n")

    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = {
        **os.environ,
        "TMPDIR": str(tmp),
        "KEYBOARD_KILLER_RELEASE_BASE": f"file://{release}",
        "KEYBOARD_KILLER_APPIMAGE": "1",
        "KEYBOARD_KILLER_APPIMAGE_DIR": str(tmp_path / "app"),
        "KEYBOARD_KILLER_BIN_DIR": str(tmp_path / "bin"),
        "XDG_DATA_HOME": str(tmp_path / "share"),
    }
    result = subprocess.run(
        ["sh", str(SITE / "install.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    wrapper = tmp_path / "bin" / "keyboard-killer"
    assert wrapper.read_text().startswith("#!/bin/sh")
    assert (tmp_path / "app" / "Keyboard-Killer.AppImage").read_text().startswith("#!/bin/sh")
    desktop = tmp_path / "share" / "applications" / "keyboard-killer.desktop"
    assert "Exec=" in desktop.read_text()


def test_installer_rejects_bad_checksum(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    (release / "keyboard-killer-x86_64.AppImage").write_text("tampered")
    (release / "SHA256SUMS").write_text(
        "0000000000000000000000000000000000000000000000000000000000000000  keyboard-killer-x86_64.AppImage\n"
    )
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = {
        **os.environ,
        "TMPDIR": str(tmp),
        "KEYBOARD_KILLER_RELEASE_BASE": f"file://{release}",
        "KEYBOARD_KILLER_APPIMAGE": "1",
        "KEYBOARD_KILLER_APPIMAGE_DIR": str(tmp_path / "app"),
        "KEYBOARD_KILLER_BIN_DIR": str(tmp_path / "bin"),
        "XDG_DATA_HOME": str(tmp_path / "share"),
    }
    result = subprocess.run(
        ["sh", str(SITE / "install.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert not (tmp_path / "app" / "Keyboard-Killer.AppImage").exists()
