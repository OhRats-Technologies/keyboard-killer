# Releases

Keyless follows the same shape as OhRats RC: the tag is the release
boundary, CI must be green on the tagged commit, and the assets are built from
that exact commit.

## Version

`pyproject.toml` and the newest `CHANGELOG.md` entry must agree:

```sh
VERSION="0.1.2"
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
git tag -a v0.1.2 -m "Keyless 0.1.2"
git push origin v0.1.2
```

`.github/workflows/release.yml` then:

- verifies the tag matches `pyproject.toml` and the commit is on `main`;
- waits for a successful `CI` run on the tagged SHA;
- builds `keyless_amd64.deb` and `keyless-x86_64.AppImage`
  with `packaging/linux/build_deb.py --force-runtime --appimage`;
- writes `SHA256SUMS` and copies `site/install.sh`;
- mirrors the public assets to `/data/keyless-releases` on the
  Coolify host (served at `https://keyless.ohrats.party/releases/`);
- creates the GitHub release as a draft, uploads the assets, and publishes it;
- runs the public installer from the site mirror twice (fresh + idempotent).

## Public mirror

The repository and release assets are public, so the primary download path is
GitHub:

```
https://github.com/OhRats-Technologies/keyless/releases/latest/download/keyless_amd64.deb
https://github.com/OhRats-Technologies/keyless/releases/latest/download/keyless-x86_64.AppImage
https://github.com/OhRats-Technologies/keyless/releases/latest/download/SHA256SUMS
```

The release workflow also mirrors the same files to
`https://keyless.ohrats.party/releases/` as a fast CDN/fallback. The
site container mounts `/data/keyless-releases` at
`/usr/share/nginx/html/releases`. The mirror upload authenticates with the
`COOLIFY_SSH_KEY` and `COOLIFY_HOST` repository secrets.

## Verify

```sh
gh release view v0.1.2 --repo OhRats-Technologies/keyless \
  --json tagName,isDraft --jq '.tagName+" draft=\(.isDraft)"'
curl -fsS https://keyless.ohrats.party/releases/SHA256SUMS
curl -fsSIL https://keyless.ohrats.party/releases/keyless_amd64.deb
curl -fsSL https://keyless.ohrats.party/install.sh | sh
```

Manual install smoke without root:

```sh
TMPDIR="$(pwd)/build/install-tmp" \
KEYLESS_APPIMAGE=1 \
KEYLESS_APPIMAGE_DIR="$(pwd)/build/install-app" \
KEYLESS_BIN_DIR="$(pwd)/build/install-bin" \
sh site/install.sh
APPIMAGE_EXTRACT_AND_RUN=1 build/install-bin/keyless --help
```

## Traps

- **Never `gh release create` for a version whose tag you will push.** The
  workflow owns the release.
- **Do not skip `--force-runtime`.** Reusing a cached runtime after source
  changes ships stale Python files.
- **GitHub is the install path now that the repo is public.** Keep the site
  mirror in sync as a fallback.
- **Tag commits must be on `main`.** The verify job checks ancestry.
- **Fix forward after publishing.** Do not rewrite a published tag; bump and
  release a new version.

## Rollback

Keep the previous release assets in `/data/keyless-releases` until the
new version passes the install smoke. The installer always verifies
`SHA256SUMS`; a rollback is a restore of the previous three files plus
`install.sh`.
