from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

import httpx
import numpy as np
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vsniper.core.database import Base
from vsniper.db.models import Candidate, ListingEmbedding, Search, TasteHeadState
from vsniper.services import local_taste_service
from vsniper.services.local_taste_service import LocalTasteService

RED, BLUE = (220, 30, 30), (30, 30, 220)


class ColorEncoder:
    """Stands in for the image encoder: a cover's vector is its mean colour."""

    def embed(self, image: np.ndarray) -> np.ndarray:
        return image.mean(axis=(1, 2))


def _png(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _setup(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'local-taste.db'}", future=True)
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

    monkeypatch.setattr(local_taste_service, "session_scope", fake_session_scope)
    settings = SimpleNamespace(model_dir=tmp_path, resolve_path=lambda path: path)
    service = LocalTasteService(settings, encoder=ColorEncoder())  # ty: ignore[invalid-argument-type]
    covers: dict[str, bytes | None | Exception] = {}

    def fetch_cover(_client, url: str) -> bytes | None:
        cover = covers[url]
        if isinstance(cover, Exception):
            raise cover
        return cover

    service._fetch_cover = fetch_cover  # ty: ignore[invalid-assignment]
    with factory() as session:
        session.add(Search(id="s1", user_id=1, name="Search", clothing_item="hosen", query="q", region="de", enabled=True))
        session.commit()
    return service, factory, covers


def _add(factory, covers, candidate_id: str, cover, *, feedback: str = "unknown", age_minutes: int = 0) -> None:
    covers[f"https://img.test/{candidate_id}"] = _png(cover) if isinstance(cover, tuple) else cover
    with factory() as session:
        session.add(
            Candidate(
                id=candidate_id,
                user_id=1,
                search_id="s1",
                clothing_item="hosen",
                title=candidate_id,
                brand="Brand",
                price_eur=20.0,
                size="M",
                url=f"https://example.test/{candidate_id}",
                image_urls=[f"https://img.test/{candidate_id}"],
                normalized_listing={},
                # The decision model's score; here simply how red the cover is.
                score_trace={"score_10": 1 + cover[0] // 3 if isinstance(cover, tuple) else 50},
                decision="review",
                final_score=0.6,
                grading_stage="vlm_judged",
                feedback=feedback,
                created_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
            )
        )
        session.commit()


def _embedding(factory, candidate_id: str) -> ListingEmbedding | None:
    with factory() as session:
        return session.get(ListingEmbedding, candidate_id)


def test_embed_pending_settles_each_listing_once_and_retries_network_failures(monkeypatch, tmp_path) -> None:
    service, factory, covers = _setup(monkeypatch, tmp_path)
    _add(factory, covers, "fine", RED)
    _add(factory, covers, "gone", None)
    _add(factory, covers, "not-an-image", b"<html>")
    _add(factory, covers, "network-down", httpx.ConnectError("down"))

    assert service.embed_pending() == 3

    fine = _embedding(factory, "fine")
    assert fine is not None and fine.vector is not None and fine.score is None
    assert np.frombuffer(fine.vector, dtype=np.float16).shape == (3,)
    for candidate_id in ("gone", "not-an-image"):
        settled = _embedding(factory, candidate_id)
        assert settled is not None and settled.vector is None
    assert _embedding(factory, "network-down") is None

    covers["https://img.test/network-down"] = _png(BLUE)
    assert service.embed_pending() == 1
    assert service.embed_pending() == 0


def test_listings_with_a_verdict_are_embedded_first(monkeypatch, tmp_path) -> None:
    service, factory, covers = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(local_taste_service, "_BATCH", 1)
    _add(factory, covers, "newer-unrated", BLUE)
    _add(factory, covers, "older-liked", RED, feedback="like", age_minutes=60)

    service.embed_pending()

    assert _embedding(factory, "older-liked") is not None
    assert _embedding(factory, "newer-unrated") is None


def test_head_trains_on_verdicts_and_scores_later_listings(monkeypatch, tmp_path) -> None:
    service, factory, covers = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(local_taste_service, "_MIN_UNRATED", 40)
    monkeypatch.setattr(local_taste_service, "_BATCH", 100)
    rng = np.random.default_rng(0)
    for index in range(5):
        _add(factory, covers, f"liked-{index}", (200 + index * 5, 30, 30), feedback="like")
    for index in range(60):
        _add(factory, covers, f"unrated-{index}", tuple(int(value) for value in rng.integers(0, 256, 3)))
    service.embed_pending()

    assert service.train_due_heads() == 1
    assert service.train_due_heads() == 0

    _add(factory, covers, "new-red", RED)
    _add(factory, covers, "new-blue", BLUE)
    service.embed_pending()
    red, blue = _embedding(factory, "new-red"), _embedding(factory, "new-blue")
    assert red is not None and blue is not None
    assert red.head_version == blue.head_version == 1
    assert red.score is not None and blue.score is not None and red.score > blue.score

    with factory() as session:
        session.get(Candidate, "new-blue").feedback = "dislike"
        session.commit()
    assert service.train_due_heads() == 1
    with factory() as session:
        head = session.scalars(select(TasteHeadState)).one()
    assert (head.version, head.like_count, head.dislike_count) == (2, 5, 1)


def test_no_head_is_trained_below_the_minimum(monkeypatch, tmp_path) -> None:
    service, factory, covers = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(local_taste_service, "_MIN_UNRATED", 2)
    for index in range(2):
        _add(factory, covers, f"liked-{index}", RED, feedback="like")
    for index in range(5):
        _add(factory, covers, f"unrated-{index}", BLUE)
    service.embed_pending()

    assert service.train_due_heads() == 0
