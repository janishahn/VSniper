"""User profiles. TELEGRAM_CHAT_IDS lists the chats allowed to use the bot; each has a profile
that owns its buckets, searches, candidates and taste. Profiles are managed in the web UI."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vsniper.core.config import Settings
from vsniper.core.database import session_scope
from vsniper.db.models import Bucket, Search, TasteState, User
from vsniper.domain.contracts import UserRecord, UserUpdate
from vsniper.services.bucket_service import bucket_records
from vsniper.services.search_defaults import canonical_search_values

logger = logging.getLogger(__name__)


class UserNotFound(KeyError):
    pass


def require_user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise UserNotFound(user_id)
    return user


def normalize_blocked_brands(brands: list[str]) -> list[str]:
    """Strip, drop empties, and dedupe case-insensitively while keeping first-seen casing/order."""
    seen: set[str] = set()
    normalized: list[str] = []
    for brand in brands:
        stripped = brand.strip()
        if not stripped or stripped.lower() in seen:
            continue
        seen.add(stripped.lower())
        normalized.append(stripped)
    return normalized


class UserService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _record(self, user: User) -> UserRecord:
        return UserRecord(
            id=user.id,
            name=user.name,
            telegram_chat_id=user.telegram_chat_id,
            authorized=user.telegram_chat_id in self.settings.allowed_chat_ids,
            alert_threshold=user.alert_threshold,
            blocked_brands=list(user.blocked_brands or []),
        )

    def all(self) -> list[UserRecord]:
        with session_scope() as session:
            return [self._record(user) for user in session.scalars(select(User).order_by(User.id))]

    def get(self, user_id: int) -> UserRecord:
        with session_scope() as session:
            return self._record(require_user(session, user_id))

    def update(self, user_id: int, payload: UserUpdate) -> UserRecord:
        with session_scope() as session:
            user = require_user(session, user_id)
            user.name = payload.name.strip()
            user.alert_threshold = payload.alert_threshold
            session.flush()
            return self._record(user)

    def update_blocked_brands(self, user_id: int, brands: list[str]) -> list[str]:
        with session_scope() as session:
            user = require_user(session, user_id)
            user.blocked_brands = normalize_blocked_brands(brands)
            session.flush()
            return list(user.blocked_brands)

    def authorized_user_ids(self) -> list[int]:
        allowed = self.settings.allowed_chat_ids
        if not allowed:
            return []
        with session_scope() as session:
            return list(session.scalars(select(User.id).where(User.telegram_chat_id.in_(allowed))))

    def user_for_chat(self, chat_id: int | str | None) -> int | None:
        """The profile of an allowed chat, or None for a chat the bot must ignore."""
        if chat_id is None or str(chat_id) not in self.settings.allowed_chat_ids:
            return None
        with session_scope() as session:
            return session.scalar(select(User.id).where(User.telegram_chat_id == str(chat_id)))

    def chat_for_user(self, session: Session, user_id: int) -> str | None:
        """Where the user's messages go, or None while the user's chat is not allowed."""
        chat_id = require_user(session, user_id).telegram_chat_id
        return chat_id if chat_id in self.settings.allowed_chat_ids else None

    def sync_allowed_chats(self) -> None:
        """Give every allowed chat a profile. A chat first takes over a profile that has no chat
        yet (the data from before profiles existed); otherwise it gets a new profile that starts
        with a copy of the first profile's buckets."""
        now = datetime.now(UTC)
        with session_scope() as session:
            linked = set(session.scalars(select(User.telegram_chat_id).where(User.telegram_chat_id.isnot(None))))
            for chat_id in self.settings.allowed_chat_ids:
                if chat_id in linked:
                    continue
                unlinked = session.scalar(select(User).where(User.telegram_chat_id.is_(None)).order_by(User.id).limit(1))
                if unlinked is not None:
                    unlinked.telegram_chat_id = chat_id
                    session.flush()
                    logger.info("linked Telegram chat to profile %d", unlinked.id)
                    continue
                user = User(name=f"Chat {chat_id}", telegram_chat_id=chat_id, alert_threshold=95, blocked_brands=[], created_at=now)
                session.add(user)
                session.flush()
                self._copy_starter_buckets(session, user.id, now=now)
                logger.info("created profile %d for a new Telegram chat", user.id)
            for user_id in session.scalars(select(User.id)):
                if session.get(TasteState, user_id) is None:
                    session.add(TasteState(user_id=user_id, manual_note="", taste_profile={}, reference_observations=[]))
            session.flush()

    @staticmethod
    def _copy_starter_buckets(session: Session, user_id: int, *, now: datetime) -> None:
        first_user_id = session.scalar(select(User.id).where(User.id != user_id).order_by(User.id).limit(1))
        if first_user_id is None:
            return
        for bucket in bucket_records(session, first_user_id):
            session.add(
                Bucket(
                    user_id=user_id,
                    key=bucket.key,
                    label=bucket.label,
                    description=bucket.description,
                    catalog_ids=[category.id for category in bucket.categories],
                    sizes=[],
                    position=bucket.position,
                    archived=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            session.add(Search(**canonical_search_values(user_id, bucket, now=now)))
