"""Tests for SearchService.claim_for_run — the DB-backed run lease.

The key behavior under test (#4): a run that failed before persisting is keyed on
run_status, so it becomes reclaimable after one interval instead of being locked out
until the 3x stale cutoff, while a run still "running" is only recoverable once stale.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker

from vsniper.core.database import Base
from vsniper.db.models import AppSettingsState, Bucket, Candidate, Search, SearchRun, User
from vsniper.domain.contracts import ScoreTrace, SearchRecord, SearchUpdate, TasteProfile
from vsniper.services.search_service import (
    SearchClothingItemImmutable,
    SearchRunAlreadyClaimed,
    SearchService,
)

INTERVAL = timedelta(seconds=60)


def _setup(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'claim.db'}", future=True)

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
    monkeypatch.setattr("vsniper.services.operations_service.session_scope", fake_session_scope)

    with fake_session_scope() as session:
        session.add(User(id=1, name="Profile 1"))
        session.flush()
        session.add(Bucket(user_id=1, key="hosen", label="Hosen", catalog_ids=[9]))
        session.add(Search(id="s1", user_id=1, name="S", clothing_item="hosen", query="q", region="de", run_status="idle"))

    service = SearchService.__new__(SearchService)
    return service, factory


def _set(factory, **values):
    session = factory()
    search = session.get(Search, "s1")
    for key, value in values.items():
        setattr(search, key, value)
    session.commit()
    session.close()


def _row(factory) -> Search:
    session = factory()
    search = session.get(Search, "s1")
    session.expunge(search)
    session.close()
    return search


def _raw_candidate(external_id: str = "item-1") -> dict:
    return {
        "id": external_id,
        "external_item_id": external_id,
        "title": "Cargos",
        "brand": "Brand",
        "price_eur": 25.0,
        "size": "M",
        "url": f"https://www.vinted.de/items/{external_id}",
        "image_urls": [],
        "description": "",
        "features": [],
        "raw_listing": {},
    }


def _latest_run(factory) -> SearchRun:
    session = factory()
    try:
        run = session.query(SearchRun).order_by(SearchRun.id.desc()).first()
        assert run is not None
        session.expunge(run)
        return run
    finally:
        session.close()


def test_fresh_search_is_claimed_and_marked_running(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)

    assert service.claim_for_run("s1", INTERVAL) is True
    row = _row(factory)
    assert row.run_status == "running"
    assert row.last_claimed_at is not None


def test_running_within_interval_is_not_reclaimed(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    service.claim_for_run("s1", INTERVAL)  # now running, claimed just now

    assert service.claim_for_run("s1", INTERVAL) is False


def test_stale_running_run_is_recovered(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    # A worker crashed mid-run: "running" and claimed 200s ago (> 3x interval).
    _set(factory, run_status="running", last_claimed_at=datetime.now(UTC) - timedelta(seconds=200))

    assert service.claim_for_run("s1", INTERVAL) is True


def test_stale_reclaim_closes_orphaned_search_run_as_failed(monkeypatch, tmp_path) -> None:
    """A crashed process leaves both Search.run_status and its SearchRun row stuck "running".

    Without cleanup, that SearchRun row shows up forever in the "Recent scan runs" history as
    "running" even though it will never be touched again — this is the root cause of searches
    appearing to run more than once concurrently. Reclaiming the stale search must close it out.
    """
    service, factory = _setup(monkeypatch, tmp_path)
    _set(factory, run_status="running", last_claimed_at=datetime.now(UTC) - timedelta(seconds=200))
    with _session_scope_of(factory) as session:
        session.add(
            SearchRun(search_id="s1", mode="live", trigger="worker", status="running", started_at=datetime.now(UTC))
        )

    assert service.claim_for_run("s1", INTERVAL) is True

    with _session_scope_of(factory) as session:
        orphaned = session.query(SearchRun).filter_by(search_id="s1").one()
        assert orphaned.status == "failed"
        assert orphaned.finished_at is not None
        assert "Orphaned" in orphaned.error


def test_restart_releases_interrupted_claims_so_a_manual_run_can_start(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    _set(factory, run_status="running", last_claimed_at=datetime.now(UTC))
    with _session_scope_of(factory) as session:
        session.add(SearchRun(search_id="s1", mode="live", trigger="worker", status="running", started_at=datetime.now(UTC)))

    service.release_interrupted_runs()

    assert _row(factory).run_status == "failed"
    assert _latest_run(factory).status == "failed"
    assert service.claim_for_run("s1", timedelta(0)) is True


def test_api_live_run_rejects_active_claim(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    _set(factory, run_status="running", last_claimed_at=datetime.now(UTC))

    with pytest.raises(SearchRunAlreadyClaimed):
        service.run_live(1, "s1")


def test_update_rejects_changing_clothing_item(monkeypatch, tmp_path) -> None:
    service, _ = _setup(monkeypatch, tmp_path)

    with pytest.raises(SearchClothingItemImmutable):
        service.update(
            1,
            "s1",
            SearchUpdate(
                name="Warm tops",
                clothing_item="obenrum_warm",
                query="graphic tee",
                region="de",
                enabled=True,
                filters=[],
            ),
        )


def _persist_minimal(service, factory, *, mode):
    search_record = SearchRecord(
        id="s1", user_id=1, name="S", enabled=True, clothing_item="hosen", query="q", region="de",
        filters=[], last_run_at=None, last_found_count=0,
    )
    raw = {
        "id": "item-1", "external_item_id": "item-1", "title": "Cargos", "brand": "Brand",
        "price_eur": 25.0, "size": "M", "url": "https://www.vinted.de/items/item-1",
        "image_urls": [], "description": "", "features": [], "raw_listing": {},
    }
    trace = ScoreTrace(final_score=8.0, score_10=8, threshold=7.0, decision="alert", components=[], summary="alert")
    alert_count, queued_alert_deliveries, judged_count = service._persist_candidate_batch(
        raw_candidates=[raw],
        search_record=search_record,
        taste_profile=TasteProfile(summary="", taste_prompt="", transparency_labels=[]),
        score_traces={"s1:item-1": trace},
        stages={"s1:item-1": "vlm_judged"},
        mode=mode,
    )
    session = factory()
    try:
        search = session.get(Search, "s1")
        service._persist_run(
            session=session, search=search, search_record=search_record, mode=mode,
            fetched_count=1, alert_count=alert_count, judged_count=judged_count,
            queued_alert_deliveries=queued_alert_deliveries,
        )
        session.commit()
    finally:
        session.close()


# --- #3: a failed run only marks run_status="failed" for live runs (which hold the claim) ---


def _wire_run(service, factory, *, run_search):
    """Stub the Phase-1 dependencies _run reads so we can drive it end-to-end with a fake
    Vinted client. run_search is the callable used as VintedClient.run_search."""
    with _session_scope_of(factory) as session:
        session.add(AppSettingsState(id=1))
    service.settings = type("Settings", (), {"openai_decision_model": "gpt-6-luna"})()

    profile = TasteProfile(summary="", taste_prompt="", transparency_labels=[])
    service.preferences = type(
        "Prefs",
        (),
        {
            "active_taste_profile": staticmethod(lambda *a, **k: profile),
            "get_taste_state": staticmethod(lambda *a, **k: type("S", (), {"manual_note": ""})()),
        },
    )()
    service.vinted_client = type(
        "Vinted",
        (),
        {
            "run_search": staticmethod(run_search),
        },
    )()
    service.telegram = type("Telegram", (), {"queue_delivery": staticmethod(lambda *a, **k: True)})()


@contextmanager
def _session_scope_of(factory):
    session = factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


def _boom(*args, **kwargs):
    raise RuntimeError("vinted exploded")


def test_failed_preview_does_not_touch_a_concurrent_live_claim(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    _wire_run(service, factory, run_search=_boom)
    # A worker is mid-scan, holding the claim.
    _set(factory, run_status="running", last_claimed_at=datetime.now(UTC))

    with pytest.raises(RuntimeError):
        service._run("s1", mode="preview")

    # The preview failed but must leave the worker's claim untouched, or claim_for_run would
    # let a second worker reclaim and re-scan mid-run.
    assert _row(factory).run_status == "running"


def test_scan_searches_the_bucket_sizes(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    searched: list[SearchRecord] = []
    _wire_run(service, factory, run_search=lambda search: searched.append(search) or [])
    with _session_scope_of(factory) as session:
        session.get(Bucket, (1, "hosen")).sizes = [{"id": 4, "title": "M"}, {"id": 5, "title": "L"}]
    service._judge_candidates = lambda *args, **kwargs: ({}, {}, 0, 0)

    service._run("s1", mode="preview")

    filters = {item.field: item.values for item in searched[0].filters}
    assert filters == {"category": ["9"], "size": ["4", "5"]}


# --- #3 (related): re-persisting a candidate upserts rather than IntegrityError-ing ---


def test_persist_run_upserts_and_preserves_feedback(monkeypatch, tmp_path) -> None:
    service, factory = _setup(monkeypatch, tmp_path)
    service.telegram = type("Telegram", (), {"queue_delivery": staticmethod(lambda *a, **k: True)})()

    _persist_minimal(service, factory, mode="live")

    # Simulate the user having voted on this candidate, plus an existing first_seen_at.
    session = factory()
    candidate = session.get(Candidate, "s1:item-1")
    candidate.feedback = "like"
    original_first_seen = candidate.first_seen_at
    session.commit()
    session.close()

    # A second persist of the same candidate (e.g. a concurrent run) must update, not raise.
    _persist_minimal(service, factory, mode="live")

    session = factory()
    rows = session.query(Candidate).filter(Candidate.id == "s1:item-1").all()
    assert len(rows) == 1
    # feedback and first_seen_at are insert-only / coalesced — the user's vote and the earliest
    # sighting survive the re-persist.
    assert rows[0].feedback == "like"
    assert rows[0].first_seen_at == original_first_seen
    session.close()


# --- cancel: request_cancel() and a mid-scan cancellation keeps partial results ---


def test_cancelled_run_persists_partial_results_and_closes_run(monkeypatch, tmp_path) -> None:
    """A user cancels mid-scan: whatever was already judged must be saved as a Candidate row,
    the SearchRun history entry must be closed as "cancelled" (not left "running" forever, which
    is what caused searches to look like they were running more than once), and the search must
    become claimable again immediately (run_status back to "idle", cancel flag cleared)."""
    service, factory = _setup(monkeypatch, tmp_path)
    _wire_run(service, factory, run_search=lambda *args, **kwargs: [_raw_candidate()])
    _set(factory, run_status="running", last_claimed_at=datetime.now(UTC))

    def fake_judge(raw_candidates, search_record, taste_profile, mode, manual_note="", on_usage=None, new_judged_ids=None, cancel_check=None, **kwargs):
        trace = ScoreTrace(final_score=8.0, score_10=8, threshold=7.0, decision="alert", summary="alert", model="gpt-5")
        alert_count, queued, _judged = service._persist_candidate_batch(
            raw_candidates=raw_candidates,
            search_record=search_record,
            taste_profile=taste_profile,
            score_traces={"s1:item-1": trace},
            stages={"s1:item-1": "vlm_judged"},
            mode=mode,
        )
        if new_judged_ids is not None:
            new_judged_ids.add("s1:item-1")
        # Simulate the user clicking "Cancel" in the dashboard while this batch was in flight.
        with _session_scope_of(factory) as session:
            session.execute(
                update(Search).where(Search.id == "s1").values(cancel_requested_at=datetime.now(UTC))
            )
        assert cancel_check() is True
        return {"s1:item-1": trace}, {"s1:item-1": "vlm_judged"}, alert_count, queued

    service._judge_candidates = fake_judge

    result = service._run("s1", mode="live")

    assert "cancelled" in result.summary
    assert result.alert_candidates == 1

    run = _latest_run(factory)
    assert run.status == "cancelled"
    assert run.judged_count == 1

    row = _row(factory)
    assert row.run_status == "idle"
    assert row.cancel_requested_at is None

    session = factory()
    candidate = session.get(Candidate, "s1:item-1")
    assert candidate is not None
    assert candidate.decision == "alert"
    session.close()
