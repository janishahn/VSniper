from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from vsniper.core.database import Base
from vsniper.db.models import Search, TasteSampleState
from vsniper.domain.contracts import BucketCategory, BucketCreate, BucketRecord, BucketUpdate, SearchFilter
from vsniper.integrations.openai.client import OpenAITasteClient
from vsniper.services.bucket_service import BucketService, bucket_records


def _service(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'buckets.db'}", future=True)
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

    monkeypatch.setattr("vsniper.services.bucket_service.session_scope", fake_session_scope)
    settings = SimpleNamespace(cache_dir=tmp_path / "cache", resolve_path=lambda path: path)
    monkeypatch.setattr("vsniper.services.bucket_service.get_settings", lambda: settings)
    return BucketService(settings, SimpleNamespace()), factory  # type: ignore[arg-type]


def _search(factory, key: str) -> Search | None:
    with factory() as session:
        return session.scalar(select(Search).where(Search.clothing_item == key))


def test_a_new_bucket_gets_a_disabled_search_over_its_categories(monkeypatch, tmp_path) -> None:
    service, factory = _service(monkeypatch, tmp_path)

    bucket = service.create(BucketCreate(key="kleider", label="Kleider", description="Dresses.", catalog_ids=[10, 1055]))

    assert [category.path for category in bucket.categories] == ["Damen > Kleidung > Kleider", "Damen > Kleidung > Kleider > Maxikleider"]
    search = _search(factory, "kleider")
    assert search is not None and search.enabled is False and search.name == "Kleider"
    assert SearchFilter.model_validate(search.filters[0]).values == ["10", "1055"]

    # Narrowing the bucket drops categories the search may no longer use.
    service.update("kleider", BucketUpdate(label="Kleider", description="Dresses.", catalog_ids=[1055]))
    assert SearchFilter.model_validate(_search(factory, "kleider").filters[0]).values == ["1055"]


def test_delete_removes_an_unused_bucket_and_archives_one_with_history(monkeypatch, tmp_path) -> None:
    service, factory = _service(monkeypatch, tmp_path)
    service.create(BucketCreate(key="kleider", label="Kleider", catalog_ids=[10]))
    service.create(BucketCreate(key="ringe", label="Ringe", catalog_ids=[553]))
    with factory() as session:
        session.add(TasteSampleState(id="sample-1", kind="wardrobe", clothing_item="ringe"))
        session.get(Search, "search-ringe").enabled = True
        session.commit()

    assert service.delete("kleider").outcome == "deleted"
    assert service.delete("ringe").outcome == "archived"

    assert _search(factory, "kleider") is None
    assert _search(factory, "ringe").enabled is False
    with factory() as session:
        assert bucket_records(session) == []
        assert [bucket.key for bucket in bucket_records(session, include_archived=True)] == ["ringe"]


def test_taste_profile_prompt_and_schema_follow_the_active_buckets() -> None:
    buckets = [
        BucketRecord(
            key=key,
            label=label,
            description=description,
            categories=[BucketCategory(id=catalog_id, path=path) for catalog_id, path in categories],
            sizes=[],
            position=index,
            archived=False,
        )
        for index, (key, label, description, categories) in enumerate(
            [
                ("ringe", "Ringe", "Rings.", [(553, "Damen > Accessoires > Schmuck > Ringe")]),
                ("kleider", "Kleider", "Dresses.", [(10, "Damen > Kleidung > Kleider"), (1055, "Damen > Kleidung > Kleider > Maxikleider")]),
            ]
        )
    ]
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read().decode("utf-8")))
        item = {
            "summary": "",
            "taste_prompt": "You like it.",
            "core_aesthetic_summary": "",
            "likes": [],
            "dislikes_or_penalties": [],
            "instant_alert_examples": [],
            "instant_reject_examples": [],
            "scoring_rubric": {},
            "transparency_labels": [],
            "cross_item_influence": [],
        }
        profile = {
            "taste_prompt": "You like it.",
            "item_profiles": [
                {**item, "clothing_item": "kleider", "generated_search": {
                    "name": "", "query": "maxikleid", "region": "de", "rationale": "",
                    "filters": [{"field": "category", "label": "", "values": ["1055", "553"], "mode": "include"}],
                }},
            ],
        }
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(profile)}]}]})

    client = OpenAITasteClient(
        SimpleNamespace(openai_api_key="test-key", openai_vision_model="gpt-6.1-terra"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    profile = client.build_taste_profile(
        observations=[], notes=[], liked_examples=[], disliked_examples=[], previous_profile=None, buckets=buckets
    )

    item_profiles = captured["text"]["format"]["schema"]["properties"]["item_profiles"]
    assert item_profiles["minItems"] == item_profiles["maxItems"] == 2
    assert item_profiles["items"]["properties"]["clothing_item"]["enum"] == ["ringe", "kleider"]
    prompt = captured["input"][0]["content"][0]["text"]
    assert '"path": "Damen > Kleidung > Kleider > Maxikleider"' in prompt
    # The draft keeps only the bucket's own categories; the omitted bucket gets a fallback draft.
    drafts = {draft.clothing_item: draft for draft in profile.generated_searches}
    assert drafts["kleider"].filters[0].values == ["1055"]
    assert drafts["ringe"].filters[0].values == ["553"]
    assert profile.item_profiles["kleider"].label == "Kleider"
