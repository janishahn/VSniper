from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import unquote, urlparse

from playwright.sync_api import BrowserContext, Page, Playwright, ProxySettings, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from vsniper.core.config import Settings


_logger = logging.getLogger(__name__)

# Cloudflare interstitials and DataDome's captcha iframe. Normal Vinted pages also load
# DataDome's tag script, so "datadome" alone is not a challenge marker.
_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-chl-",
    "challenge-platform",
    "_cf_chl_opt",
    "cf-turnstile",
    "captcha-delivery.com",
)


class BrowserListingError(RuntimeError):
    """Base error for the optional browser-backed listing transport."""


class BrowserListingUnconfiguredError(BrowserListingError):
    pass


class BrowserListingChallengeError(BrowserListingError):
    pass


class BrowserListingUnavailableError(BrowserListingError):
    pass


class VintedBrowserClient:
    """Own one persistent visible Chromium session for one-off listing imports.

    Chromium runs on the container's virtual display with a persistent profile, so a
    challenge solved once through the noVNC viewer keeps its clearance cookies across
    imports and restarts. Playwright's sync API is bound to the thread that started it,
    so all browser work runs on one dedicated thread.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vinted-browser")
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    def close(self) -> None:
        self._executor.submit(self._discard_context).result()
        self._executor.shutdown(wait=True)

    def fetch_html(self, url: str, *, cookie_header: str) -> str:
        return self._executor.submit(self._fetch_html, url, cookie_header).result()

    def _fetch_html(self, url: str, cookie_header: str) -> str:
        for attempt in range(2):
            try:
                context = self._context_or_create()
                self._add_missing_vinted_cookies(context, cookie_header)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url, wait_until="domcontentloaded")
                html = self._wait_for_listing_or_challenge(page)
                if self._looks_like_challenge(page.title(), html):
                    raise BrowserListingChallengeError(
                        "Vinted requires browser verification. Open the Vinted browser, "
                        "complete the challenge, then retry the import."
                    )
                return html
            except PlaywrightError as exc:
                self._discard_context()
                if attempt == 0:
                    continue
                raise BrowserListingUnavailableError(
                    "The Vinted listing browser is unavailable. Check the browser display and, "
                    "if configured, the proxy, then retry."
                ) from exc
        raise BrowserListingUnavailableError("The Vinted listing browser is unavailable.")

    def _context_or_create(self) -> BrowserContext:
        if self._context is not None:
            return self._context
        proxy = self._proxy_settings()
        profile_dir = self.settings.resolve_path(self.settings.vinted_browser_profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        try:
            context = self._playwright.chromium.launch_persistent_context(
                str(profile_dir),
                executable_path=self.settings.vinted_browser_executable or None,
                headless=False,
                no_viewport=True,
                args=["--disable-blink-features=AutomationControlled", "--window-size=1440,1000"],
                ignore_default_args=["--enable-automation"],
                proxy=proxy,
                timeout=self.settings.vinted_browser_timeout_seconds * 1000,
            )
        except PlaywrightError as exc:
            self._discard_context()
            raise BrowserListingUnavailableError(
                "The Vinted listing browser could not start. Check the browser display and, "
                "if configured, the proxy."
            ) from exc
        context.set_default_timeout(self.settings.vinted_browser_timeout_seconds * 1000)
        self._context = context
        return context

    def _proxy_settings(self) -> ProxySettings | None:
        proxy_url = self.settings.vinted_browser_proxy_url.strip()
        if not proxy_url:
            return None
        parsed = urlparse(proxy_url)
        if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname or parsed.port is None:
            raise BrowserListingUnconfiguredError(
                "VINTED_BROWSER_PROXY_URL must be an http, https, or socks5 URL including a port."
            )
        if (parsed.username is None) != (parsed.password is None):
            raise BrowserListingUnconfiguredError(
                "VINTED_BROWSER_PROXY_URL must include both a username and password, or neither."
            )
        if parsed.scheme == "socks5" and parsed.username is not None:
            raise BrowserListingUnconfiguredError(
                "Authenticated SOCKS5 proxies are not supported by Chromium; use an authenticated HTTP proxy."
            )
        proxy: ProxySettings = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username is not None and parsed.password is not None:
            proxy["username"] = unquote(parsed.username)
            proxy["password"] = unquote(parsed.password)
        return proxy

    @staticmethod
    def _add_missing_vinted_cookies(context: BrowserContext, cookie_header: str) -> None:
        """Seed the profile from the configured cookie without overwriting what it already holds.

        The profile's own cookies are newer: they include any clearance solved in the browser.
        """
        existing = {cookie.get("name") for cookie in context.cookies("https://www.vinted.de")}
        cookies: list[Any] = []
        for part in cookie_header.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name and value and name not in existing:
                cookies.append({"name": name, "value": value, "domain": ".vinted.de", "path": "/", "secure": True})
        if cookies:
            context.add_cookies(cookies)

    def _wait_for_listing_or_challenge(self, page: Page) -> str:
        deadline = time.monotonic() + max(1, self.settings.vinted_browser_timeout_seconds)
        while True:
            html = page.content()
            if 'property="og:title"' in html or "property='og:title'" in html:
                return html
            if self._looks_like_challenge(page.title(), html):
                return html
            if time.monotonic() >= deadline:
                raise BrowserListingUnavailableError("Timed out waiting for Vinted listing metadata.")
            page.wait_for_timeout(500)

    @staticmethod
    def _looks_like_challenge(title: str, html: str) -> bool:
        sample = f"{title}\n{html[:50_000]}".lower()
        return any(marker in sample for marker in _CHALLENGE_MARKERS)

    def _discard_context(self) -> None:
        context, self._context = self._context, None
        playwright, self._playwright = self._playwright, None
        try:
            if context is not None:
                context.close()
        except PlaywrightError:
            _logger.debug("Vinted browser context was already closed.", exc_info=True)
        if playwright is not None:
            playwright.stop()
