"""Tests for SearchService search drafts."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from vsniper.core.database import Base
from vsniper.db.models import AppSettingsState, Bucket, Search, TasteState, User
from vsniper.domain.contracts import GeneratedSearchDraft, SearchFilter, TasteProfile
from vsniper.services.search_service import SearchService


def _setup(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sizes.db'}", future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

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

    monkeypatch.setattr("vsniper.services.search_service.session_scope", fake_session_scope)

    with fake_session_scope() as session:
        session.add(AppSettingsState(id=1))
        session.add(User(id=1, name="Profile 1"))
        session.flush()
        session.add(Bucket(user_id=1, key="schuhe", label="Schuhe", catalog_ids=[16, 2632]))
        session.add(Bucket(user_id=1, key="hosen", label="Hosen", catalog_ids=[9]))
        session.add(Bucket(user_id=1, key="kopf", label="Kopf", catalog_ids=[88]))
        session.add(Bucket(user_id=1, key="obenrum_warm", label="Obenrum Warm", catalog_ids=[12]))
        session.add(Bucket(user_id=1, key="obenrum_mittel", label="Obenrum Mittel", catalog_ids=[13]))
        session.add(Search(user_id=1, id="search-schuhe", name="Schuhe", clothing_item="schuhe", query="q", region="de", run_status="idle"))
        session.add(Search(user_id=1, id="search-hosen", name="Hosen", clothing_item="hosen", query="q", region="de", run_status="idle"))
        session.add(Search(user_id=1, id="search-kopf", name="Kopf", clothing_item="kopf", query="q", region="de", run_status="idle"))
        session.add(Search(user_id=1, id="search-obenrum-warm", name="Obenrum Warm", clothing_item="obenrum_warm", query="q", region="de", run_status="idle"))
        session.add(Search(user_id=1, id="search-obenrum-mittel", name="Obenrum Mittel", clothing_item="obenrum_mittel", query="q", region="de", run_status="idle"))

    service = SearchService.__new__(SearchService)
    return service, factory


def test_apply_generated_drafts_preserves_the_user_price_ceiling(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    existing_price = SearchFilter(field="price", label="Maximum price", values=["20"], mode="range")
    draft_price = SearchFilter(field="price", label="Maximum price", values=["80"], mode="range")
    draft_category = SearchFilter(field="category", label="Vinted category", values=["2632"], mode="include")
    profile = TasteProfile(
        version=4,
        summary="",
        taste_prompt="",
        generated_searches=[
            GeneratedSearchDraft(
                id="draft-shoes",
                clothing_item="schuhe",
                name="Generated shoes",
                query="retro sneaker",
                region="de",
                filters=[draft_price, draft_category],
                created_at=datetime.now(UTC),
            )
        ],
    )
    with factory() as session:
        search = session.get(Search, "search-schuhe")
        search.filters = [existing_price.model_dump(mode="json")]
        session.add(TasteState(user_id=1, taste_profile=profile.model_dump(mode="json")))
        session.commit()

    service.preferences = SimpleNamespace(get_taste_state=lambda session, user_id: session.get(TasteState, user_id))

    result = service.apply_generated_search_drafts(1, profile_version=4)

    assert result.applied == ["schuhe"]
    with factory() as session:
        search = session.get(Search, "search-schuhe")
        filters = [SearchFilter.model_validate(item) for item in search.filters]
    assert search.query == "retro sneaker"
    assert [filter_.values for filter_ in filters if filter_.field == "price"] == [["20"]]
