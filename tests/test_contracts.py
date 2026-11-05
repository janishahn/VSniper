from __future__ import annotations


import pytest
from pydantic import ValidationError

from vsniper.domain.contracts import SearchUpdate


def test_search_update_only_accepts_de_region() -> None:
    payload = {
        "name": "Search",
        "clothing_item": "hosen",
        "query": "vintage",
        "region": "fr",
        "filters": [],
    }

    with pytest.raises(ValidationError, match="Only the de Vinted region is supported"):
        SearchUpdate.model_validate(payload)
