"""Tests for the blocked-brands hard filter applied before VLM judging."""
from __future__ import annotations

from vsniper.services.search_service import _filter_blocked_brands


def test_filter_blocked_brands_drops_case_insensitive_matches() -> None:
    candidates = [
        {"id": "1", "brand": "Nike"},
        {"id": "2", "brand": "carhartt wip"},
        {"id": "3", "brand": "Adidas"},
    ]
    result = _filter_blocked_brands(candidates, ["nike", "Carhartt WIP"])
    assert [c["id"] for c in result] == ["3"]
