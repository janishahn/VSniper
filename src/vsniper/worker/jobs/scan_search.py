from __future__ import annotations

from vsniper.core.state import get_state
from vsniper.domain.contracts import SearchRunResult


def run_once(user_id: int, search_id: str) -> SearchRunResult:
    return get_state().searches.run_live(user_id, search_id, already_claimed=True, trigger="worker")
