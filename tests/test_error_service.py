from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from vsniper.core.database import Base
from vsniper.db.models import AppSettingsState, ErrorEvent
from vsniper.integrations.telegram.client import TelegramDeliveryError
from vsniper.services.error_service import ErrorService


def _setup(monkeypatch, tmp_path, *, enabled: bool = False):
    engine = create_engine(f"sqlite:///{tmp_path / 'errors.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def fake_session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr("vsniper.services.error_service.session_scope", fake_session_scope)
    with fake_session_scope() as session:
        session.add(
            AppSettingsState(
                id=1,
                vinted_region="de",
                vinted_cookie="session-cookie-secret",
                telegram_bot_token="telegram-bot-secret",
                telegram_chat_id="chat-123",
                telegram_configured=True,
                error_telegram_notifications_enabled=enabled,
            )
        )

    runtime = SimpleNamespace(
        vinted_cookie="put-your-vinted-cookie-here",
        telegram_bot_token="put-your-telegram-bot-token-here",
        ai_api_key="put-your-ai-key-here",
        cerebras_api_key="put-your-cerebras-api-key-here",
        openrouter_api_key="put-your-openrouter-api-key-here",
        error_retention_days=365,
    )
    telegram = SimpleNamespace(bot_token="", send_message=lambda **_kwargs: {"message_id": 1})
    return ErrorService(runtime, telegram), factory


def test_record_lists_and_redacts_terminal_error(monkeypatch, tmp_path) -> None:
    service, _ = _setup(monkeypatch, tmp_path)
    exc = RuntimeError("request used telegram-bot-secret")
    exc.attempt_count = 3  # type: ignore[attr-defined]
    exc.retry_label = "Telegram request"  # type: ignore[attr-defined]

    event_id = service.record(
        source="telegram",
        operation="send_message",
        summary="Telegram failed",
        exception=exc,
        details={
            "authorization": "Bearer telegram-bot-secret",
            "nested": {"cookie": "session-cookie-secret"},
        },
        related_entity_type="delivery",
        related_entity_id=42,
    )

    page = service.page(source="telegram")
    assert event_id is not None
    assert page.total == 1
    event = page.items[0]
    assert event.telegram_notification_status == "not_requested"
    assert event.details["authorization"] == "<redacted>"
    assert event.details["nested"] == {"cookie": "<redacted>"}
    assert event.details["retry_attempt_count"] == 3
    assert "telegram-bot-secret" not in event.message
    assert event.related_entity_id == "42"


def test_retryable_notification_stops_after_three_attempts(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path, enabled=True)

    def fail_send(**_kwargs):
        raise TelegramDeliveryError("temporary outage", retryable=True, status_code=503)

    service.telegram_client.send_message = fail_send
    event_id = service.record(
        source="worker",
        operation="maintenance",
        summary="Maintenance failed",
        message="Temporary failure.",
    )

    outcomes = []
    for _ in range(3):
        outcomes.append(service.process_pending_notifications())
        with factory() as session:
            event = session.get(ErrorEvent, event_id)
            assert event is not None
            event.telegram_notification_last_attempted_at = datetime.now(UTC) - timedelta(minutes=10)
            session.commit()

    assert [item["retry"] for item in outcomes] == [1, 1, 0]
    assert outcomes[-1]["failed"] == 1
    with factory() as session:
        event = session.get(ErrorEvent, event_id)
        assert event is not None
        assert event.telegram_notification_status == "failed"
        assert event.telegram_notification_attempt_count == 3
        assert session.scalar(select(func.count()).select_from(ErrorEvent)) == 1
