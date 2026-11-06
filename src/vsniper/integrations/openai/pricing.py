from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)
_warned_unknown: set[str] = set()

# Standard tier prices per 1M tokens: model -> (input_usd, output_usd, cached_input_usd).
# The Decisions API bills no output tokens.
_PRICING: dict[str, tuple[float, float, float]] = {
    "gpt-6.1-terra": (1.0, 5.0, 0.05),
    "gpt-6-luna": (0.10, 0.0, 0.01),
}


def _family_pricing(model: str) -> tuple[float, float, float] | None:
    """Return pricing for the longest known prefix of *model* (e.g. 'gpt-4o-2024-11' → 'gpt-4o')."""
    parts = model.split("-")
    for length in range(len(parts), 0, -1):
        candidate = "-".join(parts[:length])
        if candidate in _PRICING:
            return _PRICING[candidate]
    return None


def compute_cost(model: str, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0) -> float:
    """Return estimated USD cost for a call.

    Cached input tokens are billed at the model's discounted cached rate; the remaining
    (non-cached) input tokens and all output tokens are billed at the standard rate. Logs a
    warning once for unknown models, which report $0.
    """
    pricing = _PRICING.get(model)
    if pricing is None:
        if model not in _warned_unknown:
            _warned_unknown.add(model)
            family = _family_pricing(model)
            if family:
                _logger.warning(
                    "Unknown model %r not in pricing table; estimating cost from nearest family match",
                    model,
                )
            else:
                _logger.warning(
                    "Unknown model %r not in pricing table and no family match found; cost reported as $0",
                    model,
                )
        pricing = _family_pricing(model)
        if pricing is None:
            return 0.0
    input_price, output_price, cached_price = pricing
    cached = max(0, min(cached_input_tokens, input_tokens))
    non_cached = input_tokens - cached
    return (
        non_cached * input_price + cached * cached_price + output_tokens * output_price
    ) / 1_000_000
