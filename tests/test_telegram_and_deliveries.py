from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


from vsniper.db.models import AlertDeliveryState, AppSettingsState
from vsniper.domain.contracts import (
    TelegramUpdate,
)
from vsniper.integrations.telegram.service import TelegramFormatter
from vsniper.integrations.telegram.client import TelegramDeliveryError
from vsniper.services.telegram_service import TelegramRuntimeConfig, TelegramService


def test_poll_updates_clears_webhook_once_and_handles_each_update(monkeypatch) -> None:
    service = _build_telegram_service_without_init()
    service._polling_bot_token = None
    calls: list[tuple[str, object]] = []
    batches = [[{'update_id': 7, 'message': {'message_id': 1, 'text': 'hi', 'chat': {'id': 523}}}], []]
    service.telegram_client = SimpleNamespace(
        bot_token='',
        delete_webhook=lambda: calls.append(('delete_webhook', None)),
        get_updates=lambda **kwargs: (calls.append(('get_updates', kwargs['offset'])), batches.pop(0))[1],
    )
    handled: list[int | None] = []
    monkeypatch.setattr(service, 'handle_update', lambda update, **_: handled.append(update.update_id))
    monkeypatch.setattr(
        'vsniper.services.telegram_service._telegram_config',
        lambda: TelegramRuntimeConfig('bot-token', '523'),
    )

    first = service.poll_updates(offset=0, timeout_seconds=1)
    second = service.poll_updates(offset=first, timeout_seconds=1)

    assert (first, second) == (8, 8)
    assert handled == [7]
    assert calls == [('delete_webhook', None), ('get_updates', 0), ('get_updates', 8)]


def _build_telegram_service_without_init() -> TelegramService:
    """Construct a TelegramService bypassing __init__ so tests can inject fakes."""
    return TelegramService.__new__(TelegramService)


def test_refresh_token_expiry_warning_sends_once_and_persists_marker(monkeypatch) -> None:
    expiry = datetime.now(UTC) + timedelta(hours=2)
    settings_row = SimpleNamespace(
        telegram_bot_token='bot-token',
        telegram_chat_id='chat-123',
        session_health={},
        refresh_token_expiry_warning_sent_for=None,
    )
    sent_messages: list[dict[str, str]] = []

    service = _build_telegram_service_without_init()
    service.vinted_client = SimpleNamespace(get_refresh_token_expiry=lambda: expiry)
    service.telegram_client = SimpleNamespace(
        bot_token='',
        send_message=lambda **kwargs: sent_messages.append(kwargs),
    )

    class FakeSession:
        def get(self, model, key):
            if model is AppSettingsState and key == 1:
                return settings_row
            return None

    @contextmanager
    def fake_session_scope():
        yield FakeSession()

    monkeypatch.setattr('vsniper.services.telegram_service.session_scope', fake_session_scope)

    service.check_refresh_token_expiry()
    service.check_refresh_token_expiry()

    assert len(sent_messages) == 1
    assert sent_messages[0]['chat_id'] == 'chat-123'
    assert 'Refresh-Token läuft bald ab' in sent_messages[0]['text']
    assert settings_row.refresh_token_expiry_warning_sent_for == expiry.isoformat()
    assert settings_row.session_health == {}


def test_process_pending_deliveries_retries_retryable_errors(monkeypatch) -> None:
    service = _build_telegram_service_without_init()
    service.settings = SimpleNamespace(telegram_chat_id='chat-123')
    service.telegram_formatter = SimpleNamespace(
        build_alert_message=lambda candidate, kind='alert': 'alert body',
        build_feedback_reply_markup=lambda delivery_id: {'inline_keyboard': [[{'callback_data': f'feedback:{delivery_id}:like'}]]},
    )

    def fail_send_message(*, chat_id: str, text: str, reply_markup: dict | None = None) -> None:
        raise TelegramDeliveryError('rate limited', retryable=True, status_code=429)

    service.telegram_client = SimpleNamespace(send_message=fail_send_message)

    candidate = SimpleNamespace(
        id='cand-1',
        feedback='unknown',
        alert_deliveries=[],
    )
    delivery = AlertDeliveryState(
        id=1,
        candidate_id='cand-1',
        channel='telegram',
        status='pending',
        attempt_count=0,
        last_error=None,
        payload_preview='queued body',
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_attempted_at=None,
        sent_at=None,
    )

    class FakeSession:
        def get(self, model, key):
            if model is AppSettingsState:
                return SimpleNamespace(
                    telegram_bot_token='bot-token',
                    telegram_chat_id='chat-123',
                )
            if model is AlertDeliveryState and key == delivery.id:
                return delivery
            if key == 'cand-1':
                return candidate
            return None

    @contextmanager
    def fake_session_scope():
        yield FakeSession()

    monkeypatch.setattr('vsniper.services.telegram_service.session_scope', fake_session_scope)
    monkeypatch.setattr('vsniper.services.telegram_service.candidate_to_contract', lambda candidate: SimpleNamespace(id='cand-1'))
    monkeypatch.setattr(TelegramService, '_count_pending_deliveries', lambda self, session: 1)

    def fake_claim(self, session, *, limit: int):
        delivery.status = 'processing'
        delivery.attempt_count += 1
        delivery.last_attempted_at = datetime.now(UTC)
        return [delivery]

    monkeypatch.setattr(TelegramService, '_claim_pending_deliveries', fake_claim)

    result = service.process_pending_deliveries(limit=10)

    assert result.eligible_deliveries == 1
    assert result.processed_deliveries == 1
    assert result.retry_scheduled_deliveries == 1
    assert result.failed_deliveries == 0
    assert delivery.status == 'pending'
    assert delivery.attempt_count == 1
    assert delivery.last_error == 'rate limited'


def test_handle_telegram_webhook_records_feedback_from_delivery_callback(monkeypatch) -> None:
    service = _build_telegram_service_without_init()
    acknowledgements: list[dict[str, str]] = []
    edits: list[dict[str, object]] = []
    service.telegram_client = SimpleNamespace(
        answer_callback_query=lambda **kwargs: acknowledgements.append(kwargs),
        edit_message_text=lambda **kwargs: edits.append(kwargs),
    )
    service.telegram_formatter = TelegramFormatter()

    candidate = SimpleNamespace(id='cand-3', feedback='unknown')
    delivery = AlertDeliveryState(
        id=3,
        candidate_id='cand-3',
        channel='telegram',
        status='sent',
        attempt_count=1,
        last_error=None,
        payload_preview='queued body',
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_attempted_at=datetime.now(UTC),
        sent_at=datetime.now(UTC),
    )

    class FakeSession:
        def get(self, model, key):
            if key == 3:
                return delivery
            if key == 'cand-3':
                return candidate
            return None

        def flush(self):
            return None

        def refresh(self, model):
            return None

    @contextmanager
    def fake_session_scope():
        yield FakeSession()

    def fake_apply_feedback(candidate_id, *, verdict: str, comment: str = '', skip_if_unchanged: bool = False):
        candidate.feedback = verdict
        return SimpleNamespace(id='cand-3'), SimpleNamespace(id='snapshot-1')

    service.candidates = SimpleNamespace(apply_feedback=fake_apply_feedback)

    monkeypatch.setattr('vsniper.services.telegram_service.session_scope', fake_session_scope)
    monkeypatch.setattr(
        'vsniper.services.telegram_service._telegram_config',
        lambda: TelegramRuntimeConfig('bot-token', '523'),
    )

    result = service.handle_update(
        TelegramUpdate.model_validate(
            {
                'update_id': 1001,
                'callback_query': {
                    'id': 'callback-telegram-1',
                    'data': 'feedback:3:like',
                    'message': {
                        'message_id': 77,
                        'text': 'queued body',
                        'chat': {'id': 523},
                    },
                },
            }
        )
    )

    assert result.action == 'feedback_recorded'
    assert result.candidate_id == 'cand-3'
    assert result.delivery_id == '3'
    assert result.verdict == 'like'
    assert result.learning_snapshot_id == 'snapshot-1'
    assert edits == [
        {
            'chat_id': 523,
            'message_id': 77,
            'text': 'queued body\n\nDeine Bewertung: 👍 Gefällt mir\n↩ Antworte auf diese Nachricht, um eine Notiz dazu zu hinterlassen.',
            'reply_markup': {'inline_keyboard': []},
        },
    ]
    assert acknowledgements == [
        {'callback_query_id': 'callback-telegram-1', 'text': 'Gespeichert: Gefällt mir'},
    ]


def test_handle_telegram_webhook_rejects_feedback_from_unauthorized_chat(monkeypatch) -> None:
    service = _build_telegram_service_without_init()
    acknowledgements: list[dict[str, str]] = []
    edits: list[dict[str, object]] = []
    service.telegram_client = SimpleNamespace(
        answer_callback_query=lambda **kwargs: acknowledgements.append(kwargs),
        edit_message_text=lambda **kwargs: edits.append(kwargs),
    )
    service.telegram_formatter = TelegramFormatter()

    record_calls: list[dict[str, object]] = []

    def fake_record_feedback_in_session(session, **kwargs):
        record_calls.append(kwargs)
        return SimpleNamespace(id='cand-3'), SimpleNamespace(id='snapshot-1')

    service.candidates = SimpleNamespace(record_feedback_in_session=fake_record_feedback_in_session)

    @contextmanager
    def fake_session_scope():
        raise AssertionError('session_scope must not be opened for an unauthorized chat')
        yield

    monkeypatch.setattr('vsniper.services.telegram_service.session_scope', fake_session_scope)
    monkeypatch.setattr(
        'vsniper.services.telegram_service._telegram_config',
        lambda: TelegramRuntimeConfig('bot-token', '523'),
    )

    result = service.handle_update(
        TelegramUpdate.model_validate(
            {
                'update_id': 1002,
                'callback_query': {
                    'id': 'callback-forged-1',
                    'data': 'feedback:3:like',
                    'message': {
                        'message_id': 77,
                        'text': 'queued body',
                        'chat': {'id': 999},
                    },
                },
            }
        )
    )

    assert result.action == 'unauthorized'
    assert record_calls == []
    assert edits == []
    assert acknowledgements == [
        {
            'callback_query_id': 'callback-forged-1',
            'text': 'Dieser Chat ist nicht freigeschaltet.',
        },
    ]


def test_taste_recompute_callback_detects_already_running(monkeypatch) -> None:
    service = _build_telegram_service_without_init()
    edits: list[dict] = []
    acknowledgements: list[dict] = []
    running_since = datetime.now(UTC) - timedelta(minutes=5)
    service.telegram_client = SimpleNamespace(
        bot_token='',
        edit_message_text=lambda **kwargs: edits.append(kwargs),
        send_message=lambda **kwargs: None,
        answer_callback_query=lambda **kwargs: acknowledgements.append(kwargs),
    )
    service.telegram_formatter = TelegramFormatter()
    service.taste = SimpleNamespace(
        claim_recompute=lambda source: SimpleNamespace(
            claimed=False,
            job_id=None,
            started_at=None,
            running_job_id='taste-job-running',
            running_started_at=running_since,
        )
    )

    monkeypatch.setattr(
        'vsniper.services.telegram_service._telegram_config',
        lambda: TelegramRuntimeConfig('bot-token', '523'),
    )

    result = service.handle_update(
        TelegramUpdate.model_validate(
            {
                'update_id': 1,
                'callback_query': {
                    'id': 'cb-taste-2',
                    'data': 'taste:recompute',
                    'message': {'message_id': 77, 'text': 'taste status', 'chat': {'id': 523}},
                },
            }
        ),
        schedule_background=lambda task: (_ for _ in ()).throw(AssertionError('should not schedule')),
    )

    assert result.action == 'taste_recompute_already_running'
    assert result.recompute_job_id == 'taste-job-running'
    assert 'läuft schon' in edits[0]['text']
    assert acknowledgements == [{'callback_query_id': 'cb-taste-2', 'text': 'Die Berechnung läuft schon.'}]


def test_borderline_probe_sends_the_best_recent_near_miss_once_a_day(monkeypatch, tmp_path) -> None:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from vsniper.core.database import Base
    from vsniper.db.models import Candidate

    engine = create_engine(f"sqlite:///{tmp_path / 'borderline.db'}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def fake_session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr('vsniper.services.telegram_service.session_scope', fake_session_scope)
    now = datetime.now(UTC)

    def candidate(candidate_id: str, score: int, *, feedback: str = 'unknown', age: timedelta = timedelta(hours=1)):
        return Candidate(
            id=candidate_id, search_id='s1', clothing_item='hosen', title=candidate_id, brand='B', price_eur=20.0,
            size='M', url=f'https://example.test/{candidate_id}', image_urls=[], matched_filters=[],
            matched_preferences=[], features=[], normalized_listing={}, extraction_status='completed',
            score_trace={'final_score': score / 100, 'score_10': score, 'threshold': 0.95, 'decision': 'review',
                         'components': [], 'summary': '', 'raw_response': {}},
            decision='review', final_score=score / 100, ai_observation={}, grading_stage='vlm_judged',
            feedback=feedback, feedback_comment='', created_at=now - age,
        )

    with factory() as session:
        session.add(AppSettingsState(id=1, vinted_region='de', telegram_bot_token='bot-token', telegram_chat_id='523'))
        session.add_all([
            candidate('near-85', 85),
            candidate('near-88', 88),
            candidate('liked-90', 90, feedback='like'),
            candidate('too-low', 70),
            candidate('stale-92', 92, age=timedelta(days=2)),
        ])
        session.commit()

    service = _build_telegram_service_without_init()
    service.telegram_formatter = TelegramFormatter()

    assert service.queue_borderline_probe() is True
    assert service.queue_borderline_probe() is False  # one per day

    with factory() as session:
        deliveries = session.scalars(select(AlertDeliveryState)).all()
        assert [(d.candidate_id, d.kind) for d in deliveries] == [('near-88', 'borderline')]
        assert deliveries[0].payload_preview.startswith('🤔 Knapp daneben: 88/100')
