# VSniper

A Vinted clothes-discovery app that learns your taste from wardrobe photos and feedback, scores new listings with vision models, and sends strong matches to Telegram. Forked from [jonas-klesen/VSniper](https://github.com/jonas-klesen/VSniper).

It runs as one container: the FastAPI app serves the web UI, scans saved searches in the background, polls Telegram for button presses and replies, and keeps everything in a `/data` volume.

Each 👍/👎 is stored with a one-line lesson about what drove it. The taste profile rebuilds itself at most once a day, once three new verdicts have arrived (or one has waited a week); newer verdicts reach the judge as examples until then. Once a day the bot also sends the best listing that scored just under the alert threshold, since alerts alone never show what it scores too low.

Clothing buckets (Schuhe, Hosen, Ringe, …) are managed on the Buckets page. Each bucket covers chosen categories from Vinted's Damen or Herren catalog and the sizes to search for, and gets its own search and taste profile. A bucket with history is archived instead of deleted.

Several people can share one installation. Every Telegram chat listed in `TELEGRAM_CHAT_IDS` gets its own profile with separate buckets, searches, taste and alerts; the bot ignores any other chat. The first chat in the list receives error notices. A new chat starts with a copy of the first profile's buckets. Profiles are managed on the Profiles page, and the web UI has no login, so keep it private.

Vinted is read as an anonymous visitor, so no Vinted account or cookie is needed. Scans use the catalog search; each listing that has not been judged before is then loaded from its item page for the description and all photos.

## Run it

```bash
cp .env.example .env   # add the Telegram bot token, the allowed chat IDs and the OpenAI key
docker compose pull
docker compose up -d
```

The app listens on `127.0.0.1:8010` only. Reach it privately through Tailscale:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8010
```

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

`uv run dev` runs without the background worker, so it never competes with the deployed instance for Telegram updates. `uv run scan-once` runs every enabled search once, then maintenance.

To pull in changes from the original project: `git fetch upstream && git merge upstream/main`.
