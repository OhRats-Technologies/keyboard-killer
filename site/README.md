# Keyboard-Killer landing page

Static page served at `https://keyboard-killer.ohrats.party/` and deployed by Coolify.

- `index.html` / `styles.css` / `app.js`: page, theme, platform tabs, copy buttons.
- `install.sh`: the Linux installer published as a release asset and served at `/install.sh`.
- `Dockerfile` / `nginx.conf`: static container for Coolify.

There is no build step. The page links to stable release asset names:
`keyboard-killer_amd64.deb`, `keyboard-killer-x86_64.AppImage`, and `SHA256SUMS`.
