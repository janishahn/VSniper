from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from vsniper.api.routes.users import UserId
from vsniper.core.state import get_state
from vsniper.domain.contracts import CandidatePage, CandidateRecord, FeedbackPayload

router = APIRouter(tags=["candidates"])


@router.get("/users/{user_id}/candidates", response_model=CandidatePage)
def list_candidates(
    user_id: UserId,
    clothing_item: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    decision: str | None = Query(default=None),
    feedback: str | None = Query(default=None),
    delivery_status: str | None = Query(default=None),
    window: Literal["1h", "6h", "12h", "1d", "7d", "30d"] = Query(default="7d"),
    sort: str = Query(default="score_desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> CandidatePage:
    return get_state().candidates.page(
        user_id,
        clothing_item=clothing_item,
        stage=stage,
        decision=decision,
        feedback=feedback,
        delivery_status=delivery_status,
        window=window,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.post("/users/{user_id}/candidates/{candidate_id}/feedback", response_model=CandidateRecord)
def record_feedback(user_id: UserId, candidate_id: str, payload: FeedbackPayload) -> CandidateRecord:
    try:
        return get_state().candidates.record_feedback(user_id, candidate_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/users/{user_id}/candidates/{candidate_id}/retry-delivery", status_code=status.HTTP_204_NO_CONTENT)
def retry_delivery(user_id: UserId, candidate_id: str) -> None:
    if not get_state().telegram.retry_delivery(user_id, candidate_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No failed Telegram delivery to retry for this candidate.",
        )
