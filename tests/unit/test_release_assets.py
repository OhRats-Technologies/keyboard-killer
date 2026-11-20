"""Release/version/installer checks that do not need GitHub or root."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"


def test_version_script_matches_pyproject() -> None:
    version = re.search(
        r'^version = "([^"]+)"$', (ROOT / "pyproject.toml").read_text(), flags=re.MULTILINE
    ).group(1)
    result = subprocess.run(
        [str(ROOT / "scripts" / "check-version.sh"), version],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == version
    branch = subprocess.run(
        [str(ROOT / "scripts" / "check-version.sh"), version],
        cwd=ROOT,
        env={**os.environ, "GITHUB_REF": "refs/heads/main", "GITHUB_REF_NAME": "main"},
        capture_output=True,
        text=True,
    )
    assert branch.returncode == 0


def test_whisper_is_not_a_dependency() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text().lower()
    assert "faster-whisper" not in pyproject
    assert "whisper" not in (ROOT / "src" / "keyless" / "config" / "schema.py").read_text().lower()


def test_bundled_fonts_exist() -> None:
    fonts = ROOT / "src" / "keyless" / "ui" / "theme" / "fonts"
    assert (fonts / "Inter.ttf").stat().st_size > 100_000
    assert (fonts / "SpaceMono-Regular.ttf").stat().st_size > 50_000
    assert (fonts / "OFL-Inter.txt").exists()


def test_landing_page_links_stable_assets() -> None:
    html = (SITE / "index.html").read_text()
    assert "https://assets.ohrats.party/v1/ohrats.css" in html
    assert "https://assets.ohrats.party/v1/landing.css" in html
    assert 'class="site-nav"' in html
    assert "assets.ohrats.party/v1/logo.svg" in html
    assert "install.sh" in html
    assert 'href="/docs.html"' in html
    assert "github.com/OhRats-Technologies/keyless/releases/latest/download" in html
    assert "keyless_amd64.deb" in html
    docs = (SITE / "docs.html").read_text()
    assert "One or two stable V4L2 cameras" in docs
    assert "not implemented yet" in docs
    assert "keyless-x86_64.AppImage" in html
    assert "macOS is not available yet" in html
    assert "Windows is not available yet" in html


def test_appimage_installer_verifies_and_installs(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    asset = release / "keyless-x86_64.AppImage"
    asset.write_text("#!/bin/sh\nexit 0\n")
    asset.chmod(0o755)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (release / "SHA256SUMS").write_text(f"{digest}  keyless-x86_64.AppImage\n")

    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = {
        **os.environ,
        "TMPDIR": str(tmp),
        "KEYLESS_RELEASE_BASE": f"file://{release}",
        "KEYLESS_APPIMAGE": "1",
        "KEYLESS_APPIMAGE_DIR": str(tmp_path / "app"),
        "KEYLESS_BIN_DIR": str(tmp_path / "bin"),
        "XDG_DATA_HOME": str(tmp_path / "share"),
    }
    result = subprocess.run(
        ["sh", str(SITE / "install.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    wrapper = tmp_path / "bin" / "keyless"
    assert wrapper.read_text().startswith("#!/bin/sh")
    assert (tmp_path / "app" / "Keyless.AppImage").read_text().startswith("#!/bin/sh")
    desktop = tmp_path / "share" / "applications" / "keyless.desktop"
    assert "Exec=" in desktop.read_text()


def test_installer_rejects_bad_checksum(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    (release / "keyless-x86_64.AppImage").write_text("tampered")
    (release / "SHA256SUMS").write_text(
        "0000000000000000000000000000000000000000000000000000000000000000  keyless-x86_64.AppImage\n"
    )
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env = {
        **os.environ,
        "TMPDIR": str(tmp),
        "KEYLESS_RELEASE_BASE": f"file://{release}",
        "KEYLESS_APPIMAGE": "1",
        "KEYLESS_APPIMAGE_DIR": str(tmp_path / "app"),
        "KEYLESS_BIN_DIR": str(tmp_path / "bin"),
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
    assert not (tmp_path / "app" / "Keyless.AppImage").exists()


def test_local_install_and_uninstall_scripts(tmp_path: Path) -> None:
    fake_venv = tmp_path / "venv"
    (fake_venv / "bin").mkdir(parents=True)
    fake_py = fake_venv / "bin" / "python"
    fake_py.write_text("#!/bin/sh\nexit 0\n")
    fake_py.chmod(0o755)

    env = {
        **os.environ,
        "KEYLESS_BIN_DIR": str(tmp_path / "bin"),
        "XDG_DATA_HOME": str(tmp_path / "share"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "KEYLESS_VENV_DIR": str(fake_venv),
    }
    res_in = subprocess.run(
        ["sh", str(ROOT / "scripts" / "install.sh"), "--skip-sync"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_in.returncode == 0, res_in.stderr
    assert (tmp_path / "bin" / "keyless").exists()
    assert (tmp_path / "share" / "applications" / "keyless.desktop").exists()
    assert (tmp_path / "share" / "icons" / "hicolor" / "scalable" / "apps" / "keyless.svg").exists()

    res_out = subprocess.run(
        ["sh", str(ROOT / "scripts" / "uninstall.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_out.returncode == 0, res_out.stderr
    assert not (tmp_path / "bin" / "keyless").exists()
    assert not (tmp_path / "share" / "applications" / "keyless.desktop").exists()
    assert not (tmp_path / "share" / "icons" / "hicolor" / "scalable" / "apps" / "keyless.svg").exists()
