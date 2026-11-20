# Releases

Keyboard-Killer follows the same shape as OhRats RC: the tag is the release
boundary, CI must be green on the tagged commit, and the assets are built from
that exact commit.

## Version

`pyproject.toml` and the newest `CHANGELOG.md` entry must agree:

```sh
VERSION="0.1.0"
./scripts/check-version.sh "$VERSION"
```

The release workflow calls the same script with the tag name; a mismatch fails
before anything is published.

## Publish

1. Bump `pyproject.toml` and add the `CHANGELOG.md` entry.
2. Commit and push `main`.
3. Wait for the `CI` workflow to pass on that commit.
4. Tag and push:

```sh
git tag -a v0.1.0 -m "Keyboard-Killer 0.1.0"
git push origin v0.1.0
```

`.github/workflows/release.yml` then:

- verifies the tag matches `pyproject.toml` and the commit is on `main`;
- waits for a successful `CI` run on the tagged SHA;
- builds `keyboard-killer_amd64.deb` and `keyboard-killer-x86_64.AppImage`
  with `packaging/linux/build_deb.py --force-runtime --appimage`;
- writes `SHA256SUMS` and copies `site/install.sh`;
- mirrors the public assets to `/data/keyboard-killer-releases` on the
  Coolify host (served at `https://keyboard-killer.ohrats.party/releases/`);
- creates the GitHub release as a draft, uploads the assets, and publishes it;
- runs the public installer from the site mirror twice (fresh + idempotent).

## Public mirror

The source repo is private, so GitHub release asset URLs are not anonymously
downloadable. The landing page and installer use the public mirror instead:

```
https://keyboard-killer.ohrats.party/releases/keyboard-killer_amd64.deb
https://keyboard-killer.ohrats.party/releases/keyboard-killer-x86_64.AppImage
https://keyboard-killer.ohrats.party/releases/SHA256SUMS
```

The site container mounts `/data/keyboard-killer-releases` at
`/usr/share/nginx/html/releases`. The release workflow authenticates with the
`COOLIFY_SSH_KEY` and `COOLIFY_HOST` repository secrets. The deploy key is
restricted to the Coolify host and is only used for this upload.

## Verify

```sh
gh release view v0.1.0 --repo OhRats-Technologies/keyboard-killer \
  --json tagName,isDraft --jq '.tagName+" draft=\(.isDraft)"'
curl -fsS https://keyboard-killer.ohrats.party/releases/SHA256SUMS
curl -fsSIL https://keyboard-killer.ohrats.party/releases/keyboard-killer_amd64.deb
curl -fsSL https://keyboard-killer.ohrats.party/install.sh | sh
```

Manual install smoke without root:

```sh
TMPDIR="$(pwd)/build/install-tmp" \
KEYBOARD_KILLER_APPIMAGE=1 \
KEYBOARD_KILLER_APPIMAGE_DIR="$(pwd)/build/install-app" \
KEYBOARD_KILLER_BIN_DIR="$(pwd)/build/install-bin" \
sh site/install.sh
APPIMAGE_EXTRACT_AND_RUN=1 build/install-bin/keyboard-killer --help
```

## Traps

- **Never `gh release create` for a version whose tag you will push.** The
  workflow owns the release.
- **Do not skip `--force-runtime`.** Reusing a cached runtime after source
  changes ships stale Python files.
- **The public mirror is the install path.** Do not point the landing page or
  installer at `github.com/.../releases` while the repo is private.
- **Tag commits must be on `main`.** The verify job checks ancestry.
- **Fix forward after publishing.** Do not rewrite a published tag; bump and
  release a new version.

## Rollback

Keep the previous release assets in `/data/keyboard-killer-releases` until the
new version passes the install smoke. The installer always verifies
`SHA256SUMS`; a rollback is a restore of the previous three files plus
`install.sh`.
