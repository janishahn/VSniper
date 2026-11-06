from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vsniper.core.database import Base
from vsniper.db.models import Bucket, Search, TasteState, User
from vsniper.services.user_service import UserService


def test_allowed_chats_take_over_the_unlinked_profile_then_get_new_ones(monkeypatch, tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'users.db'}", future=True)
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

    monkeypatch.setattr("vsniper.services.user_service.session_scope", fake_session_scope)
    with factory() as session:
        # Profile 1 is what the migration makes from the data that existed before profiles.
        session.add(User(id=1, name="Profile 1", telegram_chat_id=None))
        session.flush()
        session.add(Bucket(user_id=1, key="hosen", label="Hosen", catalog_ids=[9], sizes=[{"id": 4, "title": "M"}]))
        session.add(Bucket(user_id=1, key="alt", label="Alt", catalog_ids=[9], archived=True))
        session.commit()

    service = UserService(SimpleNamespace(allowed_chat_ids=["523", "777"]))
    service.sync_allowed_chats()
    service.sync_allowed_chats()  # a restart changes nothing

    users = service.all()
    assert [(user.id, user.telegram_chat_id, user.authorized) for user in users] == [(1, "523", True), (2, "777", True)]
    assert service.user_for_chat(777) == 2
    assert service.user_for_chat(999) is None
    with factory() as session:
        copied = session.scalars(select(Bucket).where(Bucket.user_id == 2)).all()
        assert [(bucket.key, bucket.sizes) for bucket in copied] == [("hosen", [])]
        search = session.scalars(select(Search).where(Search.user_id == 2)).one()
        assert (search.id, search.clothing_item) == ("search-2-hosen", "hosen")
        assert {state.user_id for state in session.scalars(select(TasteState))} == {1, 2}

    # A chat removed from TELEGRAM_CHAT_IDS keeps its profile but loses access.
    removed = UserService(SimpleNamespace(allowed_chat_ids=["523"]))
    assert removed.get(2).authorized is False
    assert removed.user_for_chat(777) is None
    assert removed.authorized_user_ids() == [1]
