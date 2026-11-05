from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vsniper.core.config import get_settings
from vsniper.core.database import dispose_engine, init_database
from vsniper.services import storage_service as svc


@pytest.fixture()
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage = tmp_path / "storage"
    db_path = storage / "sqlite" / "vsniper.db"
    uploads = storage / "uploads"
    cache = storage / "cache"
    feedback_assets = storage / "feedback-assets"
    monkeypatch.setenv("SQLITE_PATH", str(db_path))
    monkeypatch.setenv("UPLOAD_DIR", str(uploads))
    monkeypatch.setenv("CACHE_DIR", str(cache))
    monkeypatch.setenv("FEEDBACK_ASSET_DIR", str(feedback_assets))
    get_settings.cache_clear()
    dispose_engine()
    init_database()

    yield SimpleNamespace(
        storage=storage,
        db_path=db_path,
        uploads=uploads,
        cache=cache,
        feedback_assets=feedback_assets,
    )

    dispose_engine()
    get_settings.cache_clear()


def test_clear_candidate_image_cache_only_clears_candidate_images(isolated_storage) -> None:
    candidate_images = isolated_storage.cache / "candidate-images"
    candidate_images.mkdir(parents=True)
    (candidate_images / "a.jpg").write_bytes(b"candidate-a")
    (candidate_images / "b.jpg").write_bytes(b"candidate-bb")

    taste_offers = isolated_storage.cache / "taste-offers"
    taste_offers.mkdir(parents=True)
    (taste_offers / "offer.jpg").write_bytes(b"taste-offer")

    isolated_storage.uploads.mkdir(parents=True, exist_ok=True)
    (isolated_storage.uploads / "sample.jpg").write_bytes(b"upload-image")

    isolated_storage.feedback_assets.mkdir(parents=True, exist_ok=True)
    (isolated_storage.feedback_assets / "feedback.jpg").write_bytes(b"feedback-image")

    result = svc.clear_candidate_image_cache()

    assert result.bytes_freed == len(b"candidate-a") + len(b"candidate-bb")
    assert result.files_removed == 2
    assert list(candidate_images.rglob("*")) == []
    assert candidate_images.exists()
    assert (taste_offers / "offer.jpg").read_bytes() == b"taste-offer"
    assert (isolated_storage.uploads / "sample.jpg").read_bytes() == b"upload-image"
    assert (isolated_storage.feedback_assets / "feedback.jpg").read_bytes() == b"feedback-image"
