from __future__ import annotations

import json
from pathlib import Path

import pytest

from vsniper.domain.contracts import BucketCategory, BucketRecord, SearchFilter
from vsniper.integrations.vinted.catalog import parse_catalog_page
from vsniper.integrations.vinted.categories import CategoryFilterError, ensure_category_filter

FIXTURE = Path(__file__).parent / "fixtures/vinted/de/catalog_tree.txt"
SNAPSHOT = Path(__file__).parents[1] / "src/vsniper/integrations/vinted/catalog.json"


def _bucket(key: str, catalog_ids: list[int]) -> BucketRecord:
    return BucketRecord(
        key=key,
        label=key.title(),
        description="",
        categories=[BucketCategory(id=catalog_id, path="") for catalog_id in catalog_ids],
        sizes=[],
        position=0,
        archived=False,
    )


def test_catalog_page_parses_to_the_women_and_men_subtrees_with_paths() -> None:
    nodes = parse_catalog_page(FIXTURE.read_text())
    by_id = {node.id: node for node in nodes}

    assert nodes[0].id == 1904 and nodes[0].parent_id is None
    assert by_id[5].parent_id is None
    assert by_id[257].path == "Herren > Kleidung > Jeans"
    assert 1193 not in by_id  # other roots such as Kinder are left out
    assert by_id[553].path == "Damen > Accessoires > Schmuck > Ringe"
    assert by_id[553].parent_id == 21
    assert [node.model_dump() for node in nodes] == json.loads(SNAPSHOT.read_text())


def test_category_filter_keeps_bucket_ids_and_rejects_others_in_strict_mode() -> None:
    shoes = _bucket("schuhe", [16, 2632])
    narrowed = SearchFilter(field="category", label="Category", values=["2632"], mode="include")

    assert ensure_category_filter([narrowed], shoes, strict=True)[0].values == ["2632"]
    with pytest.raises(CategoryFilterError):
        ensure_category_filter([narrowed.model_copy(update={"values": ["553"]})], shoes, strict=True)
    # Outside ids are dropped; with nothing left the filter covers the whole bucket.
    fallback = ensure_category_filter([narrowed.model_copy(update={"values": ["553"]})], shoes, strict=False)
    assert fallback[0].values == ["16", "2632"]
