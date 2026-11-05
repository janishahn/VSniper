from __future__ import annotations

from vsniper.integrations.openai.pricing import compute_cost


def test_compute_cost_charges_cached_tokens_at_discounted_rate() -> None:
    # gpt-5: input 1.25, output 10.0, cached 0.125 per 1M tokens.
    # 1000 input of which 800 cached, 500 output:
    #   non-cached 200 * 1.25 + cached 800 * 0.125 + output 500 * 10.0 = 250 + 100 + 5000 = 5350 / 1e6
    cost = compute_cost("gpt-5", input_tokens=1000, output_tokens=500, cached_input_tokens=800)
    assert cost == (200 * 1.25 + 800 * 0.125 + 500 * 10.0) / 1_000_000
