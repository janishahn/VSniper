from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from vsniper.domain.contracts import SearchFilter, SearchRecord
from vsniper.integrations.vinted.browser import BrowserListingChallengeError
from vsniper.integrations.vinted.client import (
    VINTED_BROWSER_VIEWER_PATH,
    VintedBrowserActionError,
    VintedClient,
    VintedSearchError,
)


def _jwt_with_expiry(expiry: datetime) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b'=').decode()
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(expiry.timestamp())}).encode()).rstrip(b'=').decode()
    return f'{header}.{payload}.signature'


def test_sizes_resolve_without_listing_constraints(monkeypatch) -> None:
    monkeypatch.setattr(
        'vsniper.integrations.vinted.client.get_settings',
        lambda: SimpleNamespace(vinted_cookie='', vinted_region='de'),
    )
    fixture = json.loads((Path(__file__).parent / 'fixtures/vinted/de/gateway_size_facets.json').read_text())
    lookups: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/v2/catalog/filters/facets':
            return httpx.Response(404)
        if request.url.path == '/svc-filters/filters/facets':
            lookups.append(request.url.params)
            assert request.url.params['attribute_ids[catalog]'] == '34'
            constrained = any(key in request.url.params for key in ('search_text', 'price_from', 'price_to'))
            return httpx.Response(200, json={'options': []} if constrained else fixture)
        assert request.url.path == '/api/v2/catalog/items'
        assert request.url.params['size_ids'] == '1640,1641'
        assert request.url.params['search_text'] in ('long generated query', 'another query')
        assert request.url.params['price_to'] == '30'
        return httpx.Response(200, json={'items': []})

    client = VintedClient(base_url='https://www.vinted.test', client=httpx.Client(transport=httpx.MockTransport(handler)))
    search = SearchRecord(
        id='search-hosen', name='Cargo', enabled=True, clothing_item='hosen', query='long generated query', region='de',
        filters=[
            SearchFilter(field='price', label='Price', values=['30'], mode='range'),
            SearchFilter(field='size', label='Size', values=['W32 | DE 48', 'W33 | DE 48'], mode='include'),
            SearchFilter(field='category', label='Category', values=['hosen'], mode='include'),
        ],
        last_run_at=None, last_found_count=0,
    )
    assert client.run_search(search, validate_session=False) == []
    assert client.run_search(search.model_copy(update={'query': 'another query'}), validate_session=False) == []
    assert len(lookups) == 1


@pytest.mark.parametrize('sizes', [['not-a-size'], ['not-a-size', 'M']])
def test_unresolved_sizes_never_remove_filter(monkeypatch, sizes) -> None:
    monkeypatch.setattr(
        'vsniper.integrations.vinted.client.get_settings',
        lambda: SimpleNamespace(vinted_cookie='', vinted_region='de'),
    )
    fixture = json.loads((Path(__file__).parent / 'fixtures/vinted/de/gateway_size_facets.json').read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/v2/catalog/filters/facets':
            return httpx.Response(200, json=fixture)
        assert 'M' in sizes, 'unresolved sizes must prevent an unfiltered catalog request'
        assert request.url.params['size_ids'] == '208'
        return httpx.Response(200, json={'items': []})

    client = VintedClient(base_url='https://www.vinted.test', client=httpx.Client(transport=httpx.MockTransport(handler)))
    search = SearchRecord(
        id='search-hosen', name='Cargo', enabled=True, clothing_item='hosen', query='cargo', region='de',
        filters=[SearchFilter(field='size', label='Size', values=sizes, mode='include')],
        last_run_at=None, last_found_count=0,
    )
    if 'M' in sizes:
        assert client.run_search(search, validate_session=False) == []
    else:
        with pytest.raises(VintedSearchError, match='size'):
            client.run_search(search, validate_session=False)


def test_search_uses_api_host_after_legacy_404(monkeypatch) -> None:
    # A live search cannot reliably exercise endpoint retirement or upstream
    # failures. Check routing here so a retired host cannot pass on fixture data.
    token = _jwt_with_expiry(datetime.now(UTC) + timedelta(hours=2))
    cookie = f'access_token_web={token}; anon_id=anon-1; csrf_token=csrf-1'
    monkeypatch.setattr(
        'vsniper.integrations.vinted.client.get_settings',
        lambda: SimpleNamespace(vinted_cookie=cookie, vinted_region='de'),
    )
    fixture = json.loads((Path(__file__).parent / 'fixtures/vinted/de/gateway_items.json').read_text())
    legacy_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/v2/catalog/items':
            assert request.url.host == 'www.vinted.de'
            legacy_params.update(request.url.params)
            return httpx.Response(404, text='<html>Not found</html>')
        assert request.url.host == 'api.vinted.de'
        assert request.url.path == '/svc-catalogue/items'
        assert request.headers['platform'] == 'web'
        assert request.headers['locale'] == 'de-DE'
        assert request.headers['origin'] == 'https://www.vinted.de'
        assert request.headers['referer'] == 'https://www.vinted.de/catalog'
        assert request.headers['sec-fetch-site'] == 'same-site'
        assert request.headers['cookie'] == cookie
        assert request.headers['x-anon-id'] == 'anon-1'
        assert request.headers['x-csrf-token'] == 'csrf-1'
        assert request.url.params['attribute_ids[catalog]'] == '34'
        assert request.url.params['attribute_ids[color]'] == '1'
        assert request.url.params['attribute_ids[status]'] == '2'
        assert request.url.params['attribute_ids[material]'] == '46'
        for key in ('catalog_ids', 'color_ids', 'status_ids', 'material_ids'):
            assert key not in request.url.params
        for key in ('search_session_id', 'global_catalog_browse_session_id', 'time', 'price_to', 'search_text'):
            assert request.url.params[key] == legacy_params[key]
        return httpx.Response(200, json=fixture)

    client = VintedClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    search = SearchRecord(
        id='search-hosen', name='Cargo', enabled=True, clothing_item='hosen', query='cargo', region='de',
        filters=[
            SearchFilter(field='category', label='Category', values=['hosen'], mode='include'),
            SearchFilter(field='colour', label='Colour', values=['black'], mode='include'),
            SearchFilter(field='condition', label='Condition', values=['very good'], mode='include'),
            SearchFilter(field='material', label='Material', values=['wool'], mode='include'),
            SearchFilter(field='price', label='Price', values=['30'], mode='range'),
        ],
        last_run_at=None, last_found_count=0,
    )
    listing, = client.run_search(search, validate_session=False)
    assert listing['external_item_id'] == '9959847701'
    assert listing['brand'] == 'One Vintage'
    assert listing['size'] == 'W23 | DE 38'
    assert listing['price_eur'] == 20
    assert listing['image_urls']


@pytest.mark.parametrize('field,operation', [('size', 'facets'), ('brand', 'search')])
def test_filter_lookup_uses_gateway_after_404(monkeypatch, field, operation) -> None:
    monkeypatch.setattr(
        'vsniper.integrations.vinted.client.get_settings',
        lambda: SimpleNamespace(vinted_cookie='', vinted_region='de'),
    )
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == f'/api/v2/catalog/filters/{operation}':
            assert request.url.host == 'www.vinted.de'
            return httpx.Response(404, json={
                'code': 104, 'message': 'Artikel kann nicht mehr gefunden werden', 'message_code': 'not_found',
            })
        if request.url.path == f'/svc-filters/filters/{operation}':
            assert request.url.host == 'api.vinted.de'
            assert request.headers['platform'] == 'web'
            assert request.headers['locale'] == 'de-DE'
            assert request.headers['sec-fetch-site'] == 'same-site'
            assert request.url.params['attribute_ids[catalog]'] == '34'
            assert 'catalog_ids' not in request.url.params
            assert request.url.params['currency'] == 'EUR'
            if field == 'size':
                assert 'search_text' not in request.url.params
                assert 'price_to' not in request.url.params
                assert request.url.params['filter_code'] == 'size'
            else:
                assert request.url.params['search_text'] == 'cargo'
                assert request.url.params['price_to'] == '30'
                assert request.url.params['filter_search_code'] == 'brand'
                assert request.url.params['filter_search_text'] == 'M'
            return httpx.Response(200, json={'options': [{'id': 208, 'title': 'M'}]})
        assert request.url.path == '/api/v2/catalog/items'
        assert request.url.params[f'{field}_ids'] == '208'
        assert request.url.params['catalog_ids'] == '34'
        return httpx.Response(200, json={'items': []})

    client = VintedClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    search = SearchRecord(
        id='search-hosen', name='Cargo', enabled=True, clothing_item='hosen', query='cargo', region='de',
        filters=[
            SearchFilter(field='price', label='Price', values=['30'], mode='range'),
            SearchFilter(field=field, label=field, values=['M'], mode='include'),
            SearchFilter(field='category', label='Category', values=['hosen'], mode='include'),
        ],
        last_run_at=None, last_found_count=0,
    )

    assert client.run_search(search, validate_session=False) == []
    assert client.run_search(search, validate_session=False) == []
    assert paths == [
        f'/api/v2/catalog/filters/{operation}', f'/svc-filters/filters/{operation}',
        '/api/v2/catalog/items', '/api/v2/catalog/items',
    ]


def test_vinted_client_validates_session_and_normalises_results(monkeypatch) -> None:
    token = _jwt_with_expiry(datetime.now(UTC) + timedelta(hours=2))
    cookie = f'access_token_web={token}; refresh_token_web={token}; anon_id=anon-1; datadome=dd-1; csrf_token=csrf-1'
    monkeypatch.setattr(
        'vsniper.integrations.vinted.client.get_settings',
        lambda: SimpleNamespace(vinted_cookie=cookie, vinted_region='de'),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers['cookie'].startswith('access_token_web=')
        assert 'content-type' not in request.headers
        assert request.headers['x-anon-id'] == 'anon-1'
        assert request.headers['x-csrf-token'] == 'csrf-1'
        if request.url.path == '/api/v2/users/current':
            return httpx.Response(200, json={"user": {"login": "alice"}})
        if request.url.path == '/api/v2/catalog/filters/facets':
            assert request.url.params['filter_code'] == 'size'
            return httpx.Response(
                200,
                json={
                    "filter_code": "size",
                    "options": [
                        {
                            "id": 1,
                            "title": "Clothing sizes",
                            "type": "group",
                            "options": [
                                {"id": 208, "title": "M", "type": "default"},
                                {"id": 209, "title": "L", "type": "default"},
                            ],
                        }
                    ],
                },
            )
        if request.url.path == '/api/v2/catalog/items':
            assert request.url.params['search_text'] == 'wool jacket'
            assert request.url.params['per_page'] == '96'
            assert request.url.params['currency'] == 'EUR'
            assert request.url.params['time'].isdigit()
            assert request.url.params['size_ids'] == '208,209'
            assert request.url.params['price_to'] == '200'
            assert 'price_from' not in request.url.params
            assert 'filter[keywords]' not in request.url.params
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": 12345,
                            "title": "Boxy wool bomber jacket",
                            "brand_title": "Acne Studios",
                            "price": {"amount": "149.95"},
                            "size_title": "M",
                            "url": "/items/12345-boxy-wool-bomber-jacket",
                            "description": "Brown oversized wool bomber with minimal logo",
                            "photos": [{"full_size_url": "https://images.vinted.example/12345.jpg"}],
                            "created_at": "2026-05-19T10:15:00Z",
                        }
                    ]
                },
            )
        raise AssertionError(f'unexpected request: {request.method} {request.url}')

    client = VintedClient(base_url='https://www.vinted.test', client=httpx.Client(transport=httpx.MockTransport(handler)))
    search = SearchRecord(
        id='search-1',
        name='Wool jackets',
        enabled=True,
        clothing_item='obenrum_kalt',
        query='wool jacket',
        region='de',
        filters=[
            SearchFilter(field='size', label='Sizes', values=['M', 'L'], mode='include'),
            SearchFilter(field='price', label='Price ceiling', values=['200'], mode='range'),
            SearchFilter(field='keywords', label='Prefer', values=['boxy'], mode='include'),
            SearchFilter(field='keywords', label='Avoid', values=['office'], mode='exclude'),
        ],
        last_run_at=None,
        last_found_count=0,
    )

    health = client.get_session_health(region='de', force=True)
    listings = client.run_search(search, validate_session=False)

    assert health.status == 'healthy'
    assert 'missing full browser cookie pieces' not in health.detail
    assert health.last_validated_at is not None
    assert listings[0]['external_item_id'] == '12345'
    assert listings[0]['price_eur'] == 149.95
    assert listings[0]['url'] == 'https://www.vinted.test/items/12345-boxy-wool-bomber-jacket'
    assert listings[0]['image_urls'] == ['https://images.vinted.example/12345.jpg']
    feature_map = {feature.key: feature for feature in listings[0]['features']}
    assert feature_map['material_wool'].signal_strength > 0
    assert feature_map['fit_boxy'].signal_strength > 0
    assert feature_map['branding_minimal'].signal_strength > 0


def test_fetch_item_by_url_retries_anonymously_after_stale_cookie_redirect(monkeypatch) -> None:
    monkeypatch.setattr(
        'vsniper.integrations.vinted.client.get_settings',
        lambda: SimpleNamespace(vinted_cookie='access_token_web=stale-token', vinted_region='de'),
    )
    cookies: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cookies.append(request.headers.get('cookie'))
        if request.headers.get('cookie'):
            return httpx.Response(307, headers={'location': '/web/api/auth/expire-cookies?ref_url=%2Fitems%2F12345'})
        return httpx.Response(
            200,
            text='''
                <meta property="og:title" content="Wide black cargos | Vinted">
                <meta property="og:image" content="https://images.vinted.example/12345.jpg">
            ''',
        )

    client = VintedClient(base_url='https://www.vinted.test', client=httpx.Client(transport=httpx.MockTransport(handler)))
    listing = client.fetch_item_by_url('https://www.vinted.test/items/12345-wide-black-cargos', clothing_item='hosen')

    assert listing['title'] == 'Wide black cargos'
    assert cookies[0] is not None
    assert cookies[1] is None


def test_fetch_item_by_url_parses_complete_de_listing_fixture(monkeypatch) -> None:
    monkeypatch.setattr(
        'vsniper.integrations.vinted.client.get_settings',
        lambda: SimpleNamespace(vinted_cookie='', vinted_region='de'),
    )
    fixture = Path(__file__).parent / 'fixtures' / 'vinted' / 'de' / 'item_9491586544.html'

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/items/9491586544-hose-mit-muster'
        return httpx.Response(200, text=fixture.read_text())

    client = VintedClient(
        base_url='https://www.vinted.de',
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    listing = client.fetch_item_by_url(
        'https://www.vinted.de/items/9491586544-hose-mit-muster',
        clothing_item='hosen',
    )

    assert listing['external_item_id'] == '9491586544'
    assert listing['title'] == 'Hose mit Muster'
    assert listing['brand'] == 'Cider'
    assert listing['size'] == 'L'
    assert listing['price_eur'] == 12.0
    assert listing['url'] == 'https://www.vinted.de/items/9491586544-hose-mit-muster'
    assert listing['image_urls'] == [
        'https://images1.vinted.net/t/02_0116a/f800/1785048655.webp',
        'https://images1.vinted.net/t/01_0236b/f800/1785048655.webp',
        'https://images1.vinted.net/t/05_00473/f800/1785048655.webp',
        'https://images1.vinted.net/t/05_00394/f800/1785048655.webp',
    ]


def test_fetch_item_by_url_maps_browser_challenge_to_actionable_error(monkeypatch) -> None:
    monkeypatch.setattr(
        'vsniper.integrations.vinted.client.get_settings',
        lambda: SimpleNamespace(vinted_cookie='', vinted_region='de'),
    )

    class FakeBrowser:
        def fetch_html(self, url: str, *, cookie_header: str) -> str:
            raise BrowserListingChallengeError("verification required")

    client = VintedClient(base_url='https://www.vinted.test', browser_client=FakeBrowser())  # type: ignore[arg-type]
    with pytest.raises(VintedBrowserActionError) as exc_info:
        client.fetch_item_by_url(
            'https://www.vinted.test/items/12345-wide-black-cargos',
            clothing_item='hosen',
        )

    assert exc_info.value.code == 'vinted_browser_challenge'
    assert exc_info.value.recovery_path == VINTED_BROWSER_VIEWER_PATH


def test_vinted_client_refresh_token_is_serialised_under_concurrency(monkeypatch) -> None:
    import threading

    expired_access = _jwt_with_expiry(datetime.now(UTC) - timedelta(minutes=5))
    refresh = _jwt_with_expiry(datetime.now(UTC) + timedelta(hours=2))
    cookie = f'access_token_web={expired_access}; refresh_token_web={refresh}'
    monkeypatch.setattr(
        'vsniper.integrations.vinted.client.get_settings',
        lambda: SimpleNamespace(vinted_cookie=cookie, vinted_region='de'),
    )

    call_count = 0
    barrier = threading.Barrier(4)
    fresh_access = _jwt_with_expiry(datetime.now(UTC) + timedelta(hours=2))

    def fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"access_token": fresh_access, "refresh_token": refresh})

    monkeypatch.setattr('vsniper.integrations.vinted.client.httpx.post', fake_post)

    client = VintedClient(base_url='https://www.vinted.test')
    client.set_cookie(cookie)

    def worker() -> None:
        barrier.wait()
        client._maybe_refresh_token()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Only one thread should have actually hit the refresh endpoint; the rest re-check the
    # (now fresh) token under the lock and return early.
    assert call_count == 1


def test_validate_cookie_does_not_refresh_or_persist_tokens(monkeypatch) -> None:
    # A probe cookie whose access token is within the refresh buffer (valid but soon to expire)
    # must NOT trigger a token refresh: refreshing rotates and upstream-invalidates the refresh
    # token inside the very cookie the user is about to save, and would persist it to the DB.
    monkeypatch.setattr(
        'vsniper.integrations.vinted.client.get_settings',
        lambda: SimpleNamespace(vinted_cookie='', vinted_region='de'),
    )
    persisted: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/oauth/token':
            raise AssertionError('validation probe must not refresh the token')
        if request.url.path == '/api/v2/users/current':
            return httpx.Response(200, json={"user": {"login": "probe"}})
        raise AssertionError(f'unexpected request: {request.method} {request.url}')

    client = VintedClient(
        base_url='https://www.vinted.test',
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        on_tokens_refreshed=lambda cookie, refresh: persisted.append((cookie, refresh)),
    )

    # Access token expires in 1 minute — inside TOKEN_REFRESH_BUFFER (5 min), so a live request
    # would refresh, but the probe must not.
    near_expiry = _jwt_with_expiry(datetime.now(UTC) + timedelta(minutes=1))
    probe_cookie = (
        f'access_token_web={near_expiry}; refresh_token_web=valid-refresh; '
        'anon_id=p; datadome=dd; csrf_token=csrf'
    )

    health = client.validate_cookie(probe_cookie)

    assert health.status == 'healthy'
    assert persisted == []  # nothing was persisted to the DB
