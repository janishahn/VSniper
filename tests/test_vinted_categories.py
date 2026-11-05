from __future__ import annotations


import pytest

from vsniper.domain.contracts import SearchFilter
from vsniper.integrations.vinted.categories import (
    ALLOWED_CATEGORY_ALIASES_BY_ITEM,
    CategoryFilterError,
    category_ids_for_alias,
    ensure_category_filter_for_clothing_item,
    resolved_category_ids_for_aliases,
)


def test_every_allowed_bucket_alias_resolves() -> None:
    for clothing_item, aliases in ALLOWED_CATEGORY_ALIASES_BY_ITEM.items():
        unresolved = [alias for alias in aliases if not category_ids_for_alias(alias)]
        assert unresolved == [], f"{clothing_item} has unresolved aliases: {unresolved}"
        assert resolved_category_ids_for_aliases(aliases)


def test_mismatched_category_filter_is_rejected_in_strict_mode() -> None:
    with pytest.raises(CategoryFilterError, match="outside Hosen"):
        ensure_category_filter_for_clothing_item(
            [SearchFilter(field="category", label="Category", values=["schuhe"], mode="include")],
            "hosen",
            strict=True,
        )
