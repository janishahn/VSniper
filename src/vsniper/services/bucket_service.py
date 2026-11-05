from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from vsniper.core.config import Settings, get_settings
from vsniper.core.database import session_scope
from vsniper.db.models import Bucket, Candidate, Search, TasteSampleState
from vsniper.domain.contracts import (
    BucketCategory,
    BucketCreate,
    BucketDeleteResult,
    BucketRecord,
    BucketUpdate,
    BucketWrite,
    SearchFilter,
    VintedCatalogNode,
)
from vsniper.integrations.vinted.catalog import catalog_by_id, load_catalog, refresh_catalog
from vsniper.integrations.vinted.categories import ensure_category_filter
from vsniper.services.search_defaults import canonical_search_values

_logger = logging.getLogger(__name__)
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class BucketNotFound(KeyError):
    pass


class BucketInvalid(ValueError):
    pass


def _catalog() -> dict[int, VintedCatalogNode]:
    settings = get_settings()
    return catalog_by_id(settings.resolve_path(settings.cache_dir))


def bucket_to_record(model: Bucket, catalog: dict[int, VintedCatalogNode]) -> BucketRecord:
    return BucketRecord(
        key=model.key,
        label=model.label,
        description=model.description,
        categories=[
            BucketCategory(id=catalog_id, path=catalog[catalog_id].path if catalog_id in catalog else "")
            for catalog_id in model.catalog_ids
        ],
        size_keywords=list(model.size_keywords),
        position=model.position,
        archived=model.archived,
    )


def bucket_records(session: Session, *, include_archived: bool = False) -> list[BucketRecord]:
    query = select(Bucket).order_by(Bucket.position, Bucket.created_at)
    if not include_archived:
        query = query.where(Bucket.archived.is_(False))
    catalog = _catalog()
    return [bucket_to_record(model, catalog) for model in session.scalars(query)]


def require_bucket(session: Session, key: str) -> BucketRecord:
    model = session.get(Bucket, key)
    if model is None:
        raise BucketNotFound(key)
    return bucket_to_record(model, _catalog())


def require_active_bucket(session: Session, key: str) -> BucketRecord:
    model = session.get(Bucket, key)
    if model is None:
        raise BucketInvalid(f"Unknown bucket {key}.")
    bucket = bucket_to_record(model, _catalog())
    if bucket.archived:
        raise BucketInvalid(f"The bucket {bucket.label} is archived.")
    return bucket


class BucketService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _cache_dir(self):
        return self.settings.resolve_path(self.settings.cache_dir)

    def all(self) -> list[BucketRecord]:
        with session_scope() as session:
            return bucket_records(session, include_archived=True)

    def catalog(self) -> list[VintedCatalogNode]:
        return load_catalog(self._cache_dir())

    def refresh_catalog(self) -> list[VintedCatalogNode]:
        with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": _BROWSER_USER_AGENT}) as client:
            nodes = refresh_catalog(self._cache_dir(), client)
        _logger.info("Vinted women's catalog refreshed nodes=%d", len(nodes))
        return nodes

    def _validated_fields(self, payload: BucketWrite) -> dict:
        catalog = catalog_by_id(self._cache_dir())
        catalog_ids = list(dict.fromkeys(payload.catalog_ids))
        unknown = [str(catalog_id) for catalog_id in catalog_ids if catalog_id not in catalog]
        if unknown:
            raise BucketInvalid(f"Unknown Vinted categories: {', '.join(unknown)}.")
        return {
            "label": payload.label.strip(),
            "description": payload.description.strip(),
            "catalog_ids": catalog_ids,
            "size_keywords": [keyword.strip() for keyword in payload.size_keywords if keyword.strip()],
        }

    def create(self, payload: BucketCreate) -> BucketRecord:
        fields = self._validated_fields(payload)
        now = datetime.now(UTC)
        with session_scope() as session:
            if session.get(Bucket, payload.key) is not None:
                raise BucketInvalid(f"A bucket with the key {payload.key} already exists.")
            position = (session.scalar(select(func.max(Bucket.position))) or 0) + 1
            model = Bucket(key=payload.key, position=position, archived=False, created_at=now, updated_at=now, **fields)
            session.add(model)
            session.flush()
            record = bucket_to_record(model, _catalog())
            session.add(Search(**canonical_search_values(record, now=now)))
            return record

    def update(self, key: str, payload: BucketUpdate) -> BucketRecord:
        fields = self._validated_fields(payload)
        with session_scope() as session:
            model = session.get(Bucket, key)
            if model is None:
                raise BucketNotFound(key)
            taste_fields_changed = any(getattr(model, name) != fields[name] for name in ("label", "description", "catalog_ids"))
            for name, value in fields.items():
                setattr(model, name, value)
            if taste_fields_changed:
                model.updated_at = datetime.now(UTC)
            model.archived = payload.archived
            session.flush()
            record = bucket_to_record(model, _catalog())

            search = session.scalar(select(Search).where(Search.clothing_item == key))
            if search is not None:
                search.name = record.label
                if record.archived:
                    search.enabled = False
                filters = [SearchFilter.model_validate(item) for item in search.filters or []]
                search.filters = [item.model_dump(mode="json") for item in ensure_category_filter(filters, record, strict=False)]
            return record

    def delete(self, key: str) -> BucketDeleteResult:
        """Delete a bucket without history; archive one that has samples or candidates."""
        with session_scope() as session:
            model = session.get(Bucket, key)
            if model is None:
                raise BucketNotFound(key)
            has_history = session.scalar(
                select(
                    exists().where(TasteSampleState.clothing_item == key)
                    | exists().where(Candidate.clothing_item == key)
                )
            )
            search = session.scalar(select(Search).where(Search.clothing_item == key))
            if has_history:
                model.archived = True
                if search is not None:
                    search.enabled = False
                return BucketDeleteResult(key=key, outcome="archived")
            if search is not None:
                session.delete(search)
            session.delete(model)
            return BucketDeleteResult(key=key, outcome="deleted")
