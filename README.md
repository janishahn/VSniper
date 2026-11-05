# VSniper

A Vinted clothes-discovery app that learns your taste from wardrobe photos and feedback, scores new listings with vision models, and sends strong matches to Telegram. Forked from [jonas-klesen/VSniper](https://github.com/jonas-klesen/VSniper).

It runs as one container: the FastAPI app serves the web UI, scans saved searches in the background, polls Telegram for button presses and replies, and keeps everything in a `/data` volume.

## Run it

```bash
cp .env.example .env   # add the Vinted cookie, Telegram bot token and chat ID, AI keys
docker compose pull
docker compose up -d
```

The app listens on `127.0.0.1:8010` only. Reach it privately through Tailscale:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8010
tailscale serve --bg --https=8443 --set-path /vinted-browser http://127.0.0.1:6080
```

The second mapping exposes the import browser at `/vinted-browser/`. If Vinted asks for verification during Add-by-URL, open the link the app shows, solve the challenge in that browser and retry. The browser profile is kept in `/data`, so this is normally a one-time step during setup.

Releases publish `ghcr.io/janishahn/vsniper` for amd64 and arm64. A host that tracks the latest release only needs `docker compose pull && docker compose up -d`.

## Release

1. Bump `version` in `pyproject.toml`, run `uv lock`, and merge that to `main`.
2. Tag the merge commit and push the tag: `git tag -a vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z`.

The release workflow smoke-tests the image, pushes it to GHCR as `X.Y.Z`, `X.Y` and `latest`, then creates the GitHub release.

## Develop

```bash
uv sync
npm ci --prefix ui
uv run dev          # API on :8000 with reload, Vite on :5173
uv run fast-tests   # Ruff, ty, pytest
npm run build --prefix ui
```

Set `RUN_WORKER=false` in a local `.env` while the deployed instance uses the same Telegram bot: Telegram delivers updates to only one poller. `uv run scan-once` runs every enabled search once, then maintenance.

To pull in changes from the original project: `git fetch upstream && git merge upstream/main`.
