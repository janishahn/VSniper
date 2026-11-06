from __future__ import annotations

from vsniper.domain.contracts import BucketRecord, SearchFilter


_CATEGORY_FIELDS = {"category", "catalog"}


class CategoryFilterError(ValueError):
    """Raised when a saved search contains an unusable Vinted category filter."""


def normalise_lookup_text(value: str) -> str:
    return (
        str(value)
        .strip()
        .lower()
        .replace("&", "and")
        .replace("_", " ")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )


def default_category_filter(bucket: BucketRecord) -> SearchFilter:
    return SearchFilter(
        field="category",
        label="Vinted category",
        mode="include",
        values=[str(category.id) for category in bucket.categories],
    )


def _is_category_field(field: str) -> bool:
    return normalise_lookup_text(field) in _CATEGORY_FIELDS


def ensure_category_filter(filters: list[SearchFilter], bucket: BucketRecord, *, strict: bool) -> list[SearchFilter]:
    """Return filters with one category filter whose values are catalog ids of the bucket.

    Values outside the bucket are dropped, or raise in strict mode. With no valid value left,
    the filter falls back to all of the bucket's categories.
    """
    allowed = {str(category.id) for category in bucket.categories}
    valid_values: list[str] = []
    result: list[SearchFilter] = []
    for filter_item in filters:
        if not _is_category_field(filter_item.field):
            result.append(filter_item)
            continue
        values = [value.strip() for value in filter_item.values if value.strip()]
        outside = [value for value in values if value not in allowed]
        if strict and outside:
            raise CategoryFilterError(
                f"Categories {', '.join(outside)} are not part of the bucket {bucket.label}. "
                "Add them to the bucket first."
            )
        valid_values.extend(value for value in values if value in allowed)

    if not valid_values:
        return [default_category_filter(bucket), *result]
    category = SearchFilter(field="category", label="Vinted category", mode="include", values=list(dict.fromkeys(valid_values)))
    return [category, *result]
