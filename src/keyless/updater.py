"""Background update checks, release notes, and installers modeled on pingdotgg/t3code."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from typing import Any, Literal

import httpx

from keyless.config.paths import AppPaths, current_paths
from keyless.config.schema import AppConfig

GITHUB_REPO = "OhRats-Technologies/keyless"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
MIRROR_VERSION_URL = "https://keyless.ohrats.party/releases/version.json"
MIRROR_BASE_URL = "https://keyless.ohrats.party/releases"

AUTO_UPDATE_POLL_INTERVAL = timedelta(hours=4)

UpdateChannel = Literal["stable", "prerelease"]
UpdateStatus = Literal[
    "idle",
    "checking",
    "available",
    "up_to_date",
    "downloading",
    "downloaded",
    "error",
]
InstallMode = Literal["source_git", "appimage", "deb", "wheel"]

_PRERELEASE_RE = re.compile(r"-(?:nightly|preview|alpha|beta|rc)(?:[.\-]|$)", re.IGNORECASE)
_SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def current_version() -> str:
    """Return the running Keyless version from metadata or pyproject fallback."""
    try:
        return pkg_version("keyless")
    except PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if pyproject.exists():
            m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), flags=re.MULTILINE)
            if m:
                return m.group(1)
    return "0.1.0"


def is_prerelease_version(version: str) -> bool:
    cleaned = version.strip().lstrip("v")
    return bool(_PRERELEASE_RE.search(cleaned)) or "-" in cleaned


def _parse_semver(version: str) -> tuple[int, int, int, tuple[tuple[int, int | str], ...]] | None:
    m = _SEMVER_RE.match(version.strip())
    if not m:
        return None
    pre = m.group("pre")
    if not pre:
        return (int(m.group("major")), int(m.group("minor")), int(m.group("patch")), ())
    parts: list[tuple[int, int | str]] = []
    for token in pre.split("."):
        if token.isdigit():
            parts.append((0, int(token)))
        else:
            parts.append((1, token.lower()))
    return (int(m.group("major")), int(m.group("minor")), int(m.group("patch")), tuple(parts))


def compare_semver(left: str, right: str) -> int:
    """Return -1 if left < right, 0 if equal, +1 if left > right."""
    a = _parse_semver(left)
    b = _parse_semver(right)
    if a is None or b is None:
        la, lb = left.strip().lstrip("v"), right.strip().lstrip("v")
        return (la > lb) - (la < lb)
    if a[:3] != b[:3]:
        return 1 if a[:3] > b[:3] else -1
    if not a[3] and not b[3]:
        return 0
    if not a[3]:
        return 1
    if not b[3]:
        return -1
    return 1 if a[3] > b[3] else (-1 if a[3] < b[3] else 0)


def normalize_channel(channel: str | None) -> UpdateChannel:
    return "prerelease" if (channel or "").strip().lower() == "prerelease" else "stable"


def clean_release_notes(body: str | None, *, max_items: int = 8, max_len: int = 180) -> list[str]:
    """Filter GitHub release markdown into clean user-facing bullets (t3code style)."""
    if not body:
        return []
    items: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", plain).strip()
        low = plain.lower().strip("#*` ")
        if (
            not low
            or low in ("what's changed", "whats changed", "changelog")
            or low.startswith("full changelog:")
            or low.startswith("compare:")
            or "/compare/" in low
        ):
            continue
        if plain.startswith("#"):
            continue
        plain = re.sub(r"^[-*]\s+", "", plain)
        plain = re.sub(r"\s*\((?:#\d+(?:,\s*)?)+\)\.?$", "", plain).strip()
        if not plain:
            continue
        if len(plain) > max_len:
            plain = plain[: max_len - 3].rstrip() + "..."
        items.append(plain)
        if len(items) >= max_items:
            break
    return items


@dataclass
class UpdateState:
    status: UpdateStatus = "idle"
    channel: UpdateChannel = "stable"
    current_version: str = field(default_factory=current_version)
    available_version: str | None = None
    downloaded_version: str | None = None
    downloaded_path: str | None = None
    release_url: str = ""
    released_at: str = ""
    asset_name: str = ""
    asset_url: str = ""
    asset_sha256: str = ""
    release_notes: list[str] = field(default_factory=list)
    checked_at: str | None = None
    download_percent: float | None = None
    install_mode: InstallMode = "wheel"
    message: str = ""
    can_retry: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UpdateState:
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


def detect_install_mode(repo_root: Path | None = None) -> tuple[InstallMode, Path | None]:
    """Identify how Keyless is installed so updates apply cleanly."""
    appimage = os.environ.get("APPIMAGE")
    if appimage and Path(appimage).exists():
        return "appimage", Path(appimage)

    root = repo_root or Path(__file__).resolve().parents[2]
    if (root / ".git").exists() and (root / "scripts" / "install.sh").exists():
        return "source_git", root

    if Path("/usr/lib/keyless").exists():
        return "deb", Path("/usr/lib/keyless")

    return "wheel", None


def parse_sha256sums(text: str) -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            name = parts[-1].lstrip("*").split("/")[-1]
            sums[name] = parts[0].lower()
    return sums


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class Updater:
    """State-tracked desktop & CLI updater modeled after pingdotgg/t3code."""

    def __init__(
        self,
        *,
        paths: AppPaths | None = None,
        channel: UpdateChannel = "stable",
        repo_root: Path | None = None,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.paths = paths or current_paths()
        self.mode, self.install_root = detect_install_mode(repo_root)
        self._client_factory = client_factory or (
            lambda: httpx.Client(
                timeout=httpx.Timeout(12.0, connect=8.0),
                follow_redirects=True,
                headers={"Accept": "application/vnd.github+json", "User-Agent": f"Keyless/{current_version()}"},
            )
        )
        self.state = self._load_state(channel)

    @property
    def state_path(self) -> Path:
        return self.paths.data_dir / "update-state.json"

    def _load_state(self, channel: UpdateChannel) -> UpdateState:
        ver = current_version()
        if self.state_path.exists():
            try:
                loaded = UpdateState.from_dict(json.loads(self.state_path.read_text()))
                loaded.current_version = ver
                loaded.channel = normalize_channel(channel)
                loaded.install_mode = self.mode
                if loaded.available_version and compare_semver(loaded.available_version, ver) <= 0:
                    loaded.status = "up_to_date"
                    loaded.available_version = None
                    loaded.downloaded_version = None
                    loaded.downloaded_path = None
                return loaded
            except Exception:
                pass
        return UpdateState(
            status="idle",
            channel=normalize_channel(channel),
            current_version=ver,
            install_mode=self.mode,
        )

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state.to_dict(), indent=2) + "\n")
        tmp.replace(self.state_path)

    def should_auto_check(self, config: AppConfig, now: datetime | None = None) -> bool:
        if not getattr(config, "auto_update_checks", True):
            return False
        if not self.state.checked_at:
            return True
        try:
            last = datetime.fromisoformat(self.state.checked_at)
        except ValueError:
            return True
        ref = now or datetime.now(timezone.utc)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (ref - last) >= AUTO_UPDATE_POLL_INTERVAL

    def skip_version(self, version: str, config: AppConfig) -> None:
        config.skipped_update_version = version.strip().lstrip("v")
        config.save(self.paths.settings_path)

    def _select_release(
        self,
        releases: list[dict[str, Any]],
        channel: UpdateChannel,
    ) -> dict[str, Any] | None:
        candidates: list[tuple[str, dict[str, Any]]] = []
        for rel in releases:
            if rel.get("draft"):
                continue
            tag = str(rel.get("tag_name") or "").strip()
            ver = tag.lstrip("v")
            if not _parse_semver(ver):
                continue
            is_pre = bool(rel.get("prerelease")) or is_prerelease_version(ver)
            if channel == "stable" and is_pre:
                continue
            candidates.append((ver, rel))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (_parse_semver(item[0]) or (0, 0, 0, ())), reverse=True)
        # Prefer proper compare_semver ordering for best prerelease handling.
        best_ver, best_rel = candidates[0]
        for ver, rel in candidates[1:]:
            if compare_semver(ver, best_ver) > 0:
                best_ver, best_rel = ver, rel
        return best_rel

    def _preferred_asset_name(self) -> str:
        return "keyless-x86_64.AppImage" if self.mode == "appimage" else "keyless_amd64.deb"

    def check_for_updates(self, *, channel: UpdateChannel | None = None) -> UpdateState:
        chosen_channel = normalize_channel(channel or self.state.channel)
        checked_at = datetime.now(timezone.utc).isoformat()
        self.state.status = "checking"
        self.state.channel = chosen_channel
        self.state.current_version = current_version()
        self.state.install_mode = self.mode
        self.state.message = "Checking for updates..."

        try:
            with self._client_factory() as client:
                release = self._fetch_latest_release(client, chosen_channel)
                if release is None:
                    self.state.status = "up_to_date"
                    self.state.available_version = None
                    self.state.checked_at = checked_at
                    self.state.message = f"Keyless {self.state.current_version} is up to date."
                    self.state.can_retry = False
                    self._save_state()
                    return self.state

                latest_ver = str(release.get("tag_name") or "").strip().lstrip("v")
                if compare_semver(latest_ver, self.state.current_version) <= 0:
                    self.state.status = "up_to_date"
                    self.state.available_version = None
                    self.state.release_notes = []
                    self.state.checked_at = checked_at
                    self.state.message = f"Keyless {self.state.current_version} is up to date."
                    self.state.can_retry = False
                    self._save_state()
                    return self.state

                asset_name, asset_url, sums_url = self._pick_release_asset(release)
                sha = self._fetch_asset_sha256(client, sums_url, asset_name)
                notes = clean_release_notes(str(release.get("body") or ""))

                self.state.status = "available"
                self.state.available_version = latest_ver
                self.state.release_url = str(release.get("html_url") or "")
                self.state.released_at = str(release.get("published_at") or "")
                self.state.asset_name = asset_name
                self.state.asset_url = asset_url
                self.state.asset_sha256 = sha
                self.state.release_notes = notes
                self.state.checked_at = checked_at
                self.state.message = f"Keyless {latest_ver} is available."
                self.state.can_retry = False
                self._save_state()
                return self.state
        except Exception as exc:
            self.state.status = "error"
            self.state.checked_at = checked_at
            self.state.message = f"Update check failed: {exc}"
            self.state.can_retry = True
            self._save_state()
            return self.state

    def _fetch_latest_release(
        self,
        client: httpx.Client,
        channel: UpdateChannel,
    ) -> dict[str, Any] | None:
        resp = client.get(RELEASES_API_URL, params={"per_page": "12"})
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return self._select_release(data, channel)
        if channel == "stable":
            mirror = client.get(MIRROR_VERSION_URL)
            if mirror.status_code == 200:
                payload = mirror.json()
                tag = str(payload.get("version") or "").strip()
                if tag:
                    return {
                        "tag_name": tag,
                        "prerelease": False,
                        "draft": False,
                        "html_url": f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}",
                        "body": "",
                        "assets": [
                            {
                                "name": "keyless_amd64.deb",
                                "browser_download_url": f"{MIRROR_BASE_URL}/keyless_amd64.deb",
                            },
                            {
                                "name": "keyless-x86_64.AppImage",
                                "browser_download_url": f"{MIRROR_BASE_URL}/keyless-x86_64.AppImage",
                            },
                            {
                                "name": "SHA256SUMS",
                                "browser_download_url": f"{MIRROR_BASE_URL}/SHA256SUMS",
                            },
                        ],
                    }
        resp.raise_for_status()
        return None

    def _pick_release_asset(self, release: dict[str, Any]) -> tuple[str, str, str]:
        preferred = self._preferred_asset_name()
        assets = release.get("assets") or []
        by_name: dict[str, str] = {}
        for asset in assets:
            name = str(asset.get("name") or "")
            url = str(asset.get("browser_download_url") or "")
            if name and url:
                by_name[name] = url
        sums_url = by_name.get("SHA256SUMS", "")
        if preferred in by_name:
            return preferred, by_name[preferred], sums_url
        for fallback in ("keyless-x86_64.AppImage", "keyless_amd64.deb"):
            if fallback in by_name:
                return fallback, by_name[fallback], sums_url
        return "", "", sums_url

    def _fetch_asset_sha256(self, client: httpx.Client, sums_url: str, asset_name: str) -> str:
        if not sums_url or not asset_name:
            return ""
        try:
            resp = client.get(sums_url)
            if resp.status_code == 200:
                return parse_sha256sums(resp.text).get(asset_name, "")
        except Exception:
            pass
        return ""

    def download_update(self, progress: Callable[[float], None] | None = None) -> Path:
        """Download the update package into cache and verify SHA-256."""
        if not self.state.asset_url or not self.state.available_version:
            raise RuntimeError("No downloadable update is currently available.")
        dest_dir = self.paths.downloads_dir / "updates" / self.state.available_version
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = self.state.asset_name or "keyless-update"
        target = dest_dir / filename
        partial = dest_dir / f"{filename}.partial"

        if target.exists() and (not self.state.asset_sha256 or sha256_file(target) == self.state.asset_sha256):
            self.state.status = "downloaded"
            self.state.downloaded_version = self.state.available_version
            self.state.downloaded_path = str(target)
            self.state.download_percent = 100.0
            self.state.message = f"Update {self.state.available_version} downloaded."
            self._save_state()
            return target

        self.state.status = "downloading"
        self.state.download_percent = 0.0
        self.state.message = f"Downloading Keyless {self.state.available_version}..."
        self._save_state()

        try:
            with self._client_factory() as client:
                with client.stream("GET", self.state.asset_url) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length") or 0)
                    done = 0
                    with partial.open("wb") as handle:
                        for chunk in resp.iter_bytes(chunk_size=1024 * 64):
                            handle.write(chunk)
                            done += len(chunk)
                            if total > 0:
                                pct = min(100.0, 100.0 * done / total)
                                self.state.download_percent = pct
                                if progress is not None:
                                    progress(pct)
            if self.state.asset_sha256:
                actual = sha256_file(partial)
                if actual != self.state.asset_sha256:
                    partial.unlink(missing_ok=True)
                    raise RuntimeError(
                        f"Checksum mismatch for {filename}: expected {self.state.asset_sha256[:12]}, got {actual[:12]}"
                    )
            partial.replace(target)
            if filename.endswith(".AppImage"):
                target.chmod(0o755)
            self.state.status = "downloaded"
            self.state.downloaded_version = self.state.available_version
            self.state.downloaded_path = str(target)
            self.state.download_percent = 100.0
            self.state.message = f"Update {self.state.available_version} ready to install."
            self._save_state()
            return target
        except Exception as exc:
            self.state.status = "available" if self.state.available_version else "error"
            self.state.message = f"Download failed: {exc}"
            self.state.can_retry = True
            self._save_state()
            raise

    def apply_update(
        self,
        *,
        progress: Callable[[float], None] | None = None,
    ) -> tuple[bool, str]:
        """Apply an available update according to the active installation mode."""
        if self.mode == "source_git" and self.install_root is not None:
            return self._apply_git_update(self.install_root)

        if not self.state.available_version:
            return False, "No update is currently available."

        pkg_path = self.download_update(progress=progress)
        if self.mode == "appimage" and self.install_root is not None:
            tmp = self.install_root.with_name(self.install_root.name + ".new")
            shutil.copy2(pkg_path, tmp)
            tmp.chmod(0o755)
            os.replace(tmp, self.install_root)
            return True, f"Updated AppImage to Keyless {self.state.available_version}. Restart Keyless to run the new version."

        if pkg_path.suffix == ".deb":
            if os.geteuid() == 0:
                cmd = ["dpkg", "-i", str(pkg_path)]
            elif shutil.which("pkexec"):
                cmd = ["pkexec", "apt-get", "install", "-y", str(pkg_path)]
            else:
                return (
                    True,
                    f"Downloaded {pkg_path}. Install with: sudo apt install {pkg_path}",
                )
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()
                return False, f"Package install failed: {err or proc.returncode}"
            return True, f"Installed Keyless {self.state.available_version}. Restart Keyless to apply."

        return True, f"Downloaded update to {pkg_path}."

    def _apply_git_update(self, root: Path) -> tuple[bool, str]:
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if dirty.returncode != 0:
            return False, f"Git check failed: {dirty.stderr.strip()}"
        if dirty.stdout.strip():
            return (
                False,
                f"Local repository at {root} has uncommitted changes; commit or stash before updating.",
            )
        pull = subprocess.run(
            ["git", "-C", str(root), "pull", "--ff-only", "origin", "main"],
            capture_output=True,
            text=True,
            check=False,
        )
        if pull.returncode != 0:
            return False, f"git pull failed: {(pull.stderr or pull.stdout).strip()}"
        install = subprocess.run(
            [str(root / "scripts" / "install.sh")],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        if install.returncode != 0:
            return False, f"install.sh failed: {(install.stderr or install.stdout).strip()}"
        return True, "Updated source installation and rebuilt CLI/desktop launcher. Restart Keyless to apply."
