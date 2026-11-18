"""Ecwid / Lightspeed E-Series "instant site" stores. Category pages are
server-rendered: every product is a div.grid-product cell (up to 60 per page)
whose class list carries the stock state and the product id.
"""
from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import FetchResult, Product, absolute_http, host_of
from ._common import price_from_text, product_key

NAME = "ecwid"


def recognise(client, url: str, html: str) -> bool:
    return "grid-product" in html and "ecwid" in html.lower()


def parse(html: str, url: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    products = []
    for cell in soup.select("div.grid-product"):
        title_elem = cell.select_one(".grid-product__title-inner")
        if not title_elem:
            continue
        title = title_elem.get_text(strip=True)
        classes = cell.get("class", [])
        ecwid_id = next((c.split("--id-", 1)[1] for c in classes if "--id-" in c), None)
        link = cell.select_one("a.grid-product__image[href], a.grid-product__title[href], a[href]")
        product_url = urljoin(url, link["href"]) if link else url
        price_elem = cell.select_one(".grid-product__price-value")
        img = cell.select_one("img[src]")
        products.append(Product(
            key=f"{NAME}:{host_of(url)}:{ecwid_id}" if ecwid_id else product_key(NAME, product_url, url, title),
            title=title,
            url=product_url,
            available=not any("outOfStock" in c for c in classes),
            price=price_from_text(price_elem.get_text(strip=True)) if price_elem else None,
            image=absolute_http(img["src"]) if img else None,
        ))
    return products


def fetch(client, url: str) -> FetchResult:
    return FetchResult(parse(client.get(url).text, url), exhaustive=False)
