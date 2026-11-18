"""CrystalCommerce stores. Some themes mark
each variant "variant-row in-stock" / "variant-row no-stock"; others only
show text ("1 In Stock", "Out of stock") next to an add-to-cart form.

Some themes put an HTML comment mentioning "out of stock" in every product tile, so
comments are ignored when falling back to text. Many of these stores list
in-stock items only, which is why the pager is followed: a complete listing
lets a product that disappeared be recorded as sold out, and its return be
announced. These stores answer 503 to bursts, hence the pause between pages.
"""
from __future__ import annotations

import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment

from ..http import BlockedError
from ..models import FetchResult, Product, absolute_http
from ._common import price_from_text, product_key

NAME = "crystalcommerce"
SOLD_OUT_PHRASES = ("out of stock", "sold out", "hors stock", "épuisé", "rupture de stock")
MAX_PAGES = 5
PAGE_PAUSE_SECONDS = 1.0


def recognise(client, url: str, html: str) -> bool:
    return "crystalcommerce" in html.lower()


def _available(tile) -> bool:
    if tile.select_one(".variant-row.in-stock"):
        return True
    if tile.select_one(".variant-row.no-stock"):
        return False
    return not tile.find(string=lambda s: not isinstance(s, Comment)
                         and any(p in s.lower() for p in SOLD_OUT_PHRASES))


def parse_page(soup: BeautifulSoup, url: str) -> list[Product]:
    products = []
    for tile in soup.find_all("li", class_="product") or soup.find_all("div", class_="product"):
        title_elem = tile.find("h4") or tile.find(class_="product-name")
        if not title_elem:
            continue
        title = title_elem.get_text(strip=True)
        link = tile.find("a", href=True)
        product_url = urljoin(url, link["href"]) if link else url
        price_elem = tile.find(class_="price") or tile.find(class_="regular-price")
        img = tile.find("img", src=True)
        products.append(Product(
            key=product_key(NAME, product_url, url, title),
            title=title,
            url=product_url,
            available=_available(tile),
            price=price_from_text(price_elem.get_text(" ", strip=True)) if price_elem else None,
            image=absolute_http(urljoin(url, img["src"])) if img else None,
        ))
    return products


def next_page(soup: BeautifulSoup, url: str) -> str | None:
    link = soup.select_one(".pagination a.next_page[href], .pagination a[rel=next][href]")
    return urljoin(url, link["href"]) if link else None


def parse(html: str, url: str) -> list[Product]:
    return parse_page(BeautifulSoup(html, "lxml"), url)


def fetch(client, url: str) -> FetchResult:
    found: dict[str, Product] = {}
    page_url: str | None = url
    try:
        for page in range(MAX_PAGES):
            if page:
                time.sleep(PAGE_PAUSE_SECONDS)
            soup = BeautifulSoup(client.get(page_url).text, "lxml")
            for product in parse_page(soup, url):
                found.setdefault(product.key, product)
            page_url = next_page(soup, page_url)
            if not page_url:
                break
    except Exception as e:
        if not found:
            raise
        return FetchResult(list(found.values()), ok=False, blocked=isinstance(e, BlockedError),
                           error=f"stopped after {len(found)} products: {e}")
    # Complete only when the last page was reached (no "next" link left).
    return FetchResult(list(found.values()), exhaustive=page_url is None)
