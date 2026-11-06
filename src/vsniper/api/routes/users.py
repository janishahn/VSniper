from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from vsniper.core.state import get_state
from vsniper.domain.contracts import BlockedBrandsSnapshot, BlockedBrandsUpdate, UserRecord, UserUpdate
from vsniper.services.user_service import UserNotFound

router = APIRouter(tags=["users"])


def existing_user(user_id: int) -> int:
    try:
        get_state().users.get(user_id)
    except UserNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found") from exc
    return user_id


UserId = Annotated[int, Depends(existing_user)]


@router.get("/users", response_model=list[UserRecord])
def list_users() -> list[UserRecord]:
    return get_state().users.all()


@router.get("/users/{user_id}", response_model=UserRecord)
def get_user(user_id: UserId) -> UserRecord:
    return get_state().users.get(user_id)


@router.put("/users/{user_id}", response_model=UserRecord)
def update_user(user_id: UserId, payload: UserUpdate) -> UserRecord:
    return get_state().users.update(user_id, payload)


@router.put("/users/{user_id}/blocked-brands", response_model=BlockedBrandsSnapshot)
def update_blocked_brands(user_id: UserId, payload: BlockedBrandsUpdate) -> BlockedBrandsSnapshot:
    return BlockedBrandsSnapshot(brands=get_state().users.update_blocked_brands(user_id, payload.brands))
