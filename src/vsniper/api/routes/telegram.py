from fastapi import APIRouter, HTTPException, status

from vsniper.api.routes.users import UserId
from vsniper.core.state import get_state

router = APIRouter(tags=["telegram"])


@router.post("/users/{user_id}/telegram/test")
def telegram_test_message(user_id: UserId) -> dict[str, str]:
    return {"preview": get_state().build_test_telegram_message(user_id)}


@router.post("/users/{user_id}/telegram/test/send")
def send_telegram_test_message(user_id: UserId) -> dict[str, object]:
    try:
        return get_state().telegram.send_test_notification(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
