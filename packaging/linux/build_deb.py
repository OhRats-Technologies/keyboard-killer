#!/usr/bin/env python3
"""Build a self-contained Linux .deb (and optionally an AppImage).

The package bundles a relocatable Python + virtualenv under /opt/keyboard-killer
so a new user needs no terminal, no uv, and no pip.
"""

from __future__ import annotations

import argparse
import glob
import gzip
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging" / "linux"
BUILD = ROOT / "build" / "linux"
VERSION = "0.1.0"
ARCH = "amd64"
PACKAGE = "keyboard-killer"


def run(command: list[str], **kwargs) -> None:
    print("+", " ".join(command))
    subprocess.run(command, check=True, **kwargs)


def ensure_runtime(*, force: bool = False) -> Path:
    runtime = BUILD / "runtime"
    venv = runtime / "venv"
    python_dir = runtime / "python"
    if force:
        shutil.rmtree(runtime, ignore_errors=True)
    if (venv / "bin" / "python").exists() and python_dir.exists():
        try:
            subprocess.run(
                [
                    str(venv / "bin" / "python"),
                    "-c",
                    "import keyboard_killer, PySide6, onnx_asr, mediapipe, sounddevice",
                ],
                check=True,
                capture_output=True,
            )
            print("Reusing bundled runtime at", runtime)
            return runtime
        except Exception:
            print("Existing runtime is incomplete; rebuilding.")

    runtime.mkdir(parents=True, exist_ok=True)
    run(["uv", "venv", "--relocatable", "--python", "3.13", str(venv)])
    run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv / "bin" / "python"),
            "--no-cache",
            str(ROOT),
        ]
    )

    uv_python_dir = Path(
        subprocess.check_output(["uv", "python", "dir"], text=True).strip()
    )
    candidates = sorted(glob.glob(str(uv_python_dir / "cpython-3.13.*-linux-*-gnu")))
    if not candidates:
        raise SystemExit("No uv-managed CPython 3.13 found; run `uv python install 3.13`.")
    managed = Path(candidates[-1])
    print("Bundling Python from", managed)
    shutil.copytree(managed, python_dir, dirs_exist_ok=True, symlinks=True)

    bin_dir = venv / "bin"
    for name in ("python", "python3", "python3.13"):
        target = bin_dir / name
        if target.is_symlink() or target.exists():
            target.unlink()
    os.symlink("../../python/bin/python3.13", bin_dir / "python")
    os.symlink("python", bin_dir / "python3")
    os.symlink("python", bin_dir / "python3.13")
    (venv / "pyvenv.cfg").write_text(
        "home = ../python/bin\n"
        "implementation = CPython\n"
        "version_info = 3.13\n"
        "include-system-site-packages = false\n"
        "relocatable = true\n"
    )
    run(
        [
            str(bin_dir / "python"),
            "-c",
            "import keyboard_killer, PySide6, onnx_asr, mediapipe, sounddevice, keyring; "
            "print('bundled runtime ok')",
        ]
    )
    return runtime


def control_files() -> tuple[str, str, str]:
    control = f"""Package: {PACKAGE}
Version: {VERSION}
Architecture: {ARCH}
Maintainer: OhRats Technologies <support@ohrats.dev>
Section: utils
Priority: optional
Depends: libc6, libstdc++6, libgomp1, libportaudio2, libgl1, libegl1, libxkbcommon0,
 libdbus-1-3, libxcb-cursor0, libxcb-xinerama0, libxcb-icccm4, libxcb-image0,
 libxcb-keysyms1, libxcb-render-util0, libxcb-shape0, libxcb-xfixes0, libxcb-sync1,
 libxcb-xkb1, libxkbcommon-x11-0, libglib2.0-bin, python3-gi, gir1.2-atspi-2.0
Recommends: pipewire, wireplumber, firefox
Description: Gaze and voice control for the Linux desktop
 Dual-camera stereo gaze, acoustic pop-click, and local speech-to-text with an
 optional bring-your-own AI provider. Camera, speech, and history stay on this
 device. The package includes the /dev/uinput udev rule and a desktop entry.
"""
    postinst = """#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules || true
  udevadm trigger --subsystem-match=misc /dev/uinput || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q /usr/share/icons/hicolor || true
fi
exit 0
"""
    postrm = """#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules || true
fi
exit 0
"""
    return control, postinst, postrm


def add_tar(path: Path, entries: list[tuple[Path, str]], *, mode_for=None) -> None:
    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mtime = 0
        return info

    with tarfile.open(path, "w:gz", compresslevel=6) as archive:
        for source, arcname in entries:
            archive.add(source, arcname=arcname, recursive=True, filter=_filter)
            if mode_for is not None:
                mode_for(source, arcname)


def build_deb(*, bundle_venv: bool = True, output: Path | None = None) -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    stage = BUILD / "stage-deb"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)

    bin_dir = stage / "usr" / "bin"
    bin_dir.mkdir(parents=True)
    wrapper = bin_dir / PACKAGE
    if bundle_venv:
        runtime = ensure_runtime()
        opt = stage / "opt" / "keyboard-killer"
        opt.mkdir(parents=True)
        for name in ("python", "venv"):
            print(f"Copying runtime/{name}...")
            run(["cp", "-al", str(runtime / name), str(opt / name)])
        wrapper.write_text("#!/bin/sh\nexec /opt/keyboard-killer/venv/bin/python -m keyboard_killer.main \"$@\"\n")
    else:
        wrapper.write_text(
            "#!/bin/sh\n"
            "# Structure-only build: expects an installed keyboard-killer package.\n"
            "exec python3 -m keyboard_killer.main \"$@\"\n"
        )
    wrapper.chmod(0o755)

    applications = stage / "usr" / "share" / "applications"
    applications.mkdir(parents=True)
    shutil.copy2(PACKAGING / "keyboard-killer.desktop", applications / "keyboard-killer.desktop")
    icons = stage / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    icons.mkdir(parents=True)
    shutil.copy2(PACKAGING / "keyboard-killer.svg", icons / "keyboard-killer.svg")
    png_dir = stage / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    png_dir.mkdir(parents=True)
    shutil.copy2(PACKAGING / "keyboard-killer.png", png_dir / "keyboard-killer.png")

    udev = stage / "usr" / "lib" / "udev" / "rules.d"
    udev.mkdir(parents=True)
    shutil.copy2(PACKAGING / "udev" / "99-keyboard-killer-uinput.rules", udev / "99-keyboard-killer-uinput.rules")

    systemd = stage / "usr" / "lib" / "systemd" / "user"
    systemd.mkdir(parents=True)
    shutil.copy2(PACKAGING / "systemd" / "keyboard-killer.service", systemd / "keyboard-killer.service")

    doc = stage / "usr" / "share" / "doc" / PACKAGE
    doc.mkdir(parents=True)
    shutil.copy2(PACKAGING / "README.Debian", doc / "README.Debian")
    (doc / "copyright").write_text(
        "Keyboard-Killer is distributed under the repository license.\n"
        "Parakeet TDT 0.6B v2 is CC-BY-4.0 by NVIDIA; ONNX export by istupakov.\n"
    )

    run(["chmod", "-R", "a+rX", str(stage)])
    control, postinst, postrm = control_files()
    control_dir = BUILD / "control"
    shutil.rmtree(control_dir, ignore_errors=True)
    control_dir.mkdir()
    (control_dir / "control").write_text(control)
    (control_dir / "postinst").write_text(postinst)
    (control_dir / "postrm").write_text(postrm)
    (control_dir / "postinst").chmod(0o755)
    (control_dir / "postrm").chmod(0o755)

    control_tar = BUILD / "control.tar.gz"
    with tarfile.open(control_tar, "w:gz") as archive:
        for name in ("control", "postinst", "postrm"):
            archive.add(control_dir / name, arcname=f"./{name}")
    data_tar = BUILD / "data.tar.gz"
    add_tar(data_tar, [(child, f"./{child.relative_to(stage)}") for child in stage.iterdir()])

    debian_binary = BUILD / "debian-binary"
    debian_binary.write_text("2.0\n")
    target = output or (ROOT / "dist" / f"{PACKAGE}_{VERSION}_{ARCH}.deb")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    run(["ar", "rcs", str(target), str(debian_binary), str(control_tar), str(data_tar)])
    print(f"\nBuilt {target} ({target.stat().st_size / (1024**2):.1f} MB)")
    return target


def build_appimage(*, output: Path | None = None) -> Path:
    runtime = ensure_runtime()
    appdir = BUILD / "Keyboard-Killer.AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    appdir.mkdir(parents=True)
    for name in ("python", "venv"):
        run(["cp", "-al", str(runtime / name), str(appdir / name)])
    shutil.copy2(PACKAGING / "keyboard-killer.desktop", appdir / "keyboard-killer.desktop")
    shutil.copy2(PACKAGING / "keyboard-killer.svg", appdir / "keyboard-killer.svg")
    shutil.copy2(PACKAGING / "keyboard-killer.png", appdir / ".DirIcon")
    apprun = appdir / "AppRun"
    apprun.write_text(
        "#!/bin/sh\n"
        "HERE=$(dirname \"$(readlink -f \"$0\")\")\n"
        "if [ \"$#\" -eq 0 ]; then set -- ui; fi\n"
        "exec \"$HERE/venv/bin/python\" -m keyboard_killer.main \"$@\"\n"
    )
    apprun.chmod(0o755)
    run(["chmod", "-R", "a+rX", str(appdir)])

    tool = BUILD / "appimagetool.AppImage"
    if not tool.exists():
        print("Downloading appimagetool...")
        run(
            [
                "curl",
                "-sL",
                "-o",
                str(tool),
                "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage",
            ]
        )
        tool.chmod(0o755)
    target = output or (ROOT / "dist" / f"Keyboard-Killer-{VERSION}-x86_64.AppImage")
    target.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1"}
    run(
        [str(tool), "--appimage-extract-and-run", str(appdir), str(target)],
        env=env,
    )
    print(f"\nBuilt {target} ({target.stat().st_size / (1024**2):.1f} MB)")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-venv", action="store_true", help="structure-only .deb for inspection")
    parser.add_argument("--appimage", action="store_true", help="also build the AppImage")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--force-runtime", action="store_true")
    args = parser.parse_args()
    if not args.no_venv:
        ensure_runtime(force=args.force_runtime)
    deb = build_deb(bundle_venv=not args.no_venv, output=args.output)
    if args.appimage:
        build_appimage()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
