from fastapi import APIRouter, HTTPException

from vsniper.core.state import get_state
from vsniper.domain.contracts import (
    SettingsSnapshot,
    SettingsUpdate,
    VintedBrandOption,
)
from vsniper.integrations.vinted.client import VintedClientError

router = APIRouter()


@router.get("/settings", response_model=SettingsSnapshot)
def get_settings() -> SettingsSnapshot:
    return get_state().searches.get_app_settings()


@router.put("/settings", response_model=SettingsSnapshot)
def update_settings(payload: SettingsUpdate) -> SettingsSnapshot:
    return get_state().searches.update_app_settings(payload)


@router.get("/vinted/brands", response_model=list[VintedBrandOption])
def search_vinted_brands(query: str = "") -> list[VintedBrandOption]:
    try:
        options = get_state().vinted_client.search_brands(query)
    except VintedClientError as exc:
        status_code = 503 if exc.retryable else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return [VintedBrandOption.model_validate(option) for option in options]

