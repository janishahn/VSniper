"""Tests for SearchService._judge_candidates — the VLM judging pipeline.

Covers the riskiest outcomes: all candidates being judged, reuse of candidates already judged
under the current profile version, explanations for alerts only, failed decisions, and
image-download failure.
"""
from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vsniper.core.database import Base
from vsniper.db.models import Candidate
from vsniper.domain.contracts import (
    CandidateJudgment,
    ScoreTrace,
    SearchRecord,
    TasteProfile,
)
from vsniper.domain.scoring.service import build_judgment_trace
from vsniper.integrations.vinted.client import VintedSearchError
from vsniper.integrations.vinted.item_page import ItemPage
from vsniper.integrations.openai.client import (
    CandidateImageInput,
    OpenAIIntegrationError,
)
from vsniper.services.search_service import SearchService


@pytest.fixture(autouse=True)
def memo_db(monkeypatch):
    """In-memory DB so _memoized_judgments can query the candidates table.

    The table is empty by default, so every candidate is judged; the memoization tests seed a
    previously-judged row through the returned factory.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
    return factory


def _seed_judged_candidate(factory, candidate_id: str, score_trace: ScoreTrace, *, grading_stage: str = "vlm_judged") -> None:
    session = factory()
    session.add(
        Candidate(
            id=candidate_id,
            user_id=1,
            search_id="search-001",
            clothing_item="hosen",
            title="Previously seen",
            brand="Brand",
            price_eur=20.0,
            size="M",
            url=f"https://vinted.de/{candidate_id}",
            image_urls=[],
            matched_filters=[],
            matched_preferences=[],
            features=[],
            normalized_listing={},
            decision=score_trace.decision,
            final_score=score_trace.final_score,
            grading_stage=grading_stage,
            score_trace=score_trace.model_dump(mode="json"),
        )
    )
    session.commit()
    session.close()


def _make_service() -> SearchService:
    service = SearchService.__new__(SearchService)
    service.settings = MagicMock()
    service.settings.openai_decision_model = "gpt-6-luna"
    service.vinted_client = MagicMock()
    # Without a page the listing keeps its catalog data, as when Vinted refuses the item page.
    service.vinted_client.fetch_item_page.side_effect = VintedSearchError("no page", retryable=True)
    service.taste_client = MagicMock()
    service.preferences = MagicMock()
    service.preferences.latest_labeled_anchors.return_value = ([], [])
    service.telegram = MagicMock()
    return service


def _call_judge(service: SearchService, raw_candidates, search, taste, **kwargs):
    """Returns just (traces, stages); callers here don't need the persisted counts."""
    kwargs.setdefault("mode", "preview")
    traces, stages, _alert_count, _queued = service._judge_candidates(raw_candidates, search, taste, **kwargs)
    return traces, stages


def _judgment(score: int, explanation: str = "") -> CandidateJudgment:
    return CandidateJudgment(score=score, explanation=explanation, labels=[], concerns=[])


def _search(search_id: str = "search-001") -> SearchRecord:
    return SearchRecord(id=search_id, user_id=1, name="Test", clothing_item="hosen", query="vintage", region="de")


def _jpeg(color: str = "red") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="JPEG")
    return buf.getvalue()


def _raw(external_id: str, title: str = "A listing", brand: str = "Brand") -> dict:
    return {
        "id": external_id,
        "external_item_id": external_id,
        "title": title,
        "brand": brand,
        "price_eur": 20.0,
        "size": "M",
        "url": f"https://vinted.de/{external_id}",
        "image_urls": [f"https://images.vinted.de/{external_id}.jpg"],
        "description": "",
        "features": [],
        "raw_listing": {},
    }


def test_all_candidates_are_judged() -> None:
    service = _make_service()
    search = _search()
    taste = TasteProfile(summary="", taste_prompt="likes vintage")
    candidates = [_raw(f"item-{index}") for index in range(5)]

    jpeg = _jpeg()

    def _download(*, candidate_id: str, image_urls: list[str]) -> CandidateImageInput:
        return CandidateImageInput(candidate_id=candidate_id, image_bytes=jpeg)

    service.taste_client.judge_candidate.return_value = _judgment(80)
    with patch.object(service, "_download_candidate_image", side_effect=_download):
        traces, stages = _call_judge(service, candidates, search, taste)

    judged = [c for c, s in stages.items() if s == "vlm_judged"]
    assert len(judged) == 5
    assert service.taste_client.judge_candidate.call_count == 5
    assert len([trace for trace in traces.values() if trace.score_10 == 80]) == 5


def test_item_page_details_reach_the_judge_and_a_failed_page_keeps_catalog_data() -> None:
    service = _make_service()
    page = ItemPage(
        title="Leinenhose",
        brand="Brand",
        size="M",
        condition="Sehr gut",
        color="Beige",
        material="Leinen",
        description="Weite Hose aus Leinen",
        price_eur=20.0,
        image_urls=["https://images1.vinted.net/a.webp", "https://images1.vinted.net/b.webp"],
    )

    def _fetch(url: str) -> ItemPage:
        if url.endswith("item-2"):
            raise VintedSearchError("blocked", retryable=True)
        return page

    service.vinted_client.fetch_item_page.side_effect = _fetch
    downloaded: dict[str, list[str]] = {}
    jpeg = _jpeg()

    def _download(*, candidate_id: str, image_urls: list[str]) -> CandidateImageInput:
        downloaded[candidate_id] = image_urls
        return CandidateImageInput(candidate_id=candidate_id, image_bytes=jpeg)

    service.taste_client.judge_candidate.return_value = _judgment(10)
    with patch.object(service, "_download_candidate_image", side_effect=_download):
        _call_judge(service, [_raw("item-1"), _raw("item-2")], _search(), TasteProfile(summary="", taste_prompt=""))

    judged = {call.kwargs["candidate"].candidate_id: call.kwargs["candidate"] for call in service.taste_client.judge_candidate.call_args_list}
    assert judged["search-001:item-1"].description == "Weite Hose aus Leinen"
    assert judged["search-001:item-1"].condition == "Sehr gut"
    assert downloaded["search-001:item-1"] == page.image_urls
    assert judged["search-001:item-2"].description == ""
    assert downloaded["search-001:item-2"] == ["https://images.vinted.de/item-2.jpg"]


def test_already_judged_candidate_is_reused_not_rejudged(memo_db) -> None:
    service = _make_service()
    search = _search()
    taste = TasteProfile(summary="", taste_prompt="likes vintage")  # version defaults to 1

    # item-1 was already VLM-judged under profile version 1; item-2 has never been seen.
    prior = build_judgment_trace(
        judgment=_judgment(80, "seen before"),
        taste_profile=taste,
        model="gpt-6-luna",
    )
    _seed_judged_candidate(memo_db, "search-001:item-1", prior)

    jpeg = _jpeg()

    def _download(*, candidate_id: str, image_urls: list[str]) -> CandidateImageInput:
        return CandidateImageInput(candidate_id=candidate_id, image_bytes=jpeg)

    service.taste_client.judge_candidate.return_value = _judgment(30)
    new_judged_ids: set[str] = set()
    with patch.object(service, "_download_candidate_image", side_effect=_download) as download:
        traces, stages = _call_judge(
            service, [_raw("item-1"), _raw("item-2")], search, taste, new_judged_ids=new_judged_ids
        )

    # item-1 reused from its stored trace; only the unseen item-2 reached the VLM.
    assert stages["search-001:item-1"] == "vlm_judged"
    assert traces["search-001:item-1"].score_10 == 80
    assert traces["search-001:item-1"].explanation == "seen before"
    assert stages["search-001:item-2"] == "vlm_judged"
    assert traces["search-001:item-2"].score_10 == 30
    assert new_judged_ids == {"search-001:item-2"}
    download.assert_called_once()
    assert download.call_args.kwargs["candidate_id"] == "search-001:item-2"


def test_stale_version_and_failed_candidates_are_rejudged(memo_db) -> None:
    service = _make_service()
    search = _search()
    taste = TasteProfile(summary="", taste_prompt="likes vintage", version=2)

    # item-1 was judged under an older profile version; item-2's last attempt failed.
    stale = build_judgment_trace(
        judgment=_judgment(80, "old profile"),
        taste_profile=TasteProfile(summary="", taste_prompt="x", version=1),
        model="gpt-6-luna",
    )
    _seed_judged_candidate(memo_db, "search-001:item-1", stale)
    _seed_judged_candidate(memo_db, "search-001:item-2", stale, grading_stage="failed")

    jpeg = _jpeg()

    def _download(*, candidate_id: str, image_urls: list[str]) -> CandidateImageInput:
        return CandidateImageInput(candidate_id=candidate_id, image_bytes=jpeg)

    service.taste_client.judge_candidate.return_value = _judgment(50)
    with patch.object(service, "_download_candidate_image", side_effect=_download):
        traces, stages = _call_judge(service, [_raw("item-1"), _raw("item-2")], search, taste)

    # Both were re-judged: stale-version and failed rows are never reused.
    assert traces["search-001:item-1"].score_10 == 50
    assert traces["search-001:item-2"].score_10 == 50


def test_only_alerts_get_an_explanation_and_failed_decisions_are_marked_failed() -> None:
    service = _make_service()
    search = _search()  # default alert threshold 95
    taste = TasteProfile(summary="", taste_prompt="likes vintage")
    jpeg = _jpeg()

    def _judge(*, candidate: CandidateImageInput, **kwargs) -> CandidateJudgment:
        if candidate.candidate_id.endswith("broken"):
            raise OpenAIIntegrationError("decision refused")
        return _judgment(97 if candidate.candidate_id.endswith("match") else 40)

    def _download(*, candidate_id: str, image_urls: list[str]) -> CandidateImageInput:
        return CandidateImageInput(candidate_id=candidate_id, image_bytes=jpeg)

    service.taste_client.judge_candidate.side_effect = _judge
    service.taste_client.explain_judgment.return_value = "Washed black wide-leg cargos."
    with patch.object(service, "_download_candidate_image", side_effect=_download):
        traces, stages = _call_judge(service, [_raw("match"), _raw("miss"), _raw("broken")], search, taste)

    assert traces["search-001:match"].decision == "alert"
    assert traces["search-001:match"].explanation == "Washed black wide-leg cargos."
    assert traces["search-001:miss"].explanation == ""
    assert service.taste_client.explain_judgment.call_count == 1
    assert stages["search-001:broken"] == "failed"
    assert traces["search-001:broken"].explanation == "decision refused"


def _download_service(tmp_path: Path) -> SearchService:
    service = _make_service()
    service.settings.resolve_path.side_effect = lambda p: tmp_path
    service.settings.cache_dir = "cache"
    return service


def test_download_rejects_html_error_page_with_image_content_type(tmp_path: Path) -> None:
    # Vinted occasionally serves an HTML error body with an image/* content-type. Caching it
    # would poison every future scan, so it must be rejected and never written to disk.
    service = _download_service(tmp_path)
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.headers = {"content-type": "image/jpeg"}
    response.content = b"<html><body>404 not found</body></html>"

    client_cm = MagicMock()
    client_cm.__enter__.return_value.get.return_value = response
    with patch("vsniper.services.search_service.httpx.Client", return_value=client_cm):
        result = service._download_candidate_image(candidate_id="s:item-1", image_urls=["http://x/1.jpg"])

    assert result is None
    assert not service._candidate_cache_path("s:item-1").exists()


def test_cancel_check_stops_dispatching_further_batches_but_keeps_partial_work(memo_db, monkeypatch) -> None:
    """Cancellation is checked between judge groups (one candidate per group here), so a cancel
    mid-run stops new work but must not discard what already judged and persisted."""
    from vsniper.db.models import Search

    session = memo_db()
    session.add(Search(id="search-001", user_id=1, name="Test", clothing_item="hosen", query="q", region="de", run_status="running"))
    session.commit()
    session.close()

    monkeypatch.setattr("vsniper.services.search_service.JUDGE_CONCURRENCY", 1)
    service = _make_service()
    service.telegram = MagicMock()
    service.telegram.queue_delivery.return_value = False
    search = _search()
    taste = TasteProfile(summary="", taste_prompt="likes vintage")
    candidates = [_raw(f"item-{index}") for index in range(4)]
    jpeg = _jpeg()
    judged_calls: list[str] = []

    def _judge(*, candidate: CandidateImageInput, **kwargs) -> CandidateJudgment:
        judged_calls.append(candidate.candidate_id)
        return _judgment(80)

    def _download(*, candidate_id: str, image_urls: list[str]) -> CandidateImageInput:
        return CandidateImageInput(candidate_id=candidate_id, image_bytes=jpeg)

    service.taste_client.judge_candidate.side_effect = _judge

    cancel_after = 1

    def cancel_check() -> bool:
        return len(judged_calls) >= cancel_after

    with patch.object(service, "_download_candidate_image", side_effect=_download):
        traces, stages, alert_count, queued = service._judge_candidates(
            candidates,
            search,
            taste,
            mode="live",
            cancel_check=cancel_check,
        )

    # Only the first candidate was judged before cancellation stopped further dispatch.
    assert len(judged_calls) == 1
    judged_ids = {cid for cid, stage in stages.items() if stage == "vlm_judged"}
    assert judged_ids == set(judged_calls)

    # What was judged before the cancel must already be persisted, not discarded.
    session = memo_db()
    persisted = session.query(Candidate).filter(Candidate.id.in_(judged_ids)).all()
    assert {c.id for c in persisted} == judged_ids
    session.close()
