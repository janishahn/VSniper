from fastapi import APIRouter, HTTPException, status

from vsniper.core.state import get_state

router = APIRouter(tags=["telegram"])


@router.post("/telegram/test")
def telegram_test_message() -> dict[str, str]:
    return {"preview": get_state().build_test_telegram_message()}


@router.post("/telegram/test/send")
def send_telegram_test_message() -> dict[str, object]:
    try:
        return get_state().telegram.send_test_notification()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
