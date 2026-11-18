"""Last resort for listing pages no other reader recognises: guesses product
tiles from common class names. Results vary from site to site; check a URL
with `python -m restock_watch check <url>` before relying on it.
"""
from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import FetchResult, Product, absolute_http
from ._common import price_from_text, product_key

NAME = "generic"
SOLD_OUT_PHRASES = ("out of stock", "sold out", "épuisé", "rupture", "unavailable")


def _has(classes, *words) -> bool:
    text = " ".join(classes).lower() if isinstance(classes, list) else (classes or "").lower()
    return all(w in text for w in words)


def _tiles(soup):
    return (soup.find_all(class_=lambda c: c and _has(c, "product") and _has(c, "card"))
            or soup.find_all(class_=lambda c: c and _has(c, "product-item"))
            or soup.find_all(class_=lambda c: c and _has(c, "item") and _has(c, "product"))
            or soup.find_all("article", class_=lambda c: c and _has(c, "product"))
            or soup.find_all("div", class_=lambda c: c and _has(c, "product")))


def _available(tile) -> bool:
    text = tile.get_text(" ").lower()
    if any(p in text for p in SOLD_OUT_PHRASES):
        return False
    # Nothing says sold out: count it as available (better an extra alert than a missed one).
    return True


def parse(html: str, url: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    products = []
    for tile in _tiles(soup):
        title_elem = (tile.find(["h2", "h3", "h4"])
                      or tile.find(class_=lambda c: c and _has(c, "title"))
                      or tile.find("a", class_=lambda c: c and _has(c, "name")))
        if not title_elem:
            continue
        title = title_elem.get_text(" ", strip=True)
        if not title:
            continue
        link = tile.find("a", href=True)
        product_url = urljoin(url, link["href"]) if link else url
        price_elem = tile.find(class_=lambda c: c and (_has(c, "price") or _has(c, "amount")))
        img = tile.find("img", src=True)
        products.append(Product(
            key=product_key(NAME, product_url, url, title),
            title=title,
            url=product_url,
            available=_available(tile),
            price=price_from_text(price_elem.get_text(" ", strip=True)) if price_elem else None,
            image=absolute_http(urljoin(url, img["src"])) if img else None,
        ))
    # Nested matches (a tile inside a wrapper that also matched) give duplicates.
    unique = {}
    for p in products:
        unique.setdefault(p.key, p)
    return list(unique.values())


def fetch(client, url: str) -> FetchResult:
    return FetchResult(parse(client.get(url).text, url), exhaustive=False)
