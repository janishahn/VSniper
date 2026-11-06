from fastapi import APIRouter, HTTPException, status

from vsniper.api.routes.users import UserId
from vsniper.core.state import get_state
from vsniper.domain.contracts import (
    SearchRecord,
    SearchRunResult,
    SearchUpdate,
)
from vsniper.integrations.vinted.categories import CategoryFilterError
from vsniper.integrations.vinted.client import VintedClientError
from vsniper.services.maintenance_service import get_maintenance_state
from vsniper.services.search_service import SearchClothingItemImmutable, SearchNotConfigured, SearchRunAlreadyClaimed

router = APIRouter(tags=["searches"])


def _reject_if_maintenance() -> None:
    mode = get_maintenance_state().get("mode", "idle")
    if mode != "idle":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"System is in maintenance mode ({mode}). Wait for maintenance to finish before running scans.",
        )


@router.get("/users/{user_id}/searches", response_model=list[SearchRecord])
def list_searches(user_id: UserId) -> list[SearchRecord]:
    return get_state().searches.all(user_id)


@router.put("/users/{user_id}/searches/{search_id}", response_model=SearchRecord)
def update_search(user_id: UserId, search_id: str, payload: SearchUpdate) -> SearchRecord:
    try:
        return get_state().searches.update(user_id, search_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search not found") from exc
    except SearchClothingItemImmutable as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except CategoryFilterError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/users/{user_id}/searches/{search_id}/toggle", response_model=SearchRecord)
def toggle_search(user_id: UserId, search_id: str) -> SearchRecord:
    try:
        return get_state().searches.toggle(user_id, search_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search not found") from exc


@router.post("/users/{user_id}/searches/run-all", response_model=list[SearchRunResult])
def run_all_searches(user_id: UserId) -> list[SearchRunResult]:
    _reject_if_maintenance()
    return get_state().searches.run_all_enabled(user_id)


@router.post("/users/{user_id}/searches/{search_id}/run", response_model=SearchRunResult)
def run_search(user_id: UserId, search_id: str) -> SearchRunResult:
    try:
        return get_state().searches.run_live(user_id, search_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search not found") from exc
    except SearchRunAlreadyClaimed as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Search is already running. Wait for the current run to finish before starting another live run.",
        ) from exc
    except SearchNotConfigured as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except VintedClientError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/users/{user_id}/searches/{search_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
def cancel_search(user_id: UserId, search_id: str) -> None:
    try:
        cancelled = get_state().searches.request_cancel(user_id, search_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Search not found") from exc
    if not cancelled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Search is not currently running.")
