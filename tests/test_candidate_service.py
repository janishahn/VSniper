from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy import func, select

from vsniper.core.database import Base
from vsniper.db.models import AiUsageEvent, AlertDeliveryState, Candidate, Search, TasteSampleState
from vsniper.services.candidate_service import CandidateService


def _setup(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'candidate-service.db'}", future=True)
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

    monkeypatch.setattr("vsniper.services.candidate_service.session_scope", fake_session_scope)
    return CandidateService.__new__(CandidateService), factory


def _candidate(candidate_id: str, *, created_at: datetime) -> Candidate:
    return Candidate(
        id=candidate_id,
        user_id=1,
        search_id="s1",
        clothing_item="hosen",
        title=f"Candidate {candidate_id}",
        brand="Brand",
        price_eur=20.0,
        size="M",
        url=f"https://example.test/{candidate_id}",
        image_urls=[],
        matched_filters=[],
        matched_preferences=[],
        features=[],
        normalized_listing={},
        extraction_status="completed",
        score_trace={
            "final_score": 0.8,
            "score_10": 80,
            "threshold": 0.7,
            "decision": "alert",
            "components": [],
            "summary": "Strong match.",
            "raw_response": {},
        },
        decision="alert",
        final_score=8.0,
        ai_observation={},
        grading_stage="vlm_judged",
        feedback="unknown",
        feedback_comment="",
        created_at=created_at,
    )


def test_candidate_page_defaults_to_recent_unreviewed_candidates_sorted_by_score(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    now = datetime.now(UTC)

    with factory() as session:
        session.add(Search(id="s1", user_id=1, name="Search", clothing_item="hosen", query="q", region="de", enabled=True))
        lower_score = _candidate("recent-low", created_at=now - timedelta(hours=1))
        lower_score.final_score = 0.6
        higher_score = _candidate("recent-high", created_at=now - timedelta(hours=2))
        higher_score.final_score = 0.9
        older = _candidate("older", created_at=now - timedelta(days=8))
        older.final_score = 1.0
        session.add_all([lower_score, higher_score, older])
        session.commit()

    page = service.page(1, feedback="unknown")

    assert page.total == 2
    assert [candidate.id for candidate in page.items] == ["recent-high", "recent-low"]


def test_note_only_feedback_keeps_unknown_verdict_and_upserts_sample(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    seen: dict = {}

    class PreferencesRecorder:
        def existing_feedback_image_paths(self, session, *, candidate_id):
            return []

        def precache_feedback_images(self, *, candidate_id, image_urls):
            return ["candidate-1-0.jpg"]

        def upsert_candidate_feedback_sample(
            self, session, *, candidate, verdict, comment, lesson=None, stored_image_paths=None
        ):
            seen.update(
                {
                    "feedback": candidate.feedback,
                    "verdict": verdict,
                    "comment": comment,
                    "stored_image_paths": stored_image_paths,
                }
            )
            session.add(
                TasteSampleState(
                    id="taste-note",
                    user_id=candidate.user_id,
                    kind="offer_note",
                    clothing_item=candidate.clothing_item,
                    note=comment,
                    candidate_id=candidate.id,
                    stored_image_paths=stored_image_paths or [],
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )

    service.preferences = PreferencesRecorder()
    service.settings = SimpleNamespace()

    with factory() as session:
        session.add(Search(id="s1", user_id=1, name="Search", clothing_item="hosen", query="q", region="de", enabled=True))
        candidate = _candidate("candidate-1", created_at=datetime.now(UTC))
        candidate.ai_observation = {"garment_type": "trousers"}
        session.add(candidate)
        session.commit()

    updated, snapshot = service.apply_note(1, "candidate-1", comment="great texture")

    assert updated.feedback == "unknown"
    assert updated.feedback_comment == "great texture"
    assert snapshot is not None
    assert seen == {
        "feedback": "unknown",
        "verdict": None,
        "comment": "great texture",
        "stored_image_paths": ["candidate-1-0.jpg"],
    }


def test_prune_old_records_drops_aged_rows_and_keeps_pending(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    now = datetime.now(UTC)
    old = now - timedelta(days=400)

    def _usage(called_at: datetime) -> AiUsageEvent:
        return AiUsageEvent(
            called_at=called_at, operation="judge_decision", model="m",
            input_tokens=1, output_tokens=1, cost_usd=0.1,
        )

    with factory() as session:
        session.add(Search(id="s1", user_id=1, name="Search", clothing_item="hosen", query="q", region="de", enabled=True))
        session.add(_candidate("recent", created_at=now))
        session.add(_candidate("old", created_at=old))  # pruned (aged out)
        session.add(_candidate("recent-terminal", created_at=now))
        session.add(_candidate("recent-pending", created_at=now))
        session.add(_usage(now))
        session.add(_usage(old))  # pruned (aged out)
        # One active delivery per candidate (unique index on candidate+channel).
        session.add(AlertDeliveryState(candidate_id="old", channel="telegram", status="sent", updated_at=now))
        session.add(AlertDeliveryState(candidate_id="recent-terminal", channel="telegram", status="sent", updated_at=old))
        session.add(AlertDeliveryState(candidate_id="recent-pending", channel="telegram", status="pending", updated_at=old))
        session.commit()

    counts = service.prune_old_records()

    # 'recent-terminal' (terminal + aged) and 'old' (cascade with its candidate) are removed;
    # the aged 'recent-pending' survives because pending deliveries are never pruned.
    assert counts == {"candidates": 1, "alert_deliveries": 2, "ai_usage_events": 1, "learning_snapshots": 0}

    with factory() as session:
        assert session.get(Candidate, "old") is None
        assert session.get(Candidate, "recent") is not None
        assert session.scalar(select(func.count()).select_from(AiUsageEvent)) == 1
        surviving = {d.candidate_id: d.status for d in session.scalars(select(AlertDeliveryState)).all()}
        assert surviving == {"recent-pending": "pending"}


def test_prune_drops_unrated_discards_after_two_weeks(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    now = datetime.now(UTC)
    three_weeks_ago = now - timedelta(days=21)

    def _discard(candidate_id: str, *, last_seen_at: datetime, feedback: str = "unknown") -> Candidate:
        candidate = _candidate(candidate_id, created_at=three_weeks_ago)
        candidate.decision = "discard"
        candidate.last_seen_at = last_seen_at
        candidate.feedback = feedback
        return candidate

    with factory() as session:
        session.add(Search(id="s1", user_id=1, name="Search", clothing_item="hosen", query="q", region="de", enabled=True))
        session.add(_discard("stale", last_seen_at=three_weeks_ago))
        session.add(_discard("still-listed", last_seen_at=now))
        session.add(_discard("voted", last_seen_at=three_weeks_ago, feedback="dislike"))
        session.add(_candidate("old-alert", created_at=three_weeks_ago))
        session.commit()

    assert service.prune_old_records()["candidates"] == 1

    with factory() as session:
        assert set(session.scalars(select(Candidate.id))) == {"still-listed", "voted", "old-alert"}


def test_feedback_on_another_profiles_candidate_is_rejected(monkeypatch, tmp_path) -> None:
    import pytest

    from vsniper.domain.contracts import FeedbackPayload

    service, factory = _setup(monkeypatch, tmp_path)
    with factory() as session:
        session.add(Search(id="s1", user_id=1, name="Search", clothing_item="hosen", query="q", region="de", enabled=True))
        session.add(_candidate("candidate-1", created_at=datetime.now(UTC)))
        session.commit()

    with pytest.raises(KeyError):
        service.record_feedback(2, "candidate-1", FeedbackPayload(verdict="like"))
    assert service.page(2).total == 0

    with factory() as session:
        assert session.get(Candidate, "candidate-1").feedback == "unknown"
