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
        lambda: TelegramRuntimeConfig('bot-token'),
    )

    first = service.poll_updates(offset=0, timeout_seconds=1)
    second = service.poll_updates(offset=first, timeout_seconds=1)

    assert (first, second) == (8, 8)
    assert handled == [7]
    assert calls == [('delete_webhook', None), ('get_updates', 0), ('get_updates', 8)]


def _build_telegram_service_without_init() -> TelegramService:
    """Construct a TelegramService bypassing __init__ so tests can inject fakes. Chat 523 is
    allowed and belongs to profile 1."""
    service = TelegramService.__new__(TelegramService)
    service.users = SimpleNamespace(
        user_for_chat=lambda chat_id: 1 if str(chat_id) == '523' else None,
        chat_for_user=lambda session, user_id: '523' if user_id == 1 else None,
        authorized_user_ids=lambda: [1],
    )
    return service


def test_process_pending_deliveries_retries_retryable_errors(monkeypatch) -> None:
    service = _build_telegram_service_without_init()
    service.telegram_formatter = SimpleNamespace(
        build_alert_message=lambda candidate, kind='alert': 'alert body',
        build_feedback_reply_markup=lambda delivery_id: {'inline_keyboard': [[{'callback_data': f'feedback:{delivery_id}:like'}]]},
    )

    def fail_send_message(*, chat_id: str, text: str, reply_markup: dict | None = None) -> None:
        raise TelegramDeliveryError('rate limited', retryable=True, status_code=429)

    service.telegram_client = SimpleNamespace(send_message=fail_send_message)

    candidate = SimpleNamespace(
        id='cand-1',
        user_id=1,
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
                return SimpleNamespace(telegram_bot_token='bot-token')
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
    monkeypatch.setattr(TelegramService, '_count_pending_deliveries', lambda self, session, user_ids: 1)

    def fake_claim(self, session, user_ids, *, limit: int):
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

    def fake_apply_feedback(user_id, candidate_id, *, verdict: str, comment: str = '', skip_if_unchanged: bool = False):
        # The press is answered before the slow recording starts.
        assert acknowledgements == [{'callback_query_id': 'callback-telegram-1', 'text': 'Wird gespeichert …'}]
        candidate.feedback = verdict
        return SimpleNamespace(id='cand-3'), SimpleNamespace(id='snapshot-1')

    service.candidates = SimpleNamespace(apply_feedback=fake_apply_feedback)

    monkeypatch.setattr('vsniper.services.telegram_service.session_scope', fake_session_scope)
    monkeypatch.setattr(
        'vsniper.services.telegram_service._telegram_config',
        lambda: TelegramRuntimeConfig('bot-token'),
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
        {'callback_query_id': 'callback-telegram-1', 'text': 'Wird gespeichert …'},
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
        lambda: TelegramRuntimeConfig('bot-token'),
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
        claim_recompute=lambda user_id, source: SimpleNamespace(
            claimed=False,
            job_id=None,
            started_at=None,
            running_job_id='taste-job-running',
            running_started_at=running_since,
        )
    )

    monkeypatch.setattr(
        'vsniper.services.telegram_service._telegram_config',
        lambda: TelegramRuntimeConfig('bot-token'),
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
    from vsniper.db.models import Candidate, User
    from vsniper.services.user_service import UserService

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
            id=candidate_id, user_id=1, search_id='s1', clothing_item='hosen', title=candidate_id, brand='B', price_eur=20.0,
            size='M', url=f'https://example.test/{candidate_id}', image_urls=[], matched_filters=[],
            matched_preferences=[], features=[], normalized_listing={}, extraction_status='completed',
            score_trace={'final_score': score / 100, 'score_10': score, 'threshold': 0.95, 'decision': 'review',
                         'components': [], 'summary': '', 'raw_response': {}},
            decision='review', final_score=score / 100, ai_observation={}, grading_stage='vlm_judged',
            feedback=feedback, feedback_comment='', created_at=now - age,
        )

    with factory() as session:
        session.add(AppSettingsState(id=1, telegram_bot_token='bot-token'))
        session.add(User(id=1, name='Profile 1', telegram_chat_id='523'))
        session.add_all([
            candidate('near-85', 85),
            candidate('near-88', 88),
            candidate('liked-90', 90, feedback='like'),
            candidate('too-low', 70),
            candidate('stale-92', 92, age=timedelta(days=2)),
        ])
        session.commit()

    service = _build_telegram_service_without_init()
    service.users = UserService(SimpleNamespace(allowed_chat_ids=['523']))
    service.telegram_formatter = TelegramFormatter()

    assert service.queue_borderline_probe(1) is True
    assert service.queue_borderline_probe(1) is False  # one per day

    with factory() as session:
        deliveries = session.scalars(select(AlertDeliveryState)).all()
        assert [(d.candidate_id, d.kind) for d in deliveries] == [('near-88', 'borderline')]
        assert deliveries[0].payload_preview.startswith('🤔 Knapp daneben: 88/100')


def test_reply_comment_is_saved_off_the_polling_thread_and_confirmed(monkeypatch) -> None:
    service = _build_telegram_service_without_init()
    scheduled: list = []
    sent: list[dict] = []
    saved: list = []
    service.telegram_client = SimpleNamespace(send_message=lambda **kwargs: sent.append(kwargs))
    monkeypatch.setattr(
        'vsniper.services.telegram_service._telegram_config',
        lambda: TelegramRuntimeConfig('bot-token'),
    )
    delivery = SimpleNamespace(candidate_id='cand-3')
    candidate = SimpleNamespace(id='cand-3', user_id=1, feedback='like')

    class FakeSession:
        def scalar(self, query):
            return delivery

        def get(self, model, key):
            return candidate

    @contextmanager
    def fake_session_scope():
        yield FakeSession()

    def fake_apply_feedback(user_id, candidate_id, **kwargs):
        saved.append((user_id, candidate_id, kwargs))
        return candidate, SimpleNamespace(id='snapshot')

    monkeypatch.setattr('vsniper.services.telegram_service.session_scope', fake_session_scope)
    service.candidates = SimpleNamespace(apply_feedback=fake_apply_feedback)

    result = service.handle_update(
        TelegramUpdate.model_validate({
            'update_id': 22,
            'message': {'message_id': 78, 'chat': {'id': 523}, 'text': 'Toller Schnitt', 'reply_to_message': {'message_id': 77}},
        }),
        schedule_background=scheduled.append,
    )

    assert result.action == 'feedback_queued'
    assert not saved and not sent
    scheduled[0]()
    assert saved == [(1, 'cand-3', {'verdict': 'like', 'comment': 'Toller Schnitt'})]
    assert sent == [{'chat_id': '523', 'text': '📝 Notiz gespeichert.', 'reply_markup': None}]


def test_each_alert_goes_to_its_owners_chat_and_waits_while_that_chat_is_not_allowed(monkeypatch, tmp_path) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from vsniper.core.database import Base
    from vsniper.db.models import Candidate, User
    from vsniper.services.user_service import UserService

    engine = create_engine(f"sqlite:///{tmp_path / 'routing.db'}", future=True)
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
    monkeypatch.setattr('vsniper.services.user_service.session_scope', fake_session_scope)
    now = datetime.now(UTC)

    def candidate(candidate_id: str, user_id: int) -> Candidate:
        return Candidate(
            id=candidate_id, user_id=user_id, search_id=f's{user_id}', clothing_item='hosen', title=candidate_id,
            brand='B', price_eur=20.0, size='M', url=f'https://example.test/{candidate_id}', image_urls=[],
            matched_filters=[], matched_preferences=[], features=[], normalized_listing={},
            extraction_status='completed',
            score_trace={'final_score': 0.97, 'score_10': 97, 'threshold': 0.95, 'decision': 'alert',
                         'components': [], 'summary': '', 'raw_response': {}},
            decision='alert', final_score=0.97, ai_observation={}, grading_stage='vlm_judged',
            feedback='unknown', feedback_comment='', created_at=now,
        )

    with factory() as session:
        session.add(AppSettingsState(id=1, telegram_bot_token='bot-token'))
        session.add_all([
            User(id=1, name='Host', telegram_chat_id='523'),
            User(id=2, name='Sister', telegram_chat_id='777'),
            User(id=3, name='Removed', telegram_chat_id='999'),
        ])
        session.add_all([candidate('mine', 1), candidate('hers', 2), candidate('removed', 3)])
        for candidate_id in ('mine', 'hers', 'removed'):
            session.add(AlertDeliveryState(
                candidate_id=candidate_id, channel='telegram', status='pending', attempt_count=0,
                payload_preview='', created_at=now, updated_at=now,
            ))
        session.commit()

    sent: list[tuple[str, str]] = []
    service = TelegramService.__new__(TelegramService)
    service.users = UserService(SimpleNamespace(allowed_chat_ids=['523', '777']))
    service.telegram_formatter = SimpleNamespace(
        build_alert_message=lambda candidate, kind='alert': candidate.title,
        build_feedback_reply_markup=lambda delivery_id: None,
    )
    service.telegram_client = SimpleNamespace(
        bot_token='',
        send_message=lambda *, chat_id, text, reply_markup=None: sent.append((chat_id, text)) or {'message_id': 1},
    )

    result = service.process_pending_deliveries()

    assert sorted(sent) == [('523', 'mine'), ('777', 'hers')]
    assert result.sent_deliveries == 2
    with factory() as session:
        removed = session.query(AlertDeliveryState).filter_by(candidate_id='removed').one()
        assert removed.status == 'pending'
