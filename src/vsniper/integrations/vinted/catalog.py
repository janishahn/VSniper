"""Vinted.de women's and men's catalog trees (Damen = 1904, Herren = 5), the source buckets pick
categories from.

A snapshot ships with the code. Refreshing re-reads the tree that vinted.de embeds in its
catalog page and stores it next to the other caches; that copy wins over the snapshot.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import httpx

from vsniper.domain.contracts import VintedCatalogNode

ROOT_IDS = (1904, 5)
CATALOG_PAGE_URL = "https://www.vinted.de/catalog/1904-women"
_SNAPSHOT_PATH = Path(__file__).with_name("catalog.json")
_OVERRIDE_NAME = "vinted_catalog.json"


class CatalogRefreshError(RuntimeError):
    pass


def parse_catalog_page(html: str) -> list[VintedCatalogNode]:
    """Extract the Damen and Herren subtrees from the `catalogTree` JSON in a vinted.de catalog page."""
    text = html.replace('\\"', '"')
    start = text.find('"catalogTree":[')
    if start == -1:
        raise CatalogRefreshError("The Vinted page no longer contains a catalogTree.")
    start = text.index("[", start)
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                roots = json.loads(text[start : index + 1])
                break
    else:
        raise CatalogRefreshError("The Vinted catalogTree is truncated.")

    nodes: list[VintedCatalogNode] = []

    def walk(raw: dict, parent_id: int | None, path: list[str]) -> None:
        node_path = [*path, str(raw["title"])]
        nodes.append(VintedCatalogNode(id=int(raw["id"]), parent_id=parent_id, title=str(raw["title"]), path=" > ".join(node_path)))
        for child in raw.get("catalogs") or []:
            walk(child, int(raw["id"]), node_path)

    roots_by_id = {root.get("id"): root for root in roots}
    for root_id in ROOT_IDS:
        if root_id not in roots_by_id:
            raise CatalogRefreshError(f"The Vinted catalogTree has no root {root_id}.")
        walk(roots_by_id[root_id], None, [])
    return nodes


def _override_path(cache_dir: Path) -> Path:
    return cache_dir / _OVERRIDE_NAME


def load_catalog(cache_dir: Path) -> list[VintedCatalogNode]:
    override = _override_path(cache_dir)
    path = override if override.exists() else _SNAPSHOT_PATH
    return _load(path, path.stat().st_mtime_ns)


@cache
def _load(path: Path, _mtime_ns: int) -> list[VintedCatalogNode]:
    return [VintedCatalogNode.model_validate(item) for item in json.loads(path.read_text(encoding="utf-8"))]


def catalog_by_id(cache_dir: Path) -> dict[int, VintedCatalogNode]:
    return {node.id: node for node in load_catalog(cache_dir)}


def refresh_catalog(cache_dir: Path, http_client: httpx.Client) -> list[VintedCatalogNode]:
    try:
        response = http_client.get(CATALOG_PAGE_URL, headers={"Accept-Language": "de-DE,de;q=0.9"})
    except httpx.HTTPError as exc:
        raise CatalogRefreshError(f"Could not reach vinted.de: {exc}") from exc
    if not response.is_success:
        raise CatalogRefreshError(f"Vinted returned HTTP {response.status_code} for the catalog page.")
    nodes = parse_catalog_page(response.text)
    override = _override_path(cache_dir)
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(json.dumps([node.model_dump() for node in nodes], ensure_ascii=False), encoding="utf-8")
    return nodes
