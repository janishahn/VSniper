from __future__ import annotations

from vsniper.integrations.openai.pricing import compute_cost


def test_compute_cost_charges_cached_tokens_at_discounted_rate() -> None:
    # gpt-6.1-terra: input 1.0, output 5.0, cached 0.05 per 1M tokens.
    cost = compute_cost("gpt-6.1-terra", input_tokens=1000, output_tokens=500, cached_input_tokens=800)
    assert cost == (200 * 1.0 + 800 * 0.05 + 500 * 5.0) / 1_000_000
