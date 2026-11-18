"""One scan: read every watched URL, compare with what was seen before, announce
what changed.

Rules (each one prevents a kind of false or missed alert):
- A product never seen before is "new"; one seen sold out that is now
  buyable is a "restock". Only those are announced, and only when the title
  passes the URL's filters (new sold-out listings: see ALERT_SOLD_OUT_NEW).
- The first successful read of a URL is silent: its products are recorded,
  not announced, so adding a page never floods Discord. Restocks seen only on
  such a URL are held back the same way.
- Products missing from a failed or partial read stay tracked as they were.
  Missing from a complete listing (see FetchResult.exhaustive), a product is
  recorded as sold out without an alert, so its return is announced as a
  restock: many stores hide sold-out items instead of labelling them.
- One request at a time per store, stores in parallel. A store that refuses
  requests (403/429) is left alone for BLOCK_BACKOFF_MINUTES.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import platforms
from .config import Settings
from .http import BlockedError, Http
from .models import FetchResult, Product
from .store import Store
from .watchlist import Watch

log = logging.getLogger(__name__)

Reader = Callable[[list[Watch]], dict[str, FetchResult]]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@dataclass
class Alert:
    kind: str           # "new" or "restock"
    product: Product
    label: str


@dataclass
class FirstRead:
    watch: Watch
    result: FetchResult
    matching: int       # products whose title passes the URL's filters


@dataclass
class ScanReport:
    results: dict[str, FetchResult]
    products_seen: int = 0
    alerts: list[Alert] = field(default_factory=list)
    held_back: int = 0
    gone: int = 0
    first_reads: list[FirstRead] = field(default_factory=list)
    first_failures: list[tuple[Watch, str]] = field(default_factory=list)

    def summary(self) -> str:
        ok = sum(r.ok for r in self.results.values())
        new = sum(a.kind == "new" for a in self.alerts)
        return (f"Scan done: {ok}/{len(self.results)} URLs read, {self.products_seen} products, "
                f"{new} new + {len(self.alerts) - new} restock alerts, {self.held_back} held back "
                f"(first read), {self.gone} gone from complete listings")


# -- reading ---------------------------------------------------------------------

def read_watch(watch: Watch, known_platform: str | None, http: Http) -> FetchResult:
    platform = watch.platform or known_platform or ""
    try:
        client = http.client(watch.route)
        if not platform:
            platform = platforms.detect(client, watch.url)
            log.info(f"{watch.url}: platform detected: {platform}")
        return platforms.fetch(client, watch.url, platform)
    except BlockedError as e:
        return FetchResult(ok=False, blocked=True, platform=platform, error=f"refused by the site ({e})")
    except Exception as e:
        return FetchResult(ok=False, platform=platform, error=f"{type(e).__name__}: {e}"[:300])


def read_all(watches: list[Watch], store: Store, http: Http, settings: Settings) -> dict[str, FetchResult]:
    paused = store.paused_hosts(utcnow().isoformat())
    known_platforms = {url: row["platform"] for url, row in store.sources().items()}
    by_host: dict[str, list[Watch]] = defaultdict(list)
    for watch in watches:
        if watch.host in paused:
            log.info(f"{watch.url}: skipped, {watch.host} is paused until {paused[watch.host]}")
        else:
            by_host[watch.host].append(watch)

    results: dict[str, FetchResult] = {}

    def read_host(host_watches: list[Watch]):
        for i, watch in enumerate(host_watches):
            if i:
                time.sleep(settings.host_pause_seconds)
            result = read_watch(watch, known_platforms.get(watch.url), http)
            results[watch.url] = result
            if result.ok:
                log.info(f"{watch.url}: {len(result.products)} products ({result.platform})")
            else:
                log.warning(f"{watch.url}: {result.error} ({len(result.products)} products kept)")
            if result.blocked:
                break       # leave the rest of this store for later

    with ThreadPoolExecutor(max_workers=settings.fetch_workers) as pool:
        list(pool.map(read_host, by_host.values()))
    return results


# -- comparing -----------------------------------------------------------------------

def _wanted(kind: str, product: Product, watches: list[Watch], settings: Settings) -> bool:
    if not any(w.filter.matches(product.title) for w in watches):
        return False
    return kind == "restock" or product.available or settings.alert_sold_out_new


def run_scan(watches: list[Watch], store: Store, notifier, settings: Settings, read: Reader,
             now: datetime | None = None) -> ScanReport:
    now = now or utcnow()
    stamp = now.isoformat()
    store.forget_sources_except([w.url for w in watches])
    sources = store.sources()
    known = {url for url, row in sources.items() if row["first_ok_at"]}

    results = read(watches)
    report = ScanReport(results=results)

    # Products found this scan, each with every watched URL it was found on.
    seen: dict[str, tuple[Product, list[Watch]]] = {}
    for watch in watches:
        for product in results.get(watch.url, FetchResult()).products:
            if not product.title:
                continue
            if product.key not in seen:
                seen[product.key] = (product, [watch])
            elif watch not in seen[product.key][1]:
                seen[product.key][1].append(watch)
    report.products_seen = len(seen)

    tracked = store.tracked()
    for key, (product, found_on) in seen.items():
        before = tracked.get(key)
        if before is None:
            kind = "new"
        elif product.available and not before.available:
            kind = "restock"
        elif before.available and not product.available:
            kind = "soldout"
        else:
            kind = None
        alerted = False
        if kind in ("new", "restock") and _wanted(kind, product, found_on, settings):
            if all(w.url not in known for w in found_on):
                report.held_back += 1
            else:
                report.alerts.append(Alert(kind, product, found_on[0].label))
                alerted = True
        if kind:
            store.add_event(stamp, kind, key, product.title, product.url, product.price, alerted)
        store.save_product(product, [w.url for w in found_on], stamp)

    complete = {url for url, r in results.items() if r.ok and r.exhaustive}
    for key, before in tracked.items():
        if key in seen or not before.available or not before.sources:
            continue
        if set(before.sources) <= complete:
            store.mark_unavailable(key, stamp)
            store.add_event(stamp, "missing", key, before.title, before.url, None, False)
            report.gone += 1

    for watch in watches:
        result = results.get(watch.url)
        if result is None:
            continue        # store paused
        row = sources.get(watch.url)
        if watch.url not in known:
            if result.ok:
                matching = sum(watch.filter.matches(p.title) for p in result.products)
                report.first_reads.append(FirstRead(watch, result, matching))
            elif row is None or row["failures"] == 0:
                report.first_failures.append((watch, result.error or "unknown error"))
        store.record_source(watch.url, result, stamp)
        if result.blocked and settings.block_backoff_minutes:
            until = now + timedelta(minutes=settings.block_backoff_minutes)
            store.pause_host(watch.host, until.isoformat())
            log.warning(f"{watch.host} refused requests: paused until {until:%Y-%m-%d %H:%M} UTC")

    # Restocks first: they sell out fastest.
    for kind in ("restock", "new"):
        by_label: dict[str, list[Product]] = defaultdict(list)
        for alert in report.alerts:
            if alert.kind == kind:
                by_label[alert.label].append(alert.product)
        for label, products in by_label.items():
            notifier.products(products, kind, label)
    if report.first_reads or report.first_failures:
        notifier.first_reads(report.first_reads, report.first_failures)

    store.prune_events((now - timedelta(days=90)).isoformat())
    store.commit()
    return report
