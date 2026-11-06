from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from vsniper.domain.contracts import SearchFilter, SearchRecord
from vsniper.integrations.vinted.client import VintedClient, VintedListingUrlError, VintedSessionError
from vsniper.integrations.vinted.item_page import parse_item_page

FIXTURES = Path(__file__).parent / "fixtures/vinted/de"


def _visitor_cookie(expiry: datetime) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(expiry.timestamp()), "scope": "public"}).encode())
    token = f"header.{payload.rstrip(b'=').decode()}.signature"
    return f"access_token_web={token}; Domain=.vinted.de; Path=/"


def _search(*filters: SearchFilter, query: str = "cargo") -> SearchRecord:
    return SearchRecord(id="search-hosen", user_id=1, name="Hosen", clothing_item="hosen", query=query, region="de", filters=list(filters))


class _Vinted:
    """A fake vinted.de that hands out visitor sessions and answers with saved fixtures."""

    def __init__(self, gateway) -> None:
        self.gateway = gateway
        self.session_starts = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.vinted.de" and request.url.path == "/":
            self.session_starts += 1
            return httpx.Response(200, text="<html></html>", headers={"set-cookie": _visitor_cookie(datetime.now(UTC) + timedelta(hours=24))})
        assert "access_token_web=" in request.headers.get("cookie", "")
        return self.gateway(request)


def _client(vinted: _Vinted) -> VintedClient:
    return VintedClient(client=httpx.Client(transport=httpx.MockTransport(vinted)))


def test_search_uses_a_visitor_session_on_the_gateway() -> None:
    fixture = json.loads((FIXTURES / "gateway_items.json").read_text())

    def gateway(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.vinted.de"
        assert request.url.path == "/svc-catalogue/items"
        assert request.headers["platform"] == "web"
        assert request.headers["origin"] == "https://www.vinted.de"
        params = request.url.params
        assert params["attribute_ids[catalog]"] == "9"
        assert params["attribute_ids[size]"] == "4,5"
        assert params["attribute_ids[color]"] == "1"
        assert params["attribute_ids[status]"] == "2"
        assert params["price_to"] == "30"
        assert params["order"] == "newest_first"
        for key in ("catalog_ids", "size_ids", "color_ids", "status_ids"):
            assert key not in params
        return httpx.Response(200, json=fixture)

    vinted = _Vinted(gateway)
    client = _client(vinted)
    search = _search(
        SearchFilter(field="category", label="Category", values=["9"], mode="include"),
        SearchFilter(field="size", label="Sizes", values=["4", "5"], mode="include"),
        SearchFilter(field="colour", label="Colour", values=["black"], mode="include"),
        SearchFilter(field="condition", label="Condition", values=["very good"], mode="include"),
        SearchFilter(field="price", label="Price", values=["30"], mode="range"),
    )

    listing, = client.run_search(search)
    client.run_search(search)

    assert vinted.session_starts == 1
    assert listing["external_item_id"] == "9959847701"
    assert listing["brand"] == "One Vintage"
    assert listing["size"] == "W23 | DE 38"
    assert listing["price_eur"] == 20
    assert listing["url"] == "https://www.vinted.de/items/9959847701-baggy-cargo-hose-washed-grey-y2k-streetwear-gr-31"
    assert listing["image_urls"]


def test_a_rejected_session_is_renewed_once() -> None:
    responses = iter([httpx.Response(403, text="Forbidden"), httpx.Response(200, json={"items": []})])
    vinted = _Vinted(lambda request: next(responses))

    assert _client(vinted).run_search(_search()) == []
    assert vinted.session_starts == 2


def test_a_session_rejected_twice_raises() -> None:
    vinted = _Vinted(lambda request: httpx.Response(403, text="Forbidden"))

    with pytest.raises(VintedSessionError):
        _client(vinted).run_search(_search())
    assert vinted.session_starts == 2


def test_brand_filters_resolve_through_gateway_filter_search() -> None:
    def gateway(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/svc-filters/filters/search":
            assert request.url.params["filter_search_code"] == "brand"
            assert request.url.params["filter_search_text"] == "Levi's"
            assert request.url.params["attribute_ids[catalog]"] == "9"
            return httpx.Response(200, json={"options": [{"id": 10, "title": "Levi's"}]})
        assert request.url.params["attribute_ids[brand]"] == "10"
        return httpx.Response(200, json={"items": []})

    search = _search(
        SearchFilter(field="brand", label="Brand", values=["Levi's"], mode="include"),
        SearchFilter(field="category", label="Category", values=["9"], mode="include"),
    )
    assert _client(_Vinted(gateway)).run_search(search) == []


def test_size_options_list_vinted_sizes_for_the_categories() -> None:
    fixture = json.loads((FIXTURES / "gateway_size_facets.json").read_text())

    def gateway(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/svc-filters/filters/facets"
        assert request.url.params["filter_code"] == "size"
        assert request.url.params["attribute_ids[catalog]"] == "12,13"
        return httpx.Response(200, json=fixture)

    group, = _client(_Vinted(gateway)).size_options([12, 13])

    assert group["group"] == "WOMEN-LT"
    assert group["sizes"][:2] == [{"id": 1226, "title": "XXXS / 30 / 2"}, {"id": 102, "title": "XXS / 32 / 4"}]


def test_item_page_payload_gives_the_full_listing() -> None:
    def gateway(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.vinted.de"
        assert request.url.path == "/items/10686016435-anello-cabochon"
        assert request.headers["rsc"] == "1"
        return httpx.Response(200, text=(FIXTURES / "item_10686016435.rsc").read_text())

    client = _client(_Vinted(gateway))
    page = client.fetch_item_page("https://www.vinted.de/items/10686016435-anello-cabochon")
    listing = client.fetch_item_by_url("vinted.de/items/10686016435-anello-cabochon", clothing_item="ringe")

    assert page.title == "Anello cabochon"
    assert page.brand == "Argento"
    assert page.size == "55.6 mm / 17.7 mm Ø"
    assert page.condition == "Neu, ohne Etikett"
    assert page.color == "Silber"
    assert page.description.startswith("Anello in argento 925")
    assert page.price_eur == 22.0
    assert len(page.image_urls) == 4
    assert listing["external_item_id"] == "10686016435"
    assert listing["brand"] == "Argento"
    assert listing["price_eur"] == 22.0
    assert listing["description"] == page.description
    assert listing["image_urls"] == page.image_urls
    assert listing["raw_listing"]["status"] == "Neu, ohne Etikett"


def test_a_missing_listing_is_reported_as_a_bad_url() -> None:
    client = _client(_Vinted(lambda request: httpx.Response(404, text="Not found")))

    with pytest.raises(VintedListingUrlError):
        client.fetch_item_page("https://www.vinted.de/items/1-gone")


def test_a_long_description_is_read_from_its_text_row() -> None:
    # Vinted sends long descriptions as a separate text row and puts a reference like "$15d" in the field.
    page = parse_item_page((FIXTURES / "item_10688581075.rsc").read_text())

    assert page.title == "Lässige Jeans von American Vintage"
    assert page.description.startswith("Lässige Wide-Leg-Jeans von American Vintage (Modell: Le Big Carrot).")
    assert "$" not in page.description[:5]
    assert len(page.description) > 300
