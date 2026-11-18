"""Data passed between the platform readers, the engine and the notifier."""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class Product:
    key: str                    # stable id, unique across stores ("shopify:store.com:123")
    title: str
    url: str                    # product page
    available: bool             # can be bought right now
    price: str | None = None    # as shown by the store, without currency
    image: str | None = None


@dataclass
class FetchResult:
    products: list[Product] = field(default_factory=list)
    ok: bool = True             # the page(s) were read without error
    # The listing is complete: a tracked product missing from it is no longer
    # sold there. Only readers that see a whole listing set this (a paginated
    # HTML page could simply have pushed the product to page 2).
    exhaustive: bool = False
    platform: str = ""
    error: str | None = None
    blocked: bool = False       # the site refused us (HTTP 403/429): back off


def host_of(url: str) -> str:
    """Host without 'www.', used to group URLs by store."""
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def origin_of(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def absolute_http(url: str | None) -> str | None:
    """'//cdn.example/x.jpg' -> 'https://cdn.example/x.jpg'; anything that is
    not an http(s) URL -> None (Discord rejects the whole message otherwise)."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    return url if url.startswith(("http://", "https://")) else None
