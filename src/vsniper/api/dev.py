"""The app for `uv run dev`: no background worker, so it never takes Telegram updates from the
deployed instance. `uv run scan-once` scans by hand."""

from vsniper.api.main import app

app.state.run_worker = False
