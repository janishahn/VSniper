from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vsniper.integrations.vinted.browser import (
    BrowserListingChallengeError,
    BrowserListingUnconfiguredError,
    VintedBrowserClient,
)


def _settings(*, proxy_url: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        vinted_browser_proxy_url=proxy_url,
        vinted_browser_timeout_seconds=1,
        vinted_browser_profile_dir=Path("/tmp/vsniper-browser-test"),
        vinted_browser_executable="",
    )


class _FakePage:
    def __init__(self, *, title: str, html: str) -> None:
        self._title = title
        self._html = html
        self.visited: list[str] = []

    def goto(self, url: str, **_: object) -> None:
        self.visited.append(url)

    def content(self) -> str:
        return self._html

    def title(self) -> str:
        return self._title

    def wait_for_timeout(self, _ms: int) -> None:
        pass


class _FakeContext:
    def __init__(self, page: _FakePage, *, cookies: list[dict] | None = None) -> None:
        self.pages = [page]
        self._cookies = cookies or []
        self.added: list[dict] = []

    def cookies(self, _url: str) -> list[dict]:
        return self._cookies

    def add_cookies(self, cookies: list[dict]) -> None:
        self.added.extend(cookies)


def _client(monkeypatch, context: _FakeContext, **settings: str) -> VintedBrowserClient:
    client = VintedBrowserClient(_settings(**settings))  # type: ignore[arg-type]
    monkeypatch.setattr(client, "_context_or_create", lambda: context)
    return client


def test_browser_fetch_returns_listing_html(monkeypatch) -> None:
    html = '<meta property="og:title" content="Wide cargos | Vinted">'
    page = _FakePage(title="Wide cargos | Vinted", html=html)
    client = _client(monkeypatch, _FakeContext(page))

    assert client.fetch_html("https://www.vinted.de/items/123-item", cookie_header="") == html
    assert page.visited == ["https://www.vinted.de/items/123-item"]


def test_browser_fetch_keeps_cookies_the_profile_already_has(monkeypatch) -> None:
    page = _FakePage(title="Wide cargos | Vinted", html='<meta property="og:title" content="x">')
    context = _FakeContext(page, cookies=[{"name": "datadome", "value": "solved-in-browser"}])
    client = _client(monkeypatch, context)

    client.fetch_html(
        "https://www.vinted.de/items/123-item",
        cookie_header="access_token_web=token; datadome=stale-from-env",
    )

    assert [(cookie["name"], cookie["value"]) for cookie in context.added] == [("access_token_web", "token")]


@pytest.mark.parametrize(
    ("title", "html"),
    [
        ("Just a moment...", "<html><script>window._cf_chl_opt = {}</script></html>"),
        ("vinted.de", '<iframe src="https://geo.captcha-delivery.com/captcha/?initialCid=x"></iframe>'),
    ],
)
def test_browser_fetch_reports_cloudflare_and_datadome_challenges(monkeypatch, title: str, html: str) -> None:
    client = _client(monkeypatch, _FakeContext(_FakePage(title=title, html=html)))

    with pytest.raises(BrowserListingChallengeError, match="Open the Vinted browser"):
        client.fetch_html("https://www.vinted.de/items/123-item", cookie_header="")


def test_empty_proxy_configuration_uses_no_proxy() -> None:
    assert VintedBrowserClient(_settings())._proxy_settings() is None  # type: ignore[arg-type]


def test_invalid_proxy_configuration_is_rejected() -> None:
    client = VintedBrowserClient(_settings(proxy_url="proxy.example"))  # type: ignore[arg-type]

    with pytest.raises(BrowserListingUnconfiguredError, match="http, https, or socks5"):
        client._proxy_settings()


def test_authenticated_proxy_credentials_are_passed_separately() -> None:
    client = VintedBrowserClient(  # type: ignore[arg-type]
        _settings(proxy_url="http://proxy-user:p%40ss@proxy.example:8080")
    )

    assert client._proxy_settings() == {
        "server": "http://proxy.example:8080",
        "username": "proxy-user",
        "password": "p@ss",
    }
