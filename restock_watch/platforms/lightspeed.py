"""Lightspeed eCom (C-Series) stores: any category, search or product page
answers with JSON when ?format=json is added. A listing gives each product's
id, title and a real "available" flag; ?limit=100 is the largest page size.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from ..models import FetchResult, Product, host_of

NAME = "lightspeed"
PAGE_LIMIT = 100


def json_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query.setdefault("limit", str(PAGE_LIMIT))
    query["format"] = "json"
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def recognise(client, url: str, html: str) -> bool:
    if "webshopapp.com" not in html:
        return False
    try:
        data = client.get(json_url(url)).json()
    except Exception:
        return False
    return isinstance(data, dict) and ("collection" in data or "product" in data)


def _title(item: dict) -> str:
    # fulltitle = "<brand> <title>": keep the brand, it helps filters,
    # unless the title already names it.
    title = (item.get("title") or "").strip()
    full = (item.get("fulltitle") or "").strip()
    brand = full[: -len(title)].strip() if title and full.endswith(title) else ""
    return full if full and brand.lower() not in title.lower() else title


def _product(item: dict, root: str, host: str, available: bool) -> Product | None:
    title = _title(item)
    if not title or item.get("id") is None:
        return None
    price = (item.get("price") or {}).get("price_incl")
    image = item.get("image")
    return Product(
        key=f"{NAME}:{host}:{item['id']}",
        title=title,
        url=urljoin(root, item.get("url") or ""),
        available=available,
        price=f"{float(price):.2f}" if isinstance(price, (int, float)) else None,
        image=image if isinstance(image, str) and image.startswith("http") else None,
    )


def fetch(client, url: str) -> FetchResult:
    data = client.get(json_url(url)).json()
    parsed = urlparse(url)
    # Product URLs are relative to the shop root, which includes the language
    # segment when there is one (/en/, /fr/).
    first = parsed.path.strip("/").split("/")[0]
    root = f"{parsed.scheme}://{parsed.netloc}/" + (f"{first}/" if len(first) == 2 else "")
    host = host_of(url)

    if data.get("product"):                         # a product page
        item = data["product"]
        stock = item.get("stock") or {}
        available = item["available"] if item.get("available") is not None else stock.get("available")
        product = _product(item, root, host, bool(available))
        return FetchResult([product] if product else [], exhaustive=True)

    collection = data.get("collection") or {}
    items = collection.get("products") or []
    if isinstance(items, dict):
        items = list(items.values())
    products = [p for p in (_product(i, root, host, bool(i.get("available"))) for i in items) if p]
    # Only the first page is read; a listing with more pages is not complete.
    return FetchResult(products, exhaustive=(collection.get("pages") or 1) <= 1)
