"""Platform readers. Each module exposes NAME and fetch(client, url) -> FetchResult;
the HTML-based ones also expose recognise(client, url, html) for auto-detection.

Adding a platform = one module here + an entry in READERS (and, if it can be
recognised from the page, in RECOGNISERS).
"""
from __future__ import annotations

from ..models import FetchResult
from . import crystalcommerce, ecwid, generic, jsonld, lightspeed, shopify

READERS = {m.NAME: m for m in (shopify, lightspeed, crystalcommerce, ecwid, jsonld, generic)}
PLATFORM_NAMES = tuple(READERS)
# Tried in this order on the page HTML once Shopify has been ruled out.
RECOGNISERS = (lightspeed, crystalcommerce, ecwid, jsonld)


def detect(client, url: str) -> str:
    """Pick the reader for a URL. Shopify is recognised by its JSON API, the
    others from the page itself; 'generic' when nothing else fits."""
    if shopify.probe(client, url):
        return shopify.NAME
    html = client.get(url).text
    for module in RECOGNISERS:
        if module.recognise(client, url, html):
            return module.NAME
    return generic.NAME


def fetch(client, url: str, platform: str) -> FetchResult:
    result = READERS[platform].fetch(client, url)
    result.platform = platform
    return result
