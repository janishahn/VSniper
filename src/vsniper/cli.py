from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def dev() -> None:
    """Run the API with reload and the Vite dev server; the UI proxies /api to the API."""
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "vsniper.api.main:app", "--reload", "--reload-dir", "src",
         "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT,
    )
    ui = subprocess.Popen(["npm", "run", "dev", "--", "--host", "127.0.0.1"], cwd=ROOT / "ui")
    try:
        sys.exit(api.wait())
    except KeyboardInterrupt:
        pass
    finally:
        for process in (ui, api):
            process.terminate()
            process.wait()


def fast_tests() -> None:
    """Lint, type-check and run the backend test suite."""
    for command in (
        ["ruff", "check", "src", "tests"],
        ["mypy", "src"],
        ["pytest", "tests", *sys.argv[1:]],
    ):
        result = subprocess.run([sys.executable, "-m", *command], cwd=ROOT)
        if result.returncode:
            sys.exit(result.returncode)
