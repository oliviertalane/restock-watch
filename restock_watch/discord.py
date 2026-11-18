"""Discord webhook messages: product alerts, first-read summaries, heartbeat,
errors. Without a webhook URL everything is written to the log instead.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import requests

from .models import Product, host_of

log = logging.getLogger(__name__)

EMBEDS_PER_MESSAGE = 10         # Discord limit
CONTENT_LIMIT = 2000            # Discord limit
BLUE, GREEN = 5814783, 3066993
PLATFORM_NAMES = {"shopify": "Shopify", "lightspeed": "Lightspeed", "crystalcommerce": "CrystalCommerce",
                  "ecwid": "Ecwid", "jsonld": "product data (JSON-LD)", "generic": "generic HTML"}


def short_url(url: str, width: int = 70) -> str:
    parsed = urlparse(url)
    text = host_of(url) + parsed.path.rstrip("/") + (f"?{parsed.query}" if parsed.query else "")
    return text if len(text) <= width else text[: width - 1] + "…"


def fit(lines: list[str], limit: int = CONTENT_LIMIT) -> str:
    """Join lines, dropping the tail (with a note) if the message is too long."""
    out, used = [], 0
    for i, line in enumerate(lines):
        more = f"…and {len(lines) - i} more"
        if used + len(line) + 1 > limit - len(more) - 1:
            out.append(more)
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


class Discord:
    def __init__(self, webhook_url: str, username: str = "Restock Watch",
                 currency_symbol: str = "$", mention: str = ""):
        self.webhook_url = webhook_url
        self.username = username
        self.currency = currency_symbol
        self.mention = mention

    # -- transport ----------------------------------------------------------------

    def post(self, payload: dict) -> bool:
        payload = {"username": self.username, **payload}
        if not self.webhook_url:
            titles = [e["title"] for e in payload.get("embeds", [])]
            log.info(f"[no DISCORD_WEBHOOK_URL] {payload.get('content', '')}"
                     + "".join(f"\n  - {t}" for t in titles))
            return True
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=15)
            if response.status_code == 429:         # rate limited: wait as told, once
                wait = float(response.json().get("retry_after", 2))
                time.sleep(min(wait, 30))
                response = requests.post(self.webhook_url, json=payload, timeout=15)
            response.raise_for_status()
            return True
        except Exception as e:
            log.error(f"Discord message not sent: {e}")
            return False

    def text(self, content: str) -> bool:
        return self.post({"content": content[:CONTENT_LIMIT]})

    # -- product alerts -------------------------------------------------------------

    def _embed(self, product: Product, kind: str) -> dict:
        parts = []
        if product.price:
            parts.append(f"{self.currency}{product.price}")
        parts.append("✅ In stock" if product.available else "⛔ Sold out (you'll be told when it's back)")
        parts.append(host_of(product.url))
        embed = {"title": product.title[:256], "description": " · ".join(parts),
                 "color": GREEN if kind == "restock" else BLUE}
        if product.url.startswith(("http://", "https://")):
            embed["url"] = product.url
        if product.image:
            embed["thumbnail"] = {"url": product.image}
        return embed

    def products(self, products: list[Product], kind: str, label: str = ""):
        products = sorted(products, key=lambda p: not p.available)      # in stock first
        n = len(products)
        what = "back in stock" if kind == "restock" else f"new product{'s' if n > 1 else ''}"
        emoji = "🔄" if kind == "restock" else "🚨"
        header = f"{emoji} **{f'[{label}] ' if label else ''}{n} {what}**"
        if self.mention:
            header = f"{self.mention} {header}"
        embeds = [self._embed(p, kind) for p in products]
        for i in range(0, len(embeds), EMBEDS_PER_MESSAGE):
            payload = {"embeds": embeds[i:i + EMBEDS_PER_MESSAGE]}
            if i == 0:
                payload["content"] = header
            self.post(payload)
            if i + EMBEDS_PER_MESSAGE < len(embeds):
                time.sleep(1)

    # -- status messages ---------------------------------------------------------------

    def first_reads(self, first_reads, failures):
        lines = []
        if first_reads:
            lines.append(f"👀 **Now watching {len(first_reads)} new page{'s' if len(first_reads) > 1 else ''}.** "
                         "Their current products are recorded without alerts; from the next scan "
                         "on, new products and restocks are announced.")
            for item in first_reads:
                result = item.result
                in_stock = sum(p.available for p in result.products)
                line = (f"• `{short_url(item.watch.url)}` — {PLATFORM_NAMES.get(result.platform, result.platform)}, "
                        f"{len(result.products)} products ({in_stock} in stock")
                line += f", {item.matching} match your filters)" if item.matching != len(result.products) else ")"
                if not result.products:
                    line += " ⚠️ nothing found: is this a product listing page?"
                lines.append(line)
        if failures:
            lines.append(f"⚠️ **Could not read {len(failures)} page{'s' if len(failures) > 1 else ''}** "
                         "(retried every scan):")
            lines += [f"• `{short_url(watch.url)}` — {error}" for watch, error in failures]
        self.text(fit(lines))
