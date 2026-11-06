from fastapi import APIRouter, HTTPException, Query, status

from vsniper.api.routes.users import UserId
from vsniper.core.state import get_state
from vsniper.domain.contracts import (
    BucketCreate,
    BucketDeleteResult,
    BucketRecord,
    BucketSizeGroup,
    BucketUpdate,
    VintedCatalogNode,
)
from vsniper.integrations.vinted.catalog import CatalogRefreshError
from vsniper.integrations.vinted.client import VintedClientError
from vsniper.services.bucket_service import BucketInvalid, BucketNotFound

router = APIRouter(tags=["buckets"])


@router.get("/users/{user_id}/buckets", response_model=list[BucketRecord])
def list_buckets(user_id: UserId) -> list[BucketRecord]:
    return get_state().buckets.all(user_id)


@router.post("/users/{user_id}/buckets", response_model=BucketRecord, status_code=status.HTTP_201_CREATED)
def create_bucket(user_id: UserId, payload: BucketCreate) -> BucketRecord:
    try:
        return get_state().buckets.create(user_id, payload)
    except BucketInvalid as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.put("/users/{user_id}/buckets/{key}", response_model=BucketRecord)
def update_bucket(user_id: UserId, key: str, payload: BucketUpdate) -> BucketRecord:
    try:
        return get_state().buckets.update(user_id, key, payload)
    except BucketNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bucket not found") from exc
    except BucketInvalid as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.delete("/users/{user_id}/buckets/{key}", response_model=BucketDeleteResult)
def delete_bucket(user_id: UserId, key: str) -> BucketDeleteResult:
    try:
        return get_state().buckets.delete(user_id, key)
    except BucketNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bucket not found") from exc


@router.get("/vinted-sizes", response_model=list[BucketSizeGroup])
def vinted_sizes(catalog_ids: list[int] = Query(default_factory=list)) -> list[BucketSizeGroup]:
    try:
        return get_state().buckets.size_options(catalog_ids)
    except VintedClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/vinted-catalog", response_model=list[VintedCatalogNode])
def vinted_catalog() -> list[VintedCatalogNode]:
    return get_state().buckets.catalog()


@router.post("/vinted-catalog/refresh", response_model=list[VintedCatalogNode])
def refresh_vinted_catalog() -> list[VintedCatalogNode]:
    try:
        return get_state().buckets.refresh_catalog()
    except CatalogRefreshError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
