# Keyless landing page

Static page served at `https://keyless.ohrats.party/` and deployed by Coolify.

- `index.html` / `styles.css` / `app.js`: page, theme, platform tabs, copy buttons.
- `install.sh`: the Linux installer published as a release asset and served at `/install.sh`.
- `Dockerfile` / `nginx.conf`: static container for Coolify.

The page uses the canonical OhRats UI kit (`assets.ohrats.party`), including the shared header/footer and light/dark tokens. There is no build step. The page links to stable release asset names:
`keyless_amd64.deb`, `keyless-x86_64.AppImage`, and `SHA256SUMS`.

Public artifacts are mirrored from `/data/keyless-releases` to `/releases/`; see `docs/RELEASES.md`.
