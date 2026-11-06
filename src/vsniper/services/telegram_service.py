from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Callable, Literal, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from vsniper.core.config import Settings, get_settings
from vsniper.core.database import session_scope
from vsniper.db.models import AlertDeliveryState, AppSettingsState, Candidate
from vsniper.domain.contracts import (
    DeliveryProcessingResult,
    SearchDraftApplyResult,
    TelegramCallbackMessage,
    TelegramCallbackQuery,
    TelegramMessage,
    TelegramUpdate,
    TelegramUpdateResult,
)
from vsniper.integrations.telegram.client import TelegramClient
from vsniper.integrations.telegram.service import TelegramFormatter
from vsniper.integrations.vinted.client import VintedClient
from vsniper.services._mapping import (
    as_aware,
    candidate_to_contract,
    is_value_configured,
)
from vsniper.services.candidate_service import CandidateService
from vsniper.services.error_service import ErrorService
from vsniper.services.taste_service import TasteService
from vsniper.services.user_service import UserService

logger = logging.getLogger(__name__)

DELIVERY_MAX_ATTEMPTS = 3
DELIVERY_RETRY_DELAYS = (timedelta(seconds=0), timedelta(seconds=30), timedelta(minutes=5))
DELIVERY_PROCESSING_TIMEOUT = timedelta(minutes=10)
ACTIVE_DELIVERY_STATUSES = ("pending", "processing", "sent")
# Statuses that block re-queuing the same candidate. Includes "failed" so a delivery
# that exhausted its retries is not re-inserted (with fresh attempts) on every scan.
DEDUP_DELIVERY_STATUSES = ACTIVE_DELIVERY_STATUSES + ("failed",)
_LOCAL_TZ = ZoneInfo("Europe/Berlin")
# The daily borderline probe: at most one per interval, scored within the margin below its threshold.
BORDERLINE_INTERVAL = timedelta(hours=24)
BORDERLINE_MARGIN = 15


@dataclass(frozen=True)
class TelegramRuntimeConfig:
    bot_token: str

    @property
    def configured(self) -> bool:
        return is_value_configured(self.bot_token, placeholder="put-your-telegram-bot-token-here")


def _telegram_config_from_model(model: AppSettingsState | None) -> TelegramRuntimeConfig:
    runtime = get_settings()
    bot_token = (model.telegram_bot_token if model and model.telegram_bot_token else runtime.telegram_bot_token).strip()
    return TelegramRuntimeConfig(bot_token=bot_token)


def _telegram_config() -> TelegramRuntimeConfig:
    with session_scope() as session:
        return _telegram_config_from_model(session.get(AppSettingsState, 1))


class TelegramService:
    def __init__(
        self,
        settings: Settings,
        telegram_client: TelegramClient,
        telegram_formatter: TelegramFormatter,
        candidates: CandidateService,
        vinted_client: VintedClient,
        taste: TasteService,
        users: UserService,
        errors: ErrorService | None = None,
    ) -> None:
        self.settings = settings
        self.users = users
        self.telegram_client = telegram_client
        self.telegram_formatter = telegram_formatter
        self.candidates = candidates
        self.vinted_client = vinted_client
        self.taste = taste
        self.errors = errors
        self._apply_search_drafts: Callable[[int, int | None], SearchDraftApplyResult] | None = None
        self._polling_bot_token: str | None = None

    def set_search_draft_applier(self, applier: Callable[[int, int | None], SearchDraftApplyResult]) -> None:
        self._apply_search_drafts = applier

    def _apply_bot_token(self, config: TelegramRuntimeConfig) -> None:
        self.telegram_client.bot_token = config.bot_token

    def send_test_notification(self, user_id: int) -> dict[str, object]:
        config = _telegram_config()
        self._apply_bot_token(config)
        if not config.configured:
            raise ValueError("Telegram bot token is missing.")
        with session_scope() as session:
            chat_id = self.users.chat_for_user(session, user_id)
        if chat_id is None:
            raise ValueError("This profile's Telegram chat is not listed in TELEGRAM_CHAT_IDS.")

        response = self.telegram_client.send_message(
            chat_id=chat_id,
            text="vsniper-Testnachricht: Telegram ist eingerichtet.",
        )
        return {"ok": True, "message_id": response.get("message_id"), "chat_id": chat_id}

    @staticmethod
    def _get_latest_delivery(session: Session, candidate_id: str) -> AlertDeliveryState | None:
        return session.scalar(
            select(AlertDeliveryState)
            .where(AlertDeliveryState.candidate_id == candidate_id)
            .order_by(AlertDeliveryState.created_at.desc())
            .limit(1)
        )

    def queue_delivery(self, session: Session, candidate: Candidate, kind: str = "alert") -> bool:
        preview_message = self.telegram_formatter.build_alert_message(candidate_to_contract(candidate), kind)
        now = datetime.now(UTC)
        result = cast(
            CursorResult,
            session.execute(
                sqlite_insert(AlertDeliveryState)
                .values(
                    candidate_id=candidate.id,
                    channel="telegram",
                    kind=kind,
                    status="pending",
                    attempt_count=0,
                    last_error=None,
                    payload_preview=preview_message,
                    created_at=now,
                    updated_at=now,
                    last_attempted_at=None,
                    sent_at=None,
                )
                .on_conflict_do_nothing(
                    index_elements=[AlertDeliveryState.candidate_id, AlertDeliveryState.channel],
                    index_where=AlertDeliveryState.status.in_(DEDUP_DELIVERY_STATUSES),
                )
            ),
        )
        return result.rowcount == 1

    def queue_borderline_probe(self, user_id: int) -> bool:
        """Once a day, send the user the best near-miss of the last day: a judged listing without
        feedback that scored just under its alert threshold. Alerts alone never show the
        system what it scores too low, so these verdicts are the most informative."""
        now = datetime.now(UTC)
        with session_scope() as session:
            config = _telegram_config_from_model(session.get(AppSettingsState, 1))
            if not config.configured or self.users.chat_for_user(session, user_id) is None:
                return False
            last_sent = as_aware(
                session.scalar(
                    select(func.max(AlertDeliveryState.created_at))
                    .join(Candidate, Candidate.id == AlertDeliveryState.candidate_id)
                    .where(Candidate.user_id == user_id, AlertDeliveryState.kind == "borderline")
                )
            )
            if last_sent is not None and now - last_sent < BORDERLINE_INTERVAL:
                return False
            recent = session.scalars(
                select(Candidate).where(
                    Candidate.user_id == user_id,
                    Candidate.created_at >= now - BORDERLINE_INTERVAL,
                    Candidate.feedback.notin_(("like", "dislike")),
                    Candidate.grading_stage == "vlm_judged",
                    ~Candidate.alert_deliveries.any(),
                )
            ).all()
            best: Candidate | None = None
            best_score = -1
            for candidate in recent:
                trace = candidate.score_trace or {}
                if "score_10" not in trace or "threshold" not in trace:
                    continue
                score, threshold = int(trace["score_10"]), round(float(trace["threshold"]) * 100)
                if threshold - BORDERLINE_MARGIN <= score < threshold and score > best_score:
                    best, best_score = candidate, score
            if best is None:
                return False
            return self.queue_delivery(session, best, kind="borderline")

    def retry_delivery(self, user_id: int, candidate_id: str) -> bool:
        """Re-queue the latest failed delivery for a candidate so the worker picks it up next
        cycle. Resets the attempt counter and backoff timer so it is immediately eligible.
        Returns False if there is no delivery to retry or it is not in a failed state."""
        now = datetime.now(UTC)
        with session_scope() as session:
            candidate = session.get(Candidate, candidate_id)
            if candidate is None or candidate.user_id != user_id:
                return False
            delivery = self._get_latest_delivery(session, candidate_id)
            if delivery is None or delivery.status != "failed":
                return False
            delivery.status = "pending"
            delivery.attempt_count = 0
            delivery.last_error = None
            delivery.last_attempted_at = None
            delivery.sent_at = None
            delivery.updated_at = now
            return True

    @staticmethod
    def _retry_delay(attempt_count: int) -> timedelta:
        index = max(0, min(attempt_count, len(DELIVERY_RETRY_DELAYS) - 1))
        return DELIVERY_RETRY_DELAYS[index]

    @classmethod
    def _is_eligible(cls, delivery: AlertDeliveryState, *, now: datetime) -> bool:
        if delivery.status not in {"pending", "processing"}:
            return False
        if delivery.attempt_count >= DELIVERY_MAX_ATTEMPTS:
            return False
        if delivery.last_attempted_at is None:
            return True
        last_attempted_at = cast(datetime, as_aware(delivery.last_attempted_at))
        if delivery.status == "processing":
            return last_attempted_at + DELIVERY_PROCESSING_TIMEOUT <= now
        return last_attempted_at + cls._retry_delay(delivery.attempt_count) <= now

    @staticmethod
    def _count_pending_deliveries(session: Session, user_ids: list[int]) -> int:
        # Cheap COUNT for the zero-check / skip messaging — does not load rows into memory.
        return int(
            session.scalar(
                select(func.count())
                .select_from(AlertDeliveryState)
                .join(Candidate, Candidate.id == AlertDeliveryState.candidate_id)
                .where(
                    Candidate.user_id.in_(user_ids),
                    AlertDeliveryState.channel == "telegram",
                    AlertDeliveryState.status.in_(("pending", "processing")),
                )
            )
            or 0
        )

    def _pending_deliveries(
        self, session: Session, user_ids: list[int], *, limit: int | None = None
    ) -> list[AlertDeliveryState]:
        # Bounded so a backlog can't load the whole table into memory; the Python eligibility
        # filter below may drop some (not-yet-due retries), so callers fetch a multiple of what
        # they intend to claim to leave headroom. Alerts of users whose chat is no longer allowed
        # wait until it is.
        query = (
            select(AlertDeliveryState)
            .join(Candidate, Candidate.id == AlertDeliveryState.candidate_id)
            .where(
                Candidate.user_id.in_(user_ids),
                AlertDeliveryState.channel == "telegram",
                AlertDeliveryState.status.in_(("pending", "processing")),
            )
            .order_by(AlertDeliveryState.created_at.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        deliveries = session.scalars(query).all()
        now = datetime.now(UTC)
        return [delivery for delivery in deliveries if self._is_eligible(delivery, now=now)]

    def _claim_pending_deliveries(
        self, session: Session, user_ids: list[int], *, limit: int
    ) -> list[AlertDeliveryState]:
        claimed: list[AlertDeliveryState] = []
        # Fetch a multiple of the claim target so the eligibility filter has headroom.
        for delivery in self._pending_deliveries(session, user_ids, limit=max(limit, 1) * 4):
            if len(claimed) >= limit:
                break

            attempted_at = datetime.now(UTC)
            result = cast(
                CursorResult,
                session.execute(
                    update(AlertDeliveryState)
                    .where(AlertDeliveryState.id == delivery.id)
                    .where(AlertDeliveryState.status == delivery.status)
                    .where(AlertDeliveryState.attempt_count == delivery.attempt_count)
                    .values(
                        status="processing",
                        attempt_count=delivery.attempt_count + 1,
                        last_attempted_at=attempted_at,
                        updated_at=attempted_at,
                    )
                ),
            )
            if result.rowcount != 1:
                continue

            session.flush()
            session.refresh(delivery)
            claimed.append(delivery)
        return claimed

    @staticmethod
    def _record_success(
        delivery: AlertDeliveryState,
        *,
        attempted_at: datetime,
        telegram_message_id: int | None = None,
        telegram_chat_id: str | None = None,
    ) -> None:
        delivery.status = "sent"
        delivery.last_error = None
        delivery.sent_at = attempted_at
        delivery.updated_at = attempted_at
        delivery.telegram_message_id = telegram_message_id
        delivery.telegram_chat_id = telegram_chat_id

    @staticmethod
    def _record_failure(
        delivery: AlertDeliveryState,
        *,
        attempted_at: datetime,
        error_message: str,
        retryable: bool,
    ) -> str:
        delivery.last_error = error_message
        delivery.sent_at = None
        delivery.updated_at = attempted_at
        if retryable and delivery.attempt_count < DELIVERY_MAX_ATTEMPTS:
            delivery.status = "pending"
            return "retry"
        delivery.status = "failed"
        return "failed"

    def _send_claimed_delivery(self, delivery_id: int, config: TelegramRuntimeConfig) -> str:
        """Send a single already-claimed delivery and write its result back in its own
        short transaction. No write lock is held across the (slow) Telegram network call,
        and the per-delivery commit bounds re-send-on-crash to at most one alert."""
        # Build the alert message in a short read transaction; the write lock is not held
        # while the network call below runs.
        with session_scope() as session:
            delivery = session.get(AlertDeliveryState, delivery_id)
            if delivery is None:
                return "failed"
            candidate = session.get(Candidate, delivery.candidate_id)
            if candidate is None:
                outcome = self._record_failure(
                    delivery,
                    attempted_at=datetime.now(UTC),
                    error_message="Candidate no longer exists for this alert delivery.",
                    retryable=False,
                )
                errors = getattr(self, "errors", None)
                if errors is not None:
                    errors.record(
                        source="telegram",
                        operation="deliver_candidate_alert",
                        summary="Telegram candidate alert failed",
                        message="Candidate no longer exists for this alert delivery.",
                        details={"delivery_id": delivery_id, "candidate_id": delivery.candidate_id},
                        related_entity_type="alert_delivery",
                        related_entity_id=delivery_id,
                        session=session,
                    )
                return outcome
            payload_preview = self.telegram_formatter.build_alert_message(
                candidate_to_contract(candidate), delivery.kind
            )
            # The alert goes to the chat of the user who owns the listing, and nowhere else.
            chat_id = self.users.chat_for_user(session, candidate.user_id)
            if chat_id is None:
                return self._record_failure(
                    delivery,
                    attempted_at=datetime.now(UTC),
                    error_message="The user's Telegram chat is not listed in TELEGRAM_CHAT_IDS.",
                    retryable=False,
                )

        attempted_at = datetime.now(UTC)
        try:
            response = self.telegram_client.send_message(
                chat_id=chat_id,
                text=payload_preview,
                reply_markup=self.telegram_formatter.build_feedback_reply_markup(str(delivery_id)),
            )
        except Exception as exc:
            with session_scope() as session:
                delivery = session.get(AlertDeliveryState, delivery_id)
                if delivery is None:
                    return "failed"
                delivery.payload_preview = payload_preview
                outcome = self._record_failure(
                    delivery,
                    attempted_at=attempted_at,
                    error_message=str(exc),
                    retryable=getattr(exc, "retryable", False),
                )
                errors = getattr(self, "errors", None)
                if outcome == "failed" and errors is not None:
                    errors.record(
                        source="telegram",
                        operation="deliver_candidate_alert",
                        summary="Telegram candidate alert exhausted its retries",
                        exception=exc,
                        details={
                            "delivery_id": delivery_id,
                            "candidate_id": delivery.candidate_id,
                            "attempt_count": delivery.attempt_count,
                            "payload_preview": payload_preview,
                        },
                        related_entity_type="alert_delivery",
                        related_entity_id=delivery_id,
                        session=session,
                    )
                return outcome

        with session_scope() as session:
            delivery = session.get(AlertDeliveryState, delivery_id)
            if delivery is None:
                return "sent"
            delivery.payload_preview = payload_preview
            self._record_success(
                delivery,
                attempted_at=attempted_at,
                telegram_message_id=response.get("message_id"),
                telegram_chat_id=str(response.get("chat", {}).get("id")) if response.get("chat") else None,
            )
        return "sent"

    def process_pending_deliveries(self, limit: int = 25) -> DeliveryProcessingResult:
        # Phase 1: claim eligible deliveries in a short transaction and commit, so the
        # SQLite write lock is released before any (slow) Telegram sends. The committed
        # "processing" status is the cross-process lock that keeps a second worker from
        # re-sending the same alerts.
        user_ids = self.users.authorized_user_ids()
        with session_scope() as session:
            config = _telegram_config_from_model(session.get(AppSettingsState, 1))
            self._apply_bot_token(config)
            pending_count = self._count_pending_deliveries(session, user_ids)
            if pending_count == 0:
                return DeliveryProcessingResult(summary="No eligible Telegram deliveries were waiting to be processed.")

            if not config.configured:
                return DeliveryProcessingResult(
                    eligible_deliveries=pending_count,
                    skipped_reason="Telegram credentials are missing.",
                    summary=(
                        f"Skipped Telegram delivery processing because credentials are missing; "
                        f"{pending_count} queued deliveries remain pending."
                    ),
                )

            eligible_deliveries = self._claim_pending_deliveries(session, user_ids, limit=limit)
            claimed_ids = [delivery.id for delivery in eligible_deliveries]

        eligible_count = len(claimed_ids)
        if eligible_count == 0:
            return DeliveryProcessingResult(summary="No eligible Telegram deliveries were claimed for processing.")

        sent_deliveries = 0
        retry_scheduled_deliveries = 0
        failed_deliveries = 0
        processed_deliveries = 0

        # Phase 2: send each claimed delivery without holding the write lock; results are
        # written back per delivery in their own short transactions.
        for delivery_id in claimed_ids:
            outcome = self._send_claimed_delivery(delivery_id, config)
            processed_deliveries += 1
            if outcome == "sent":
                sent_deliveries += 1
            elif outcome == "retry":
                retry_scheduled_deliveries += 1
            else:
                failed_deliveries += 1

        return DeliveryProcessingResult(
            eligible_deliveries=eligible_count,
            processed_deliveries=processed_deliveries,
            sent_deliveries=sent_deliveries,
            retry_scheduled_deliveries=retry_scheduled_deliveries,
            failed_deliveries=failed_deliveries,
            summary=(
                f"Processed {processed_deliveries} Telegram deliveries: "
                f"{sent_deliveries} sent, {retry_scheduled_deliveries} scheduled for retry, "
                f"and {failed_deliveries} marked failed."
            ),
        )

    def _acknowledge_callback(self, callback_query_id: str, text: str) -> None:
        if not callback_query_id.strip():
            return
        try:
            self.telegram_client.answer_callback_query(callback_query_id=callback_query_id, text=text)
        except Exception as exc:
            logger.warning("Failed to ACK Telegram callback %s", callback_query_id, exc_info=True)
            errors = getattr(self, "errors", None)
            if errors is not None:
                errors.record(
                    source="telegram",
                    operation="acknowledge_callback",
                    summary="Telegram callback acknowledgement failed",
                    exception=exc,
                    details={"callback_query_id": callback_query_id},
                )

    def _update_feedback_message(self, callback_query: TelegramCallbackQuery, result: TelegramUpdateResult) -> None:
        if result.action not in {"feedback_recorded", "feedback_unchanged"} or result.verdict is None:
            return

        message = callback_query.message
        if message is None or message.chat is None or message.text is None or not message.text.strip():
            return

        updated_text = self.telegram_formatter.build_feedback_applied_message(
            message_text=message.text,
            verdict=result.verdict,
        )
        if updated_text == message.text:
            return

        try:
            self.telegram_client.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.message_id,
                text=updated_text,
                reply_markup={"inline_keyboard": []},
            )
        except Exception as exc:
            logger.warning(
                "Failed to edit Telegram message %s in chat %s",
                message.message_id,
                message.chat.id,
                exc_info=True,
            )
            errors = getattr(self, "errors", None)
            if errors is not None:
                errors.record(
                    source="telegram",
                    operation="edit_feedback_message",
                    summary="Telegram feedback message update failed",
                    exception=exc,
                    details={"message_id": message.message_id, "chat_id": str(message.chat.id)},
                    related_entity_type="telegram_message",
                    related_entity_id=message.message_id,
                )

    @staticmethod
    def _callback_ack_text(result: TelegramUpdateResult) -> str:
        verdict = "Gefällt mir" if result.verdict == "like" else "Gefällt mir nicht"
        if result.action == "feedback_recorded" and result.verdict is not None:
            return f"Gespeichert: {verdict}"
        if result.action == "feedback_unchanged" and result.verdict is not None:
            return f"Schon gespeichert: {verdict}"
        if result.action == "taste_recompute_started":
            return "Neuberechnung gestartet."
        if result.action == "taste_recompute_already_running":
            return "Die Berechnung läuft schon."
        if result.action == "taste_drafts_applied":
            return "Entscheidung gespeichert."
        if result.action == "taste_drafts_skipped":
            return "Übersprungen."
        if result.action == "unauthorized":
            return "Dieser Chat ist nicht freigeschaltet."
        return "Das konnte ich nicht verarbeiten."

    @staticmethod
    def _message_chat_id(message: TelegramMessage | TelegramCallbackMessage | None) -> int | str | None:
        if message is None or message.chat is None:
            return None
        return message.chat.id

    def _send_telegram_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict | None = None,
    ) -> None:
        try:
            self.telegram_client.send_message(chat_id=str(chat_id), text=text, reply_markup=reply_markup)
        except Exception as exc:
            logger.warning("Failed to send Telegram message to chat %s", chat_id, exc_info=True)
            errors = getattr(self, "errors", None)
            if errors is not None:
                errors.record(
                    source="telegram",
                    operation="send_message",
                    summary="Telegram message failed",
                    exception=exc,
                    details={"chat_id": str(chat_id), "message_preview": text[:500]},
                )

    def _edit_or_send_callback_message(
        self,
        callback_query: TelegramCallbackQuery,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict | None = None,
    ) -> None:
        message = callback_query.message
        if message is not None:
            try:
                self.telegram_client.edit_message_text(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text=text,
                    reply_markup=reply_markup,
                )
                return
            except Exception:
                logger.warning("Failed to edit Telegram taste message; sending a new one instead", exc_info=True)
        self._send_telegram_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

    def _handle_taste_command(self, *, user_id: int, chat_id: int | str) -> TelegramUpdateResult:
        snapshot = self.taste.get_snapshot(user_id)
        self._send_telegram_message(
            chat_id=chat_id,
            text=self.telegram_formatter.build_taste_status_message(snapshot),
            reply_markup=self.telegram_formatter.build_taste_status_reply_markup(),
        )
        return TelegramUpdateResult(
            action="taste_status_sent",
            detail="Sent taste status to Telegram.",
            profile_version=snapshot.taste_profile.version if snapshot.taste_profile else None,
        )

    def _run_taste_recompute_background(self, *, user_id: int, job_id: str, chat_id: int | str) -> None:
        config = _telegram_config()
        self._apply_bot_token(config)
        try:
            result = self.taste.run_claimed_recompute(user_id, job_id)
        except Exception as exc:
            logger.exception("Telegram-triggered taste recompute failed")
            self._send_telegram_message(
                chat_id=chat_id,
                text=self.telegram_formatter.build_taste_recompute_failed_message(str(exc)),
            )
            return

        profile = result.snapshot.taste_profile
        reply_markup = None
        if profile is not None and profile.generated_searches:
            reply_markup = self.telegram_formatter.build_taste_draft_reply_markup(profile_version=profile.version)
        self._send_telegram_message(
            chat_id=chat_id,
            text=self.telegram_formatter.build_taste_recompute_success_message(result),
            reply_markup=reply_markup,
        )

    def _handle_taste_callback(
        self,
        callback_query: TelegramCallbackQuery,
        *,
        user_id: int,
        chat_id: int | str,
        schedule_background: Callable[[Callable[[], None]], None] | None = None,
    ) -> TelegramUpdateResult:
        parsed = self.telegram_formatter.parse_taste_callback_data(callback_query.data or "")
        if parsed is None:
            result = TelegramUpdateResult(action="invalid_callback", detail="Telegram taste callback was not recognized.")
            self._acknowledge_callback(callback_query.id, self._callback_ack_text(result))
            return result

        if parsed.action == "recompute":
            claim = self.taste.claim_recompute(user_id, source="telegram")
            if not claim.claimed or claim.job_id is None:
                text = self.telegram_formatter.build_taste_recompute_already_running_message(
                    started_at=claim.running_started_at,
                )
                result = TelegramUpdateResult(
                    action="taste_recompute_already_running",
                    detail=text,
                    recompute_job_id=claim.running_job_id,
                )
                self._edit_or_send_callback_message(callback_query, chat_id=chat_id, text=text)
                self._acknowledge_callback(callback_query.id, self._callback_ack_text(result))
                return result

            text = self.telegram_formatter.build_taste_recompute_started_message(started_at=claim.started_at)
            self._edit_or_send_callback_message(callback_query, chat_id=chat_id, text=text)

            def task() -> None:
                self._run_taste_recompute_background(user_id=user_id, job_id=cast(str, claim.job_id), chat_id=chat_id)

            if schedule_background is None:
                task()
            else:
                schedule_background(task)
            result = TelegramUpdateResult(
                action="taste_recompute_started",
                detail=text,
                recompute_job_id=claim.job_id,
            )
            self._acknowledge_callback(callback_query.id, self._callback_ack_text(result))
            return result

        if parsed.action == "skip_drafts":
            text = "Suchvorschläge übersprungen. Schick /taste, wenn du sie dir später ansehen willst."
            result = TelegramUpdateResult(
                action="taste_drafts_skipped",
                detail=text,
                profile_version=parsed.profile_version,
            )
            self._edit_or_send_callback_message(
                callback_query,
                chat_id=chat_id,
                text=text,
                reply_markup={"inline_keyboard": []},
            )
            self._acknowledge_callback(callback_query.id, self._callback_ack_text(result))
            return result

        if self._apply_search_drafts is None:
            result = TelegramUpdateResult(action="invalid_callback", detail="Search draft application is not configured.")
            self._acknowledge_callback(callback_query.id, self._callback_ack_text(result))
            return result
        apply_result = self._apply_search_drafts(user_id, parsed.profile_version)
        text = self.telegram_formatter.build_search_draft_apply_message(apply_result)
        result = TelegramUpdateResult(
            action="taste_drafts_applied",
            detail=text,
            profile_version=apply_result.profile_version,
            changed_searches=apply_result.applied_searches,
        )
        self._edit_or_send_callback_message(
            callback_query,
            chat_id=chat_id,
            text=text,
            reply_markup={"inline_keyboard": []},
        )
        self._acknowledge_callback(callback_query.id, self._callback_ack_text(result))
        return result

    def _handle_reply_comment(
        self, message: TelegramMessage, comment: str, *, user_id: int, chat_id: int | str
    ) -> TelegramUpdateResult:
        message_id = message.reply_to_message.message_id if message.reply_to_message else None
        if message_id is None:
            return TelegramUpdateResult(
                action="ignored",
                detail="Reply comment did not reference an alert message.",
            )
        # Phase 1 — resolve the alert reply to its candidate + current verdict in a short txn.
        with session_scope() as session:
            delivery = session.scalar(
                select(AlertDeliveryState)
                .where(
                    AlertDeliveryState.telegram_message_id == message_id,
                    AlertDeliveryState.telegram_chat_id == str(chat_id),
                )
                .limit(1)
            )
            if delivery is None:
                return TelegramUpdateResult(
                    action="ignored",
                    detail="Reply comment did not match any known alert delivery.",
                )
            candidate_model = session.get(Candidate, delivery.candidate_id)
            if candidate_model is None or candidate_model.user_id != user_id:
                return TelegramUpdateResult(
                    action="ignored",
                    detail="Candidate for this delivery no longer exists.",
                )
            candidate_id = candidate_model.id
            existing_feedback = candidate_model.feedback

        if existing_feedback in {"like", "dislike"}:
            # apply_feedback is three-phase: any VLM observation runs without a held write lock.
            _, snapshot = self.candidates.apply_feedback(
                user_id,
                candidate_id,
                verdict=cast(Literal["like", "dislike"], existing_feedback),
                comment=comment,
            )
            return TelegramUpdateResult(
                action="feedback_recorded",
                detail="📝 Notiz gespeichert.",
                candidate_id=candidate_id,
                learning_snapshot_id=snapshot.id if snapshot is not None else None,
            )

        # No verdict yet: store a neutral note-only taste sample with durable assets.
        _, snapshot = self.candidates.apply_note(
            user_id,
            candidate_id,
            comment=comment,
        )
        return TelegramUpdateResult(
            action="feedback_recorded",
            detail="📝 Notiz gespeichert.",
            candidate_id=candidate_id,
            learning_snapshot_id=snapshot.id if snapshot is not None else None,
        )

    def poll_updates(self, *, offset: int, timeout_seconds: int) -> int | None:
        """Fetch and handle one long-poll batch of bot updates.

        Returns the next offset, or ``None`` without calling Telegram while no bot token is
        configured.
        """
        config = _telegram_config()
        if not config.configured:
            return None
        self._apply_bot_token(config)
        if self._polling_bot_token != config.bot_token:
            self.telegram_client.delete_webhook()
            self._polling_bot_token = config.bot_token
        for raw in self.telegram_client.get_updates(offset=offset, timeout_seconds=timeout_seconds):
            offset = int(raw["update_id"]) + 1
            try:
                self.handle_update(
                    TelegramUpdate.model_validate(raw),
                    schedule_background=lambda task: threading.Thread(target=task, daemon=True).start(),
                )
            except Exception:
                logger.exception("Telegram update %s could not be handled", raw.get("update_id"))
        return offset

    def handle_update(
        self,
        payload: TelegramUpdate,
        schedule_background: Callable[[Callable[[], None]], None] | None = None,
    ) -> TelegramUpdateResult:
        config = _telegram_config()
        self._apply_bot_token(config)
        if payload.message and payload.message.text:
            message = payload.message
            msg_text = message.text.strip() if message.text else ""
            if not msg_text:
                return TelegramUpdateResult(action="ignored", detail="Empty Telegram message.")
            # Every reply goes back to the chat that wrote, and acts on that chat's profile only.
            chat_id = self._message_chat_id(message)
            user_id = self.users.user_for_chat(chat_id)
            if chat_id is None or user_id is None:
                return TelegramUpdateResult(action="unauthorized", detail="Telegram chat is not authorized.")
            command = msg_text.split(maxsplit=1)[0].split("@", maxsplit=1)[0].lower()
            if command == "/taste":
                return self._handle_taste_command(user_id=user_id, chat_id=chat_id)
            if message.reply_to_message is not None:
                if schedule_background is None:
                    return self._handle_reply_comment(message, msg_text, user_id=user_id, chat_id=chat_id)

                # Saving a comment can run an AI call; keep it off the polling thread and confirm afterwards.
                def save_comment() -> None:
                    try:
                        result = self._handle_reply_comment(message, msg_text, user_id=user_id, chat_id=chat_id)
                    except Exception:
                        logger.exception("Telegram reply comment could not be saved")
                        self._send_telegram_message(chat_id=chat_id, text="❌ Die Notiz konnte ich nicht speichern.")
                        return
                    if result.action == "feedback_recorded":
                        self._send_telegram_message(chat_id=chat_id, text=result.detail)

                schedule_background(save_comment)
                return TelegramUpdateResult(action="feedback_queued", detail="Saving reply comment.")

        callback_query = payload.callback_query
        if callback_query is None:
            return TelegramUpdateResult(
                action="ignored",
                detail="Telegram update did not contain a supported callback query.",
            )

        if not callback_query.data:
            result = TelegramUpdateResult(
                action="invalid_callback",
                detail="Telegram callback query did not include callback data.",
            )
            self._acknowledge_callback(callback_query.id, self._callback_ack_text(result))
            return result

        callback_chat_id = self._message_chat_id(callback_query.message)
        callback_user_id = self.users.user_for_chat(callback_chat_id)
        if callback_chat_id is None or callback_user_id is None:
            result = TelegramUpdateResult(action="unauthorized", detail="Telegram chat is not authorized.")
            self._acknowledge_callback(callback_query.id, self._callback_ack_text(result))
            return result

        if callback_query.data.startswith("taste:"):
            return self._handle_taste_callback(
                callback_query,
                user_id=callback_user_id,
                chat_id=callback_chat_id,
                schedule_background=schedule_background,
            )

        parsed_callback = self.telegram_formatter.parse_feedback_callback_data(callback_query.data)
        if parsed_callback is None:
            result = TelegramUpdateResult(
                action="invalid_callback",
                detail="Telegram callback payload was not recognized.",
            )
            self._acknowledge_callback(callback_query.id, self._callback_ack_text(result))
            return result

        try:
            delivery_id = int(parsed_callback.delivery_id)
        except ValueError:
            result = TelegramUpdateResult(
                action="invalid_callback",
                detail="Telegram callback delivery ID was not a valid integer.",
                delivery_id=parsed_callback.delivery_id,
                verdict=parsed_callback.verdict,
            )
            self._acknowledge_callback(callback_query.id, self._callback_ack_text(result))
            return result

        verdict = parsed_callback.verdict
        # Telegram stops waiting for an answer after a few seconds, and recording runs an AI call.
        # Answer now; the edited alert message shows the saved vote.
        self._acknowledge_callback(callback_query.id, "Wird gespeichert …")

        def run() -> TelegramUpdateResult:
            # Recording feedback downloads images and runs a VLM observation; keep that work
            # off the polling thread so the next updates are not held up.
            result = self._record_feedback_for_delivery(user_id=callback_user_id, delivery_id=delivery_id, verdict=verdict)
            self._update_feedback_message(callback_query, result)
            return result

        if schedule_background is None:
            return run()

        def background() -> None:
            try:
                result = run()
            except Exception:
                logger.exception("Telegram vote for delivery %s could not be saved", delivery_id)
                self._send_telegram_message(chat_id=callback_chat_id, text="❌ Die Bewertung konnte ich nicht speichern.")
                return
            if result.action not in {"feedback_recorded", "feedback_unchanged"}:
                self._send_telegram_message(chat_id=callback_chat_id, text=self._callback_ack_text(result))

        schedule_background(background)
        return TelegramUpdateResult(
            action="feedback_queued",
            detail=f"Recording your {verdict} feedback…",
            delivery_id=str(delivery_id),
            verdict=verdict,
        )

    def _record_feedback_for_delivery(
        self, *, user_id: int, delivery_id: int, verdict: Literal["like", "dislike"]
    ) -> TelegramUpdateResult:
        # Phase 1 — resolve the delivery to its candidate in a short read transaction.
        with session_scope() as session:
            delivery = session.get(AlertDeliveryState, delivery_id)
            if delivery is None:
                return TelegramUpdateResult(
                    action="invalid_callback",
                    detail="No alert delivery matched this Telegram callback.",
                    delivery_id=str(delivery_id),
                    verdict=verdict,
                )
            candidate_id = delivery.candidate_id

        # Phase 2 — record feedback. apply_feedback is itself three-phase: the image download and
        # VLM observation run with no DB transaction held.
        try:
            # Raises KeyError when the alert belongs to another user, e.g. a forwarded message.
            candidate_record, snapshot = self.candidates.apply_feedback(
                user_id,
                candidate_id,
                verdict=verdict,
                skip_if_unchanged=True,
            )
        except KeyError:
            return TelegramUpdateResult(
                action="invalid_callback",
                detail="The candidate linked to this Telegram delivery no longer exists.",
                delivery_id=str(delivery_id),
                verdict=verdict,
            )

        # Phase 3 — stamp the delivery in a short write transaction.
        with session_scope() as session:
            delivery = session.get(AlertDeliveryState, delivery_id)
            if delivery is not None:
                delivery.updated_at = datetime.now(UTC)

        return TelegramUpdateResult(
            action="feedback_recorded" if snapshot is not None else "feedback_unchanged",
            detail=(
                f"Recorded Telegram {verdict} feedback and refreshed learning snapshots."
                if snapshot is not None
                else f"Candidate was already marked {verdict}; no new learning snapshot was created."
            ),
            candidate_id=candidate_record.id,
            delivery_id=str(delivery_id),
            verdict=verdict,
            learning_snapshot_id=snapshot.id if snapshot is not None else None,
        )

    def build_test_message(self, candidates: list) -> str:
        if not candidates:
            return "No candidates available yet. Run a search first."
        return self.telegram_formatter.build_alert_message(candidates[0])
