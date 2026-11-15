"""Embeds listing covers and scores them with a small head trained on each bucket's verdicts.

Nothing here decides an alert. Vectors and scores are stored so the local head can be compared
with the decision model on live listings."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np
from PIL import UnidentifiedImageError
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from vsniper.core.config import Settings
from vsniper.core.database import session_scope
from vsniper.db.models import Candidate, ListingEmbedding, TasteHeadState
from vsniper.domain.scoring.taste_head import MIN_LIKES, TasteHead, train_head
from vsniper.integrations.encoder.client import ENCODER_FILE, ENCODER_NAME, ImageEncoder, prepare_image
from vsniper.services.search_service import SCREEN_PASS_PROBABILITY, search_card_photo_url

logger = logging.getLogger(__name__)

_BATCH = 32
_DOWNLOAD_THREADS = 8
# A head trained against too few other listings ranks poorly; wait for this many unrated ones.
_MIN_UNRATED = 1000
# Bounds memory during training; the newest unrated listings are used.
_MAX_UNRATED = 20_000
# Picks up new listings and decision-model scores when no verdict arrives.
_RETRAIN_AFTER = timedelta(hours=24)


def _luna_target(grading_stage: str, score: float | None, screen_probability: float | None) -> float:
    """The decision model's score for a listing. One that was screened out from its search card
    was never judged in full, so it counts at half its screening probability."""
    if grading_stage != "vlm_judged" or score is None:
        return float("nan")
    if screen_probability is not None and screen_probability < SCREEN_PASS_PROBABILITY:
        return 50 * screen_probability
    return score


class LocalTasteService:
    def __init__(self, settings: Settings, encoder: ImageEncoder | None = None) -> None:
        self._encoder_path = settings.resolve_path(settings.model_dir) / ENCODER_FILE
        self._encoder = encoder

    @property
    def installed(self) -> bool:
        return self._encoder_path.exists()

    @staticmethod
    def _fetch_cover(client: httpx.Client, url: str) -> bytes | None:
        """The photo, or None when Vinted no longer serves it. Rate limits and server errors raise."""
        response = client.get(url)
        if 400 <= response.status_code < 500 and response.status_code != 429:
            return None
        response.raise_for_status()
        return response.content

    def embed_pending(self) -> int:
        """Embed and score the next covers, listings with a verdict first. Returns how many
        listings were settled; one whose cover is gone is settled without a vector."""
        with session_scope() as session:
            rows = session.execute(
                select(
                    Candidate.id,
                    Candidate.user_id,
                    Candidate.clothing_item,
                    Candidate.image_urls,
                    Candidate.normalized_listing,
                )
                .outerjoin(ListingEmbedding, ListingEmbedding.candidate_id == Candidate.id)
                .where(or_(ListingEmbedding.candidate_id.is_(None), ListingEmbedding.encoder != ENCODER_NAME))
                .order_by(Candidate.feedback == "unknown", Candidate.created_at.desc())
                .limit(_BATCH)
            ).all()
            heads = {
                (head.user_id, head.clothing_item): (head.version, TasteHead.from_bytes(head.weights))
                for head in session.scalars(select(TasteHeadState).where(TasteHeadState.encoder == ENCODER_NAME))
            }
        if not rows:
            return 0

        def load(row) -> np.ndarray | None:
            url = search_card_photo_url(
                {"raw_listing": row.normalized_listing.get("raw_listing"), "image_urls": row.image_urls}
            )
            content = self._fetch_cover(client, url) if url else None
            if content is None:
                return None
            try:
                return prepare_image(content)
            except (UnidentifiedImageError, OSError):
                return None

        with (
            httpx.Client(timeout=20, follow_redirects=True) as client,
            ThreadPoolExecutor(max_workers=_DOWNLOAD_THREADS) as pool,
        ):
            futures = [pool.submit(load, row) for row in rows]
        # A rate limit, server error or network failure leaves the listing for a later pass.
        settled = [
            (row, future.result())
            for row, future in zip(rows, futures)
            if not isinstance(future.exception(), httpx.HTTPError)
        ]

        if self._encoder is None:
            self._encoder = ImageEncoder(self._encoder_path)
        now = datetime.now(UTC)
        values = []
        for row, image in settled:
            vector = self._encoder.embed(image).astype(np.float16) if image is not None else None
            version, head = heads.get((row.user_id, row.clothing_item), (None, None))
            values.append(
                {
                    "candidate_id": row.id,
                    "encoder": ENCODER_NAME,
                    "vector": vector.tobytes() if vector is not None else None,
                    "score": float(head.score(vector[None])[0]) if head is not None and vector is not None else None,
                    "head_version": version if vector is not None else None,
                    "created_at": now,
                }
            )
        with session_scope() as session:
            # A candidate pruned since it was read must not be written, or its foreign key fails.
            existing = set(
                session.scalars(select(Candidate.id).where(Candidate.id.in_([value["candidate_id"] for value in values])))
            )
            values = [value for value in values if value["candidate_id"] in existing]
            if values:
                insert = sqlite_insert(ListingEmbedding)
                session.execute(
                    insert.on_conflict_do_update(
                        index_elements=[ListingEmbedding.candidate_id],
                        set_={
                            column: insert.excluded[column]
                            for column in ("encoder", "vector", "score", "head_version", "created_at")
                        },
                    ),
                    values,
                )
        return len(settled)

    def train_due_heads(self) -> int:
        """Train the head of every bucket whose verdicts changed or whose head is a day old.
        Returns how many were trained."""
        now = datetime.now(UTC)
        with session_scope() as session:
            buckets = session.execute(
                select(
                    Candidate.user_id,
                    Candidate.clothing_item,
                    func.count().filter(Candidate.feedback == "like"),
                    func.count().filter(Candidate.feedback == "dislike"),
                    func.count().filter(Candidate.feedback == "unknown"),
                )
                .join(ListingEmbedding, ListingEmbedding.candidate_id == Candidate.id)
                .where(ListingEmbedding.encoder == ENCODER_NAME, ListingEmbedding.vector.is_not(None))
                .group_by(Candidate.user_id, Candidate.clothing_item)
            ).all()
            heads = {(head.user_id, head.clothing_item): head for head in session.scalars(select(TasteHeadState))}

        trained = 0
        for user_id, clothing_item, likes, dislikes, unrated in buckets:
            if likes < MIN_LIKES or unrated < _MIN_UNRATED:
                continue
            head = heads.get((user_id, clothing_item))
            if (
                head is not None
                and head.encoder == ENCODER_NAME
                and (head.like_count, head.dislike_count) == (likes, dislikes)
                and now - head.trained_at.replace(tzinfo=UTC) < _RETRAIN_AFTER
            ):
                continue
            self._train(user_id, clothing_item, version=head.version + 1 if head is not None else 1)
            trained += 1
        return trained

    def _train(self, user_id: int, clothing_item: str, *, version: int) -> None:
        embedded = (
            select(
                ListingEmbedding.vector,
                Candidate.feedback,
                Candidate.grading_stage,
                Candidate.score_trace["score_10"].as_float(),
                Candidate.score_trace[("raw_response", "screen_probability")].as_float(),
            )
            .join(Candidate, Candidate.id == ListingEmbedding.candidate_id)
            .where(
                Candidate.user_id == user_id,
                Candidate.clothing_item == clothing_item,
                ListingEmbedding.encoder == ENCODER_NAME,
                ListingEmbedding.vector.is_not(None),
            )
        )
        with session_scope() as session:
            rows = [
                *session.execute(embedded.where(Candidate.feedback != "unknown")),
                *session.execute(
                    embedded.where(Candidate.feedback == "unknown")
                    .order_by(Candidate.created_at.desc())
                    .limit(_MAX_UNRATED)
                ),
            ]
        vectors = np.stack([np.frombuffer(vector, dtype=np.float16) for vector, *_ in rows])
        verdict = np.array([{"like": 1, "dislike": 0}.get(feedback, -1) for _, feedback, *_ in rows])
        luna_target = np.array([_luna_target(stage, score, screen) for _, _, stage, score, screen in rows])
        head = train_head(vectors, verdict, luna_target, np.random.default_rng(version))

        values = {
            "user_id": user_id,
            "clothing_item": clothing_item,
            "encoder": ENCODER_NAME,
            "version": version,
            "weights": head.to_bytes(),
            "like_count": int((verdict == 1).sum()),
            "dislike_count": int((verdict == 0).sum()),
            "trained_at": datetime.now(UTC),
        }
        with session_scope() as session:
            insert = sqlite_insert(TasteHeadState).values(**values)
            session.execute(
                insert.on_conflict_do_update(
                    index_elements=[TasteHeadState.user_id, TasteHeadState.clothing_item],
                    set_={key: insert.excluded[key] for key in values if key not in ("user_id", "clothing_item")},
                )
            )
        logger.info(
            "trained taste head v%d for profile %d bucket %s on %d likes, %d dislikes, %d listings",
            version, user_id, clothing_item, values["like_count"], values["dislike_count"], len(rows),
        )
