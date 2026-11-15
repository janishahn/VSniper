from __future__ import annotations

from vsniper.core.state import get_state


def run_once() -> int:
    """Embed the next listing covers and retrain stale taste heads. Returns the listings settled."""
    service = get_state().local_taste
    settled = service.embed_pending()
    service.train_due_heads()
    return settled
