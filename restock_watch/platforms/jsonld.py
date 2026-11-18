"""Any page that describes its products with schema.org JSON-LD, which most
product pages do (WooCommerce, BigCommerce, Wix, Squarespace, PrestaShop,
Magento, many custom shops). Only products whose offers state an availability
are kept: without it, stock cannot be told.
"""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

from ..models import FetchResult, Product, absolute_http, host_of
from ._common import price_from_text, product_key

NAME = "jsonld"
# schema.org ItemAvailability values that mean "can be ordered now"
BUYABLE = {"instock", "limitedavailability", "onlineonly", "preorder", "presale", "backorder"}


def _nodes(node):
    """Every object in a JSON-LD document, following the usual nestings."""
    if isinstance(node, list):
        for item in node:
            yield from _nodes(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item", "hasVariant", "mainEntity"):
            if key in node:
                yield from _nodes(node[key])


def _offers(node):
    if isinstance(node, list):
        for item in node:
            yield from _offers(item)
    elif isinstance(node, dict):
        yield node
        if "offers" in node:
            yield from _offers(node["offers"])


def _types(node: dict) -> set[str]:
    value = node.get("@type")
    values = value if isinstance(value, list) else [value]
    return {v.rsplit("/", 1)[-1].lower() for v in values if isinstance(v, str)}


def _state(value) -> str:
    # "https://schema.org/InStock", "http://schema.org/InStock", "schema:InStock", "InStock"
    return str(value).rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower()


def _price(offers: list[dict]):
    for offer in offers:
        for value in (offer.get("price"), offer.get("lowPrice")):
            if value is not None:
                return value
        spec = offer.get("priceSpecification")
        for item in spec if isinstance(spec, list) else [spec]:
            if isinstance(item, dict) and item.get("price") is not None:
                return item["price"]
    return None


def _image(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return absolute_http(value) if isinstance(value, str) else None


def parse(html: str, url: str) -> list[Product]:
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, Product] = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text(), strict=False)
        except ValueError:
            continue
        for node in _nodes(data):
            if not _types(node) & {"product", "individualproduct", "productmodel"}:
                continue
            offers = list(_offers(node.get("offers")))
            states = [_state(o["availability"]) for o in offers if o.get("availability")]
            name = (node.get("name") or "").strip()
            if not states or not name:
                continue
            price = _price(offers)
            product_url = (node.get("url") or next((o.get("url") for o in offers if o.get("url")), None) or url)
            sku = node.get("sku") or node.get("productID") or node.get("gtin13") or node.get("gtin")
            key = f"{NAME}:{host_of(url)}:{sku}" if sku else product_key(NAME, product_url, url, name)
            product = Product(
                key=key,
                title=name,
                url=absolute_http(product_url) or url,
                available=any(s in BUYABLE for s in states),
                price=price_from_text(str(price)) if price is not None else None,
                image=_image(node.get("image")),
            )
            if key in found:        # pages often describe a product twice: fill gaps
                first = found[key]
                first.price = first.price or product.price
                first.image = first.image or product.image
            else:
                found[key] = product
    return list(found.values())


def recognise(client, url: str, html: str) -> bool:
    return bool(parse(html, url))


def fetch(client, url: str) -> FetchResult:
    return FetchResult(parse(client.get(url).text, url), exhaustive=False)
