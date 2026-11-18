"""Helpers shared by the HTML readers."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from ..models import host_of

NUMBER = re.compile(r"\d[\d\s.,]*\d|\d")


def price_from_text(text: str | None) -> str | None:
    """'$1,299.99' / '1 299,99 $' / 'CAD 54.99' -> '1299.99' / '1299.99' / '54.99'."""
    match = NUMBER.search(text or "")
    if not match:
        return None
    num = re.sub(r"\s", "", match.group())
    if "," in num and "." in num:
        # the last separator is the decimal one
        num = num.replace(",", "") if num.rfind(".") > num.rfind(",") else num.replace(".", "").replace(",", ".")
    elif "," in num:
        head, _, tail = num.rpartition(",")
        num = f"{head.replace(',', '')}.{tail}" if len(tail) == 2 else num.replace(",", "")
    try:
        return f"{float(num):.2f}"
    except ValueError:
        return None


def product_key(platform: str, product_url: str, page_url: str, title: str) -> str:
    """Stable id for a product read from HTML: its page path, or its title
    when the listing gives no link of its own."""
    host = host_of(page_url)
    if product_url and product_url.split("#")[0] != page_url.split("#")[0]:
        path = urlparse(product_url).path.rstrip("/")
        if path:
            return f"{platform}:{host}{path}"
    return f"{platform}:{host}:" + hashlib.md5(title.encode()).hexdigest()[:16]
