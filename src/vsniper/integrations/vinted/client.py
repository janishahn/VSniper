from __future__ import annotations

import base64
import json
import logging
import re
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx

from vsniper.domain.contracts import CandidateFeature, ClothingItem, SearchRecord
from vsniper.integrations.vinted.item_page import ItemPage, ItemPageError, parse_item_page

FILTER_OPTION_CACHE_TTL = timedelta(hours=6)
SESSION_RENEW_BEFORE = timedelta(hours=1)
VINTED_ACCEPT_LANGUAGE = "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"
VINTED_BASE_URL = "https://www.vinted.de"
VINTED_CATALOG_PER_PAGE = 96
VINTED_CURRENCY = "EUR"
VINTED_GATEWAY_BASE_URL = "https://api.vinted.de"
VINTED_WEB_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_logger = logging.getLogger(__name__)
FilterOptionCacheKey = tuple[str, str, str, str] | tuple[str, str, str, str, str]
_ITEM_URL_RE = re.compile(r"^/items/(?P<item_id>\d+)(?:[-/].*)?$")


def _jwt_expiry(token: str) -> datetime | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except ValueError:
        return None
    exp = payload.get("exp") if isinstance(payload, dict) else None
    return datetime.fromtimestamp(exp, tz=UTC) if isinstance(exp, (int, float)) else None

FILTER_ID_PARAM_BY_FIELD = {
    "brand": "brand_ids",
    "category": "catalog_ids",
    "catalog": "catalog_ids",
    "colour": "color_ids",
    "color": "color_ids",
    "condition": "status_ids",
    "status": "status_ids",
    "material": "material_ids",
    "size": "size_ids",
}

COLOR_ALIAS_IDS = {
    "black": ["1"],
    "schwarz": ["1"],
    "grey": ["3"],
    "gray": ["3"],
    "grau": ["3"],
    "white": ["12"],
    "weiß": ["12"],
    "weiss": ["12"],
    "cream": ["20"],
    "creme": ["20"],
    "beige": ["4"],
    "brown": ["2"],
    "braun": ["2"],
    "blue": ["9"],
    "blau": ["9"],
    "navy": ["27"],
    "green": ["10"],
    "grün": ["10"],
    "gruen": ["10"],
    "khaki": ["16"],
    "red": ["7"],
    "rot": ["7"],
    "yellow": ["8"],
    "gelb": ["8"],
}

STATUS_ALIAS_IDS = {
    "new with tags": ["6"],
    "neu mit etikett": ["6"],
    "new": ["1"],
    "neu": ["1"],
    "very good": ["2"],
    "sehr gut": ["2"],
    "good": ["3"],
    "gut": ["3"],
    "satisfactory": ["4"],
    "zufriedenstellend": ["4"],
}

MATERIAL_ALIAS_IDS = {
    "wool": ["46"],
    "wolle": ["46"],
    "leather": ["43"],
    "leder": ["43"],
    "suede": ["298"],
    "wildleder": ["298"],
    "cotton": ["44"],
    "baumwolle": ["44"],
    "cashmere": ["47"],
    "kaschmir": ["47"],
    "alpaca": ["122"],
    "alpaka": ["122"],
}


class VintedClientError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class VintedSessionError(VintedClientError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None) -> None:
        super().__init__(message, retryable=retryable, status_code=status_code)


class VintedSearchError(VintedClientError):
    pass


class VintedListingUrlError(VintedClientError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class VintedClient:
    """Talks to Vinted DE as an anonymous visitor.

    A first visit to the website hands out a public-scope `access_token_web` (valid a day),
    which the catalog and filter gateway accept. The client keeps those cookies in its own
    jar and fetches a new set when the token is close to expiry or Vinted rejects it.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or VINTED_BASE_URL).rstrip("/")
        # Tests point both hosts at one mock server.
        self.gateway_url = self.base_url if base_url else VINTED_GATEWAY_BASE_URL
        self._http = client or httpx.Client(timeout=timeout)
        self._http.headers["User-Agent"] = VINTED_WEB_USER_AGENT
        self._session_lock = threading.Lock()
        self._filter_option_cache: dict[FilterOptionCacheKey, tuple[datetime, list[dict[str, Any]]]] = {}

    def close(self) -> None:
        self._http.close()

    def _session_expiry(self) -> datetime | None:
        for cookie in self._http.cookies.jar:
            if cookie.name == "access_token_web" and cookie.value:
                return _jwt_expiry(cookie.value)
        return None

    def _ensure_session(self, *, renew: bool = False) -> None:
        with self._session_lock:
            expiry = self._session_expiry()
            if not renew and expiry is not None and expiry > datetime.now(UTC) + SESSION_RENEW_BEFORE:
                return
            self._http.cookies.clear()
            try:
                response = self._http.get(
                    f"{self.base_url}/",
                    headers={"Accept": "text/html,application/xhtml+xml", "Accept-Language": VINTED_ACCEPT_LANGUAGE},
                    follow_redirects=True,
                )
            except httpx.HTTPError as exc:
                raise VintedSearchError(f"Vinted could not be reached: {exc}", retryable=True) from exc
            if self._session_expiry() is None:
                raise VintedSessionError(
                    f"Vinted did not start a visitor session (HTTP {response.status_code}).",
                    retryable=True,
                    status_code=response.status_code,
                )
            _logger.info("Vinted visitor session started")

    def run_search(self, search: SearchRecord) -> list[dict[str, Any]]:
        payload = self._request_catalog("/svc-catalogue/items", self._build_search_params(search))
        normalized: list[dict[str, Any]] = []
        for item in self._extract_items(payload):
            try:
                normalized.append(self._normalise_item(item))
            except ValueError:
                continue
        return normalized

    def fetch_item_page(self, url: str) -> ItemPage:
        """Load one listing's full details (description, all photos, attributes)."""
        path = self._listing_path_from_url(url)
        if _ITEM_URL_RE.match(path) is None:
            raise VintedListingUrlError("Vinted URL must point to a listing under /items/<id>.")
        response = self._send(
            "GET",
            f"{self.base_url}{path}",
            headers={"Accept": "text/x-component", "Accept-Language": VINTED_ACCEPT_LANGUAGE, "RSC": "1"},
            follow_redirects=True,
        )
        if response.status_code == 404:
            raise VintedListingUrlError("Vinted could not find a listing at that URL.")
        if not response.is_success:
            raise VintedSearchError(
                self._non_success_detail(path=path, response=response),
                retryable=response.status_code == 429 or response.status_code >= 500,
                status_code=response.status_code,
            )
        try:
            return parse_item_page(response.text)
        except ItemPageError as exc:
            raise VintedListingUrlError(str(exc)) from exc

    def fetch_item_by_url(self, url: str, *, clothing_item: ClothingItem, region: str = "de") -> dict[str, Any]:
        item_id = self.listing_id_from_url(url)
        page = self.fetch_item_page(url)
        item = {
            "id": item_id,
            "title": page.title,
            "brand_title": page.brand,
            "size_title": page.size,
            "status": page.condition,
            "price": page.price_eur,
            "description": page.description,
            "url": f"{self.base_url}{self._listing_path_from_url(url)}",
            "photos": [{"url": image_url} for image_url in page.image_urls],
        }
        return self._normalise_item(item)

    def search_brands(self, query: str) -> list[dict[str, str]]:
        """Look up Vinted's own brand catalog for autocomplete, e.g. for a blocked-brands picker."""
        text = query.strip()
        if not text:
            return []
        payload = self._request(
            "/svc-filters/filters/search",
            params={
                "search_text": "",
                "currency": VINTED_CURRENCY,
                "order": "newest_first",
                "filter_search_code": "brand",
                "filter_search_text": text,
            },
        )
        options = payload.get("options")
        flattened = self._flatten_filter_options([o for o in options if isinstance(o, dict)]) if isinstance(options, list) else []
        results: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for option in flattened:
            option_id = option.get("id")
            title = self._first_non_empty(option.get("title"), option.get("code"))
            if option_id is None or title is None:
                continue
            str_id = str(option_id)
            if str_id in seen_ids:
                continue
            seen_ids.add(str_id)
            results.append({"id": str_id, "title": title})
        return results

    def size_options(self, catalog_ids: list[int]) -> list[dict[str, Any]]:
        """Vinted's size groups for these categories: [{"group": "WOMEN-LT", "sizes": [{"id", "title"}]}]."""
        if not catalog_ids:
            return []
        payload = self._request(
            "/svc-filters/filters/facets",
            params={
                "filter_code": "size",
                "attribute_ids[catalog]": ",".join(str(catalog_id) for catalog_id in catalog_ids),
                "currency": VINTED_CURRENCY,
            },
        )
        groups: list[dict[str, Any]] = []
        for group in payload.get("options") or []:
            if not isinstance(group, dict):
                continue
            sizes = [
                {"id": int(option["id"]), "title": str(option["title"])}
                for option in group.get("options") or []
                if isinstance(option, dict) and str(option.get("id", "")).isdigit() and option.get("title")
            ]
            if sizes:
                groups.append({"group": str(group.get("id") or ""), "sizes": sizes})
        return groups

    @staticmethod
    def _item_id_from_url(url: str) -> str:
        path = VintedClient._listing_path_from_url(url)
        match = _ITEM_URL_RE.match(path)
        if match is None:
            raise VintedListingUrlError("Vinted URL must point to a listing under /items/<id>.")
        return match.group("item_id")

    @staticmethod
    def _listing_path_from_url(url: str, *, allowed_host: str = "vinted.de") -> str:
        value = url.strip()
        # Browsers commonly show or copy URLs without their scheme. Treat a
        # domain/path pasted that way as HTTPS, which is how Vinted serves
        # listing pages, before applying the normal listing validation.
        if "://" not in value:
            value = f"https://{value}"
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise VintedListingUrlError("Enter a full Vinted listing URL.")
        host = (parsed.hostname or "").lower().rstrip(".")
        normalized_allowed_host = allowed_host.lower().rstrip(".")
        if host != normalized_allowed_host and not host.endswith(f".{normalized_allowed_host}"):
            raise VintedListingUrlError("Vinted URL must use the vinted.de domain.")
        return parsed.path.rstrip("/")

    def listing_id_from_url(self, url: str) -> str:
        path = self._listing_path_from_url(url)
        match = _ITEM_URL_RE.match(path)
        if match is None:
            raise VintedListingUrlError("Vinted URL must point to a listing under /items/<id>.")
        return match.group("item_id")

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send with the visitor session, starting a new session once if Vinted rejects it."""
        self._ensure_session()
        for attempt in range(2):
            try:
                response = self._http.request(method, url, **kwargs)
            except httpx.TimeoutException as exc:
                raise VintedSearchError("Vinted request timed out.", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise VintedSearchError(f"Vinted request failed: {exc}", retryable=True) from exc
            if response.status_code not in {401, 403} or attempt == 1:
                return response
            self._ensure_session(renew=True)
        raise AssertionError("unreachable")

    def _request(self, path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        response = self._send(
            "GET",
            f"{self.gateway_url}{path}",
            params=params,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": VINTED_ACCEPT_LANGUAGE,
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/catalog",
                "Platform": "web",
                "Locale": "de-DE",
            },
        )
        if response.status_code in {401, 403}:
            detail = "Vinted rejected the visitor session."
            if self._looks_like_datadome_block(response):
                detail += " The response looks like a DataDome block of this IP address."
            raise VintedSessionError(detail, retryable=True, status_code=response.status_code)
        if response.status_code == 429:
            raise VintedSearchError("Vinted rate-limited the request.", retryable=True, status_code=response.status_code)
        if response.status_code >= 500:
            raise VintedSearchError("Vinted returned an upstream server error.", retryable=True, status_code=response.status_code)
        if not response.is_success:
            raise VintedSearchError(
                self._non_success_detail(path=path, response=response),
                retryable=False,
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise VintedSearchError("Vinted returned a non-JSON response.", retryable=True) from exc
        if not isinstance(payload, dict):
            raise VintedSearchError("Vinted returned an unexpected response payload.", retryable=False)
        return payload

    @staticmethod
    def _non_success_detail(*, path: str, response: httpx.Response) -> str:
        body_preview = response.text[:500].strip() or "<empty body>"
        return (
            f"Vinted request failed with status {response.status_code} {response.reason_phrase} "
            f"while calling GET {path}. Response body: {body_preview}"
        )

    @staticmethod
    def _looks_like_datadome_block(response: httpx.Response) -> bool:
        if response.headers.get("x-dd-b") or response.headers.get("x-datadome"):
            return True
        set_cookie = response.headers.get("set-cookie", "").lower()
        return "datadome" in set_cookie or "datadome" in response.text[:500].lower()

    def _build_search_params(self, search: SearchRecord) -> dict[str, Any]:
        # A real browser starts a fresh search/browse session per search action; minting these
        # once per VintedClient instance instead (a long-lived api/worker process) reuses the
        # same ids across thousands of unrelated searches over hours, which Vinted's anti-bot
        # system eventually soft-blocks (200 OK, but zero items) rather than rejecting outright.
        params: dict[str, Any] = {
            "search_text": search.query,
            "page": 1,
            "per_page": VINTED_CATALOG_PER_PAGE,
            "time": int(datetime.now(UTC).timestamp()),
            "search_session_id": str(uuid4()),
            "global_catalog_browse_session_id": str(uuid4()),
            "currency": VINTED_CURRENCY,
            "order": "newest_first",
        }
        # Category filters already hold catalog ids, so they never depend on
        # anything else in `params`, whereas dynamic facet-based resolution for other id-based
        # fields (size, brand) only scopes itself to catalog_ids if it's already in `params`.
        # Resolve category/catalog filters first regardless of their stored position in
        # search.filters — callers (e.g. SearchService._apply_size_filter) can and do prepend a
        # size filter ahead of category, which would otherwise leave size lookups unscoped.
        ordered_filters = sorted(
            search.filters,
            key=lambda f: 0 if f.field.strip().lower() in {"category", "catalog"} else 1,
        )
        for filter_item in ordered_filters:
            values = [value.strip() for value in filter_item.values if value and value.strip()]
            if not values:
                continue
            field = filter_item.field.strip().lower()

            # Search keyword filters are local draft metadata, not Vinted catalog params.
            # The query itself remains the only upstream keyword search input.
            if field in {"keyword", "keywords"}:
                continue

            # Vinted's catalog API has no native exclude param for these fields; forwarding an
            # exclude filter as a positive include would return exactly the items it should remove.
            if filter_item.mode == "exclude":
                continue

            if filter_item.field == "price" and filter_item.mode == "range":
                non_empty = [v for v in values if str(v).strip()]
                if len(non_empty) >= 2:
                    params["price_from"] = non_empty[0]
                    params["price_to"] = non_empty[1]
                elif len(non_empty) == 1:
                    # Position determines semantics: if the non-empty value was first of
                    # two elements (second was empty/omitted) it is a lower bound (from);
                    # otherwise it is a price ceiling (single-value with no empty placeholder).
                    if len(values) >= 2 and not str(values[-1]).strip():
                        params["price_from"] = non_empty[0]
                    else:
                        params["price_to"] = non_empty[0]
                continue

            id_param = FILTER_ID_PARAM_BY_FIELD.get(field)
            if id_param:
                resolved_ids = self._resolve_filter_ids(
                    field=field,
                    values=values,
                    search=search,
                    current_params=params,
                )
                if resolved_ids:
                    params[id_param] = ",".join(resolved_ids)
                continue

            if filter_item.mode == "exact" and len(values) == 1:
                params[filter_item.field] = values[0]
                continue

            params[f"filter[{filter_item.field}]"] = ",".join(values)

        return params

    def _resolve_filter_ids(
        self,
        *,
        field: str,
        values: list[str],
        search: SearchRecord,
        current_params: dict[str, Any],
    ) -> list[str]:
        field = self._normalise_filter_field(field)
        resolved: list[str] = []
        for value in values:
            normalized = self._normalise_lookup_text(value)

            static_ids = self._static_filter_ids(field=field, normalized_value=normalized)
            if static_ids:
                resolved.extend(static_ids)
                continue

            option_ids = self._resolve_dynamic_filter_ids(
                field=field,
                value=value,
                search=search,
                current_params=current_params,
            )
            resolved.extend(option_ids)
        return list(dict.fromkeys(resolved))

    @staticmethod
    def _static_filter_ids(*, field: str, normalized_value: str) -> list[str]:
        field = VintedClient._normalise_filter_field(field)
        if field in {"category", "catalog", "size"}:
            # Bucket categories and sizes are stored as Vinted ids.
            return [normalized_value] if normalized_value.isdigit() else []
        if field in {"colour", "color"}:
            return COLOR_ALIAS_IDS.get(normalized_value, [])
        if field in {"condition", "status"}:
            return STATUS_ALIAS_IDS.get(normalized_value, [])
        if field == "material":
            return MATERIAL_ALIAS_IDS.get(normalized_value, [])
        return []

    def _resolve_dynamic_filter_ids(
        self,
        *,
        field: str,
        value: str,
        search: SearchRecord,
        current_params: dict[str, Any],
    ) -> list[str]:
        field = self._normalise_filter_field(field)
        if field in {"category", "catalog"}:
            _logger.warning(
                "Skipping unresolved Vinted category filter value %r for search %s; category facets are unsupported",
                value,
                search.id,
            )
            return []

        if field == "brand":
            options = self._search_filter_options(
                field=field,
                value=value,
                search=search,
                current_params=current_params,
            )
        else:
            options = self._facet_filter_options(field=field, search=search, current_params=current_params)

        normalized_value = self._normalise_lookup_text(value)
        matches: list[str] = []
        for option in self._flatten_filter_options(options):
            option_id = option.get("id")
            title = self._first_non_empty(option.get("title"), option.get("code"))
            if option_id is None or title is None:
                continue
            normalized_title = self._normalise_lookup_text(title)
            title_prefix = self._normalise_lookup_text(title.split("/", maxsplit=1)[0])
            if normalized_value in {normalized_title, title_prefix}:
                matches.append(str(option_id))

        if matches:
            return matches

        for option in self._flatten_filter_options(options):
            option_id = option.get("id")
            title = self._first_non_empty(option.get("title"), option.get("code"))
            if option_id is None or title is None:
                continue
            if normalized_value in self._normalise_lookup_text(title):
                return [str(option_id)]
        return []

    def _search_filter_options(
        self,
        *,
        field: str,
        value: str,
        search: SearchRecord,
        current_params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        params = self._filter_context_params(search=search, current_params=current_params)
        params["filter_search_code"] = field
        params["filter_search_text"] = value
        cache_key = (
            search.region,
            field,
            value,
            str(params.get("search_text") or ""),
            str(params.get("catalog_ids") or ""),
        )
        now = datetime.now(UTC)
        cached = self._filter_option_cache.get(cache_key)
        if cached is not None and cached[0] >= now - FILTER_OPTION_CACHE_TTL:
            return cached[1]
        payload = self._request_catalog("/svc-filters/filters/search", params)
        options = payload.get("options")
        result = [item for item in options if isinstance(item, dict)] if isinstance(options, list) else []
        self._filter_option_cache[cache_key] = (now, result)
        return result

    def _facet_filter_options(
        self,
        *,
        field: str,
        search: SearchRecord,
        current_params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        field = self._normalise_filter_field(field)
        if field in {"category", "catalog"}:
            _logger.warning("Skipping Vinted category facets for search %s; category facets are unsupported", search.id)
            return []

        params = self._filter_context_params(search=search, current_params=current_params)
        params["filter_code"] = "color" if field == "colour" else "status" if field == "condition" else field
        cache_key = (
            search.region,
            params["filter_code"],
            str(params.get("search_text") or ""),
            str(params.get("catalog_ids") or ""),
        )
        now = datetime.now(UTC)
        cached = self._filter_option_cache.get(cache_key)
        if cached is not None and cached[0] >= now - FILTER_OPTION_CACHE_TTL:
            return cached[1]

        payload = self._request_catalog("/svc-filters/filters/facets", params)
        options = payload.get("options")
        result = [item for item in options if isinstance(item, dict)] if isinstance(options, list) else []
        self._filter_option_cache[cache_key] = (now, result)
        return result

    def _request_catalog(self, gateway_path: str, params: dict[str, Any]) -> dict[str, Any]:
        # The gateway uses nested attributes for both catalog and filter IDs.
        gateway_params = dict(params)
        for key in set(FILTER_ID_PARAM_BY_FIELD.values()):
            if key in gateway_params:
                gateway_params[f"attribute_ids[{key.removesuffix('_ids')}]"] = gateway_params.pop(key)
        return self._request(gateway_path, params=gateway_params)

    @staticmethod
    def _filter_context_params(*, search: SearchRecord, current_params: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "search_text": search.query,
            "order": current_params.get("order", "newest_first"),
            "currency": current_params.get("currency", VINTED_CURRENCY),
        }
        for key in ("catalog_ids", "price_from", "price_to", "currency"):
            if current_params.get(key):
                params[key] = current_params[key]
        return params

    @classmethod
    def _flatten_filter_options(cls, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        for option in options:
            flattened.append(option)
            nested = option.get("options")
            if isinstance(nested, list):
                flattened.extend(cls._flatten_filter_options([item for item in nested if isinstance(item, dict)]))
        return flattened

    @staticmethod
    def _extract_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("items", "catalog_items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _normalise_item(self, item: dict[str, Any]) -> dict[str, Any]:
        external_item_id = self._first_non_empty(item.get("id"), item.get("item_id"), item.get("external_item_id"))
        if not external_item_id:
            raise ValueError("Listing payload is missing an id")

        title = self._first_non_empty(item.get("title"), item.get("name"), item.get("description_title")) or f"Vinted item {external_item_id}"
        brand = self._extract_brand(item)
        description = self._extract_description(item)
        price_eur = self._extract_price(item)
        size = self._first_non_empty(item.get("size_title"), item.get("size"), self._nested_value(item, "size", "title"))
        if not size:
            # Gateway listings display size and condition together, e.g. "L · Gut".
            second_line = self._first_non_empty(self._nested_value(item, "item_box", "second_line")) or ""
            size_part, separator, _ = second_line.partition(" · ")
            size = size_part if separator else "unknown"
        url = self._extract_url(item, external_item_id=str(external_item_id))
        image_urls = self._extract_image_urls(item)
        created_at = self._extract_created_at(item)
        features = self._extract_features(title=title, brand=brand, description=description)

        return {
            "id": str(external_item_id),
            "external_item_id": str(external_item_id),
            "title": title,
            "brand": brand,
            "price_eur": price_eur,
            "size": size,
            "url": url,
            "image_urls": image_urls,
            "created_at": created_at,
            "description": description,
            "features": features,
            "raw_listing": item,
        }

    @staticmethod
    def _extract_brand(item: dict[str, Any]) -> str:
        brand = VintedClient._first_non_empty(
            item.get("brand_title"),
            VintedClient._nested_value(item, "brand", "title"),
            VintedClient._nested_value(item, "brand_dto", "title"),
            item.get("brand"),
        )
        if not brand:
            first_line = VintedClient._nested_value(item, "item_box", "first_line")
            if first_line != item.get("title"):
                brand = VintedClient._first_non_empty(first_line)
        return brand or "Unknown"

    @staticmethod
    def _extract_description(item: dict[str, Any]) -> str:
        return VintedClient._first_non_empty(item.get("description"), item.get("item_description"), item.get("description_plain")) or ""

    @staticmethod
    def _extract_price(item: dict[str, Any]) -> float:
        candidates = [
            item.get("price_numeric"),
            VintedClient._nested_value(item, "price", "numeric"),
            VintedClient._nested_value(item, "price", "amount"),
            VintedClient._nested_value(item, "total_item_price", "amount"),
            item.get("price_eur"),
            item.get("price"),
        ]
        for candidate in candidates:
            value = VintedClient._coerce_float(candidate)
            if value is not None:
                return value
        return 0.0

    def _extract_url(self, item: dict[str, Any], *, external_item_id: str) -> str:
        url = self._first_non_empty(item.get("url"), item.get("item_url"), item.get("path"), item.get("item_path"))
        if url:
            if url.startswith("http://") or url.startswith("https://"):
                return url
            return urljoin(f"{self.base_url}/", url.lstrip("/"))
        return f"{self.base_url}/items/{external_item_id}"

    def _extract_image_urls(self, item: dict[str, Any]) -> list[str]:
        photos = item.get("photos") or item.get("images") or []
        urls: list[str] = []
        if isinstance(photos, list):
            for photo in photos:
                if isinstance(photo, dict):
                    url = self._first_non_empty(
                        photo.get("full_size_url"),
                        photo.get("url"),
                        photo.get("image_url"),
                        self._nested_value(photo, "thumbnails", 0, "url"),
                    )
                else:
                    url = str(photo) if photo else None
                if not url:
                    continue
                if not url.startswith("http://") and not url.startswith("https://"):
                    url = urljoin(f"{self.base_url}/", url.lstrip("/"))
                urls.append(url)
        return list(dict.fromkeys(urls))

    @staticmethod
    def _extract_created_at(item: dict[str, Any]) -> datetime | None:
        # Return None (not now()) when the listing has no parseable timestamp, so undated or
        # malformed listings are not silently treated as brand-new. Callers fall back to the
        # scan time for first_seen_at.
        raw_value = item.get("created_at_ts") or item.get("created_at") or item.get("created_at_epoch")
        if isinstance(raw_value, (int, float)):
            return datetime.fromtimestamp(raw_value, tz=UTC)
        if isinstance(raw_value, str):
            normalized = raw_value.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        return None

    @staticmethod
    def _extract_features(*, title: str, brand: str, description: str) -> list[CandidateFeature]:
        text_blob = " ".join(part for part in [title, brand, description] if part).lower()
        feature_rules = [
            (
                "palette_earth",
                "Earth-tone palette",
                ["brown", "braun", "beige", "camel", "cream", "creme", "olive", "oliv", "taupe", "tan"],
                ["neon", "hot pink", "fluoro", "lime", "multicolor", "mehrfarbig", "bunt"],
            ),
            (
                "fit_boxy",
                "Boxy fit",
                ["boxy", "oversized", "relaxed", "cropped bomber", "wide fit", "weiter schnitt", "locker"],
                ["slim", "skinny", "bodycon", "fitted", "eng geschnitten", "figurbetont"],
            ),
            (
                "material_wool",
                "Wool fabric",
                ["wool", "wolle", "mohair", "alpaca", "alpaka", "cashmere", "kaschmir", "merino"],
                ["polyester", "nylon", "acrylic", "acryl"],
            ),
            (
                "branding_minimal",
                "Minimal branding",
                [
                    "minimal",
                    "plain",
                    "clean",
                    "subtle",
                    "no logo",
                    "kein logo",
                    "minimal logo",
                    "small logo",
                    "kleines logo",
                ],
                [
                    "large logo",
                    "big logo",
                    "grosses logo",
                    "großes logo",
                    "graphic",
                    "big print",
                    "large print",
                    "grosser print",
                    "großer print",
                    "embroidered logo",
                ],
            ),
        ]

        features: list[CandidateFeature] = []
        for key, label, positive_terms, negative_terms in feature_rules:
            positive_hits = [term for term in positive_terms if term in text_blob]
            negative_hits = [term for term in negative_terms if term in text_blob]
            if not positive_hits and not negative_hits:
                continue

            signal_strength = (len(positive_hits) - len(negative_hits)) / max(len(positive_hits) + len(negative_hits), 1)
            features.append(
                CandidateFeature(
                    key=key,
                    label=label,
                    value=", ".join(positive_hits or negative_hits) or "unknown",
                    signal_strength=round(max(-1.0, min(1.0, signal_strength)), 2),
                    source="text_model",
                )
            )
        return features

    @staticmethod
    def _nested_value(payload: Any, *keys: Any) -> Any:
        value = payload
        for key in keys:
            if isinstance(key, int):
                if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) <= key:
                    return None
                value = value[key]
                continue
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            normalized = value.strip().replace("€", "").replace(" ", "")
            # German formatting: '.' is the thousands separator and ',' the decimal mark.
            # "1.234,56" -> "1234.56"; "1,23" -> "1.23"; "1234.56" stays as-is.
            if "," in normalized and "." in normalized:
                normalized = normalized.replace(".", "").replace(",", ".")
            elif "," in normalized:
                normalized = normalized.replace(",", ".")
            try:
                return float(normalized)
            except ValueError:
                return None
        if isinstance(value, dict):
            for nested_key in ("amount", "numeric"):
                nested_value = value.get(nested_key)
                coerced = VintedClient._coerce_float(nested_value)
                if coerced is not None:
                    return coerced
        return None

    @staticmethod
    def _first_non_empty(*values: Any) -> str | None:
        for value in values:
            if value is None:
                continue
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    return normalized
                continue
            if isinstance(value, (int, float, bool)):
                return str(value)
        return None

    @staticmethod
    def _normalise_lookup_text(value: str) -> str:
        return (
            value.strip()
            .lower()
            .replace("&", "and")
            .replace("ä", "ae")
            .replace("ö", "oe")
            .replace("ü", "ue")
            .replace("ß", "ss")
        )

    @staticmethod
    def _normalise_filter_field(value: str) -> str:
        return VintedClient._normalise_lookup_text(str(value))
