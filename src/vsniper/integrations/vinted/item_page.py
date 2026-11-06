"""Read a listing from the React Server Components payload of a Vinted item page.

Requesting an item page with the `RSC: 1` header returns the payload the page hydrates from.
Its sidebar `plugins` array carries the summary, attributes, description and price, and a
`photos` array holds every listing photo, none of which the catalog search returns.
Long strings such as the description are sent as separate text rows (`14b:T701,<text>`) and
referenced by `"$14b"`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

_PLUGINS_RE = re.compile(r'"plugins":(?=\[\{"data")')
_PHOTOS_RE = re.compile(r'"photos":(?=\[\{)')
_DECODER = json.JSONDecoder()
_TEXT_REFERENCE_RE = re.compile(r"\$([0-9a-f]+)")


class ItemPageError(ValueError):
    pass


class ItemPage(BaseModel):
    title: str
    brand: str
    size: str
    condition: str
    color: str
    material: str
    description: str
    price_eur: float | None
    image_urls: list[str]


def _decode_after(pattern: re.Pattern[str], text: str) -> list[Any]:
    match = pattern.search(text)
    if match is None:
        return []
    value, _ = _DECODER.raw_decode(text, match.end())
    return value if isinstance(value, list) else []


def _resolve_text(value: str, text: str) -> str:
    """Follow a `$<id>` reference to its text row; the row's hex length counts UTF-8 bytes."""
    if value.startswith("$$"):
        return value[1:]
    reference = _TEXT_REFERENCE_RE.fullmatch(value)
    if reference is None:
        return value
    row = re.search(rf"(?:^|\n){reference.group(1)}:T([0-9a-f]+),", text)
    if row is None:
        raise ItemPageError(f"The Vinted item page has no text row for {value}.")
    start = len(text[: row.end()].encode())
    return text.encode()[start : start + int(row.group(1), 16)].decode()


def parse_item_page(text: str) -> ItemPage:
    plugins = {
        plugin["name"]: plugin.get("data") or {}
        for plugin in _decode_after(_PLUGINS_RE, text)
        if isinstance(plugin, dict) and isinstance(plugin.get("name"), str)
    }
    if "attributes" not in plugins and "summary" not in plugins:
        raise ItemPageError("The Vinted item page has no listing data.")

    attributes = {
        attribute.get("code"): str((attribute.get("data") or {}).get("value") or "").strip()
        for attribute in plugins.get("attributes", {}).get("attributes", [])
        if isinstance(attribute, dict)
    }
    summary_lines = plugins.get("summary", {}).get("lines") or [{}]
    title_elements = summary_lines[0].get("elements") or [{}]
    offer = plugins.get("make_offer", {})
    title = str(title_elements[0].get("value") or offer.get("title") or "").strip()
    if not title:
        raise ItemPageError("The Vinted item page has no title.")

    amount = (offer.get("price") or {}).get("amount")
    return ItemPage(
        title=title,
        brand=attributes.get("brand", ""),
        size=attributes.get("size") or attributes.get("size_familiar", ""),
        condition=attributes.get("status", ""),
        color=attributes.get("color", ""),
        material=attributes.get("material", ""),
        description=_resolve_text(str(plugins.get("description", {}).get("description") or ""), text).strip(),
        price_eur=float(amount) if amount else None,
        image_urls=[
            photo["url"]
            for photo in _decode_after(_PHOTOS_RE, text)
            if isinstance(photo, dict) and isinstance(photo.get("url"), str)
        ],
    )
