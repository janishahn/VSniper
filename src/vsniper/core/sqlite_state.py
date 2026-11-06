"""Thin facade that wires shared resources and the four domain services together.

Routes and worker jobs go through `get_state()` and call into the services via attributes
(`get_state().searches`, `.preferences`, `.candidates`, `.telegram`, `.buckets`). Business logic lives
in `vsniper/services/*` — this module only owns lifecycle (DB init + seed)."""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from time import perf_counter

import httpx
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from vsniper.core.config import get_settings
from vsniper.core.database import dispose_engine, init_database, session_scope
from vsniper.db.models import AiUsageEvent, AppSettingsState, Search, TasteState
from vsniper.integrations.openai.pricing import compute_cost
from vsniper.domain.contracts import (
    SettingsSnapshot,
)
from vsniper.integrations.openai.client import OpenAITasteClient
from vsniper.integrations.telegram.client import TelegramClient
from vsniper.integrations.telegram.service import TelegramFormatter
from vsniper.integrations.vinted.client import VintedClient
from vsniper.services._mapping import integration_configuration
from vsniper.services.candidate_service import CandidateService
from vsniper.services.error_service import ErrorService
from vsniper.services.bucket_service import BucketService, bucket_records
from vsniper.services.search_defaults import canonical_search_values
from vsniper.services.search_service import SearchService
from vsniper.services.telegram_service import TelegramService
from vsniper.services.taste_service import TasteService

logger = logging.getLogger(__name__)
_state_lock = threading.Lock()
_state_instance: AppState | None = None


def _default_settings(now: datetime) -> SettingsSnapshot:
    runtime = get_settings()
    telegram_configured, ai_configured = integration_configuration()
    return SettingsSnapshot(
        telegram_bot_token=runtime.telegram_bot_token,
        telegram_chat_id=runtime.telegram_chat_id,
        telegram_configured=telegram_configured,
        ai_configured=ai_configured,
        vision_model=runtime.openai_vision_model,
        decision_model=runtime.openai_decision_model,
        alert_threshold=95,
    )


class AppState:
    """Holds shared resources (Settings, integration clients) and exposes the four services
    that contain all business logic."""

    def __init__(self) -> None:
        self._initialize()

    def _initialize(self) -> None:
        started = perf_counter()
        logger.info("AppState initialization started")
        self.settings = get_settings()
        logger.info("AppState settings loaded")

        self._telegram_http_client = httpx.Client(timeout=10)
        self.vinted_client = VintedClient()
        self.taste_client = OpenAITasteClient(self.settings, on_usage=self._record_ai_usage)
        self.telegram_client = TelegramClient(client=self._telegram_http_client)
        self.telegram_formatter = TelegramFormatter()
        logger.info("AppState integration clients initialized")

        init_database()
        logger.info("AppState database initialized")
        self._seed_if_empty()
        logger.info("AppState defaults seeded")

        self.errors = ErrorService(self.settings, self.telegram_client)
        self.buckets = BucketService(self.settings, self.vinted_client)
        self.taste = TasteService(self.settings, self.taste_client, self.vinted_client, self.errors)
        self.preferences = self.taste
        self.candidates = CandidateService(self.settings, self.taste, self.taste_client, self.errors)
        self.telegram = TelegramService(
            self.settings,
            self.telegram_client,
            self.telegram_formatter,
            self.candidates,
            self.vinted_client,
            self.taste,
            self.errors,
        )
        self.searches = SearchService(
            self.settings, self.vinted_client, self.taste_client, self.taste, self.telegram, self.errors
        )
        self.telegram.set_search_draft_applier(self.searches.apply_generated_search_drafts)
        logger.info("AppState services initialized in %.1fs", perf_counter() - started)

    def reload(self) -> None:
        """Tear down and re-initialize shared resources against the on-disk DB.

        Called after a full-state import overwrites `vsniper.db` so the running
        API process picks up the new schema, settings, and integration credentials
        without a process restart. The worker sees the new state on its next
        `get_state()` access (it shares the singleton)."""
        logger.info("AppState reload started")
        try:
            self.close()
        except Exception:
            logger.exception("AppState close during reload failed; continuing with re-init")
        dispose_engine()
        self._initialize()
        logger.info("AppState reload finished")

    @staticmethod
    def _seed_if_empty() -> None:
        now = datetime.now(UTC)
        logger.info("AppState seed_if_empty started")
        with session_scope() as session:
            settings = _default_settings(now)
            session.execute(
                sqlite_insert(AppSettingsState)
                .values(
                    id=1,
                    telegram_bot_token=settings.telegram_bot_token or "",
                    telegram_chat_id=settings.telegram_chat_id or "",
                    telegram_configured=settings.telegram_configured,
                    error_telegram_notifications_enabled=False,
                    alert_threshold=settings.alert_threshold,
                    scan_interval_seconds=settings.scan_interval_seconds,
                )
                .on_conflict_do_nothing()
            )
            session.execute(
                sqlite_insert(TasteState)
                .values(id=1, manual_note="", taste_profile={}, reference_observations=[])
                .on_conflict_do_nothing()
            )
            # Buckets are seeded by their migration; every bucket owns one search.
            for bucket in bucket_records(session, include_archived=True):
                session.execute(
                    sqlite_insert(Search)
                    .values(**canonical_search_values(bucket, now=now))
                    .on_conflict_do_nothing()
                )
        logger.info("AppState seed_if_empty finished")

    def _record_ai_usage(
        self, operation: str, model: str, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0
    ) -> None:
        with session_scope() as session:
            session.add(AiUsageEvent(
                called_at=datetime.now(UTC),
                operation=operation,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                cost_usd=compute_cost(model, input_tokens, output_tokens, cached_input_tokens),
            ))

    def build_test_telegram_message(self) -> str:
        """Convenience for the /api/telegram/test-preview route — needs candidate list + formatter."""
        return self.telegram.build_test_message(self.candidates.page(limit=1).items)

    def close(self) -> None:
        self.taste_client.close()
        self.vinted_client.close()
        self._telegram_http_client.close()


def get_state() -> AppState:
    global _state_instance
    if _state_instance is None:
        with _state_lock:
            if _state_instance is None:
                _state_instance = AppState()
    return _state_instance
