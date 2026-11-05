from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from vsniper.core.database import Base
from vsniper.db.models import (
    AppSettingsState,
    Candidate,
    LearningSnapshotState,
    Search,
    TasteSampleState,
    TasteState,
)
from vsniper.domain.contracts import TasteProfile
from vsniper.services.taste_service import TasteService


def _setup(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'taste-service.db'}", future=True)
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

    monkeypatch.setattr("vsniper.services.taste_service.session_scope", fake_session_scope)
    settings = SimpleNamespace(
        upload_dir=tmp_path / "uploads",
        cache_dir=tmp_path / "cache",
        feedback_asset_dir=tmp_path / "feedback-assets",
        resolve_path=lambda value: value,
        openai_vision_model="gpt-6.1-terra",
    )
    service = TasteService.__new__(TasteService)
    service.settings = settings
    service.taste_client = SimpleNamespace()
    service._test_factory = factory
    return service


def test_candidate_feedback_sample_uses_durable_images(monkeypatch, tmp_path) -> None:
    service = _setup(monkeypatch, tmp_path)
    factory = service._test_factory
    now = datetime.now(UTC)

    with factory() as session:
        session.add(Search(id="s1", name="Search", clothing_item="hosen", query="q", region="de", enabled=True))
        candidate = Candidate(
            id="candidate-1",
            search_id="s1",
            clothing_item="hosen",
            title="Wide cargos",
            brand="Brand",
            price_eur=20.0,
            size="M",
            url="https://www.vinted.de/items/1",
            image_urls=["https://images1.vinted.net/item.jpg"],
            matched_filters=[],
            matched_preferences=[],
            features=[],
            normalized_listing={"description": "Wide cotton trousers"},
            extraction_status="completed",
            score_trace={"final_score": 0.8},
            decision="alert",
            final_score=8.0,
            ai_observation={},
            grading_stage="vlm_judged",
            feedback="unknown",
            feedback_comment="good fabric",
            created_at=now,
        )
        session.add(candidate)
        sample = service.upsert_candidate_feedback_sample(
            session,
            candidate=candidate,
            verdict=None,
            comment="good fabric",
            stored_image_paths=["candidate-1-0.jpg"],
        )
        session.commit()

    assert sample.kind == "offer_note"
    assert sample.stored_image_paths == ["candidate-1-0.jpg"]
    assert sample.cached_image_paths == []
    assert sample.description == "Wide cotton trousers"
    assert sample.normalized_listing["score_trace"] == {"final_score": 0.8}

    with factory() as session:
        stored = session.query(TasteSampleState).filter_by(candidate_id="candidate-1").one()
        assert stored.kind == "offer_note"
        assert stored.stored_image_paths == ["candidate-1-0.jpg"]


def test_cancel_recompute_unblocks_immediate_retry(monkeypatch, tmp_path) -> None:
    service = _setup(monkeypatch, tmp_path)
    factory = service._test_factory
    now = datetime.now(UTC)

    with factory() as session:
        session.add(
            TasteState(
                id=1,
                manual_note="",
                taste_profile={},
                recompute_status="running",
                recompute_job_id="job-stuck",
                recompute_started_at=now,
            )
        )
        session.commit()

    snapshot = service.cancel_recompute()
    assert snapshot.recompute_state.status == "cancelled"
    assert snapshot.recompute_state.job_id is None

    with factory() as session:
        state = session.get(TasteState, 1)
        assert state.recompute_status == "cancelled"
        assert state.recompute_job_id is None
        assert state.recompute_started_at is None
        assert state.recompute_error == "Cancelled by user."

    # A cancelled claim must not force the caller to wait out the 2h staleness window.
    claim = service.claim_recompute(source="api")
    assert claim.claimed is True


def _seed_recompute_prereqs(factory, *, job_id: str, taste_profile: dict, now: datetime) -> None:
    with factory() as session:
        session.add(AppSettingsState(id=1, vinted_region="de"))
        session.add(
            TasteState(
                id=1,
                manual_note="",
                taste_profile=taste_profile,
                recompute_status="running",
                recompute_job_id=job_id,
                recompute_started_at=now,
            )
        )
        session.commit()


def test_superseded_recompute_job_does_not_clobber_newer_result(monkeypatch, tmp_path) -> None:
    service = _setup(monkeypatch, tmp_path)
    factory = service._test_factory
    now = datetime.now(UTC)
    original_profile = TasteProfile(summary="original", taste_prompt="original").model_dump(mode="json")
    _seed_recompute_prereqs(factory, job_id="job-A", taste_profile=original_profile, now=now)
    service.taste_client.build_taste_profile = lambda **kwargs: TasteProfile(summary="s", taste_prompt="p")

    # Simulate the orphaned call's claim being superseded (e.g. by a cancel) while it was
    # still in flight, then the orphaned call finally returning.
    service.cancel_recompute()
    service._run_recompute_unlocked("job-A")

    with factory() as session:
        state = session.get(TasteState, 1)
        assert state.taste_profile["summary"] == "original"
        assert session.query(LearningSnapshotState).count() == 0
