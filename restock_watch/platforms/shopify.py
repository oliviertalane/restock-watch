"""Shopify stores, read through the public JSON every Shopify storefront serves.

  /collections/<handle>  -> its products.json, every page (250 per page, up to 1,000 products)
  /products/<handle>     -> /products/<handle>.js, that one product
  /search?...            -> the store's 250 newest products: Shopify has no JSON
                            search, so narrow it down with include=
  anything else          -> the whole store's products.json, newest first (up to 1,000)

A collection's products.json follows the collection's display order, not
creation date, so a product added today can be anywhere: the whole collection
is read. Query strings and #fragments of the watched URL are ignored.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..http import BlockedError
from ..models import FetchResult, Product, absolute_http, host_of, origin_of

NAME = "shopify"
PAGE_SIZE = 250
MAX_PAGES = 4
PRODUCT_PATH = re.compile(r"/products/([^/]+?)(?:\.js|\.json)?$")


def probe(client, url: str) -> bool:
    """True when the site answers like a Shopify storefront."""
    try:
        data = client.get(origin_of(url) + "/products.json", params={"limit": 1}).json()
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get("products"), list)


def plan(url: str) -> tuple[str, str, int]:
    """(kind, json_url, max_pages) for a watched URL; kind is product, list or newest."""
    origin = origin_of(url)
    path = urlparse(url).path.rstrip("/")
    if path.endswith("/products.json"):
        return "list", origin + path, MAX_PAGES
    match = PRODUCT_PATH.search(path)
    if match:
        return "product", f"{origin}/products/{match.group(1)}.js", 1
    if "/collections/" in path:
        return "list", f"{origin}{path}/products.json", MAX_PAGES
    if path.endswith("/search"):
        return "newest", f"{origin}/products.json", 1
    return "list", f"{origin}/products.json", MAX_PAGES


def _lowest(prices: list) -> str | None:
    values = []
    for price in prices:
        try:
            values.append((float(price), str(price)))
        except (TypeError, ValueError):
            pass
    return min(values)[1] if values else None


def _from_listing(item: dict, origin: str, host: str) -> Product:
    variants = item.get("variants") or []
    in_stock = [v.get("price") for v in variants if v.get("available")]
    images = item.get("images") or []
    return Product(
        key=f"{NAME}:{host}:{item['id']}",
        title=(item.get("title") or "").strip(),
        url=f"{origin}/products/{item.get('handle', '')}",
        available=bool(in_stock),
        price=_lowest(in_stock or [v.get("price") for v in variants]),
        image=absolute_http(images[0].get("src")) if images else None,
    )


def _from_product_js(item: dict, origin: str, host: str) -> Product:
    variants = item.get("variants") or []
    cents = item.get("price")                       # the .js endpoint gives cents
    return Product(
        key=f"{NAME}:{host}:{item['id']}",
        title=(item.get("title") or "").strip(),
        url=origin + (item.get("url") or f"/products/{item.get('handle', '')}"),
        available=bool(item.get("available")) or any(v.get("available") for v in variants),
        price=f"{cents / 100:.2f}" if isinstance(cents, (int, float)) else None,
        image=absolute_http(item.get("featured_image")),
    )


def fetch(client, url: str) -> FetchResult:
    kind, json_url, max_pages = plan(url)
    origin, host = origin_of(url), host_of(url)
    if kind == "product":
        item = client.get(json_url).json()
        return FetchResult([_from_product_js(item, origin, host)], exhaustive=True)

    products: list[Product] = []
    try:
        for page in range(1, max_pages + 1):
            batch = client.get(json_url, params={"limit": PAGE_SIZE, "page": page}).json().get("products") or []
            products.extend(_from_listing(item, origin, host) for item in batch)
            if len(batch) < PAGE_SIZE:
                return FetchResult(products, exhaustive=(kind == "list"))
    except Exception as e:
        if not products:
            raise
        return FetchResult(products, ok=False, blocked=isinstance(e, BlockedError),
                           error=f"stopped after {len(products)} products: {e}")
    # Every page was full: the listing goes on beyond what was read.
    return FetchResult(products, exhaustive=False)
