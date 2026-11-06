from __future__ import annotations

from datetime import UTC, datetime

from vsniper.domain.contracts import BucketRecord
from vsniper.integrations.vinted.categories import default_category_filter


def canonical_search_id(user_id: int, clothing_item: str) -> str:
    return f"search-{user_id}-{clothing_item}"


def canonical_search_values(user_id: int, bucket: BucketRecord, *, now: datetime | None = None) -> dict:
    created_at = now or datetime.now(UTC)
    return {
        "id": canonical_search_id(user_id, bucket.key),
        "user_id": user_id,
        "name": bucket.label,
        "clothing_item": bucket.key,
        "query": "",
        "region": "de",
        "filters": [default_category_filter(bucket).model_dump(mode="json")],
        "alert_threshold": None,
        "enabled": False,
        "last_claimed_at": None,
        "last_run_at": None,
        "run_status": "idle",
        "last_found_count": 0,
        "created_at": created_at,
    }
