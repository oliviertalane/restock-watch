"""The service: scan every SCAN_INTERVAL_MINUTES, send a daily heartbeat,
report errors to Discord, stop cleanly when Railway redeploys (SIGTERM).
"""
from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import __version__
from .config import Settings
from .discord import Discord, fit, short_url
from .engine import read_all, run_scan, utcnow
from .http import Http
from .store import Store
from .watchlist import Watch

log = logging.getLogger(__name__)

DB_FILE = "restock-watch.db"
CRASH_ALERT_EVERY_SECONDS = 3600


def stop_event() -> threading.Event:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return stop


def notifier_for(settings: Settings) -> Discord:
    return Discord(settings.webhook_url, settings.discord_username,
                   settings.currency_symbol, settings.discord_mention)


def startup_text(settings: Settings, watches: list[Watch], store: Store) -> str:
    known = {url for url, row in store.sources().items() if row["first_ok_at"]}
    unread = sum(w.url not in known for w in watches)
    stores = len({w.host for w in watches})
    lines = [f"🟢 **Restock Watch started**: watching {len(watches)} page{'s' if len(watches) > 1 else ''} "
             f"on {stores} store{'s' if stores > 1 else ''}, scanning every {settings.scan_interval_minutes} min."]
    if unread:
        lines.append(f"{unread} page{'s are' if unread > 1 else ' is'} new: the first scan records "
                     "what is there without alerts, and a summary follows.")
    lines += [f"⚠️ {w}" for w in settings.warnings]
    return "\n".join(lines)


def maybe_heartbeat(settings: Settings, store: Store, notifier: Discord, watches: list[Watch], now: datetime):
    """Once a day, at the first scan at/after HEARTBEAT_HOUR (TZ): proof of life + what is failing."""
    if settings.heartbeat_hour is None:
        return
    local = now.astimezone(ZoneInfo(settings.timezone))
    today = local.date().isoformat()
    last = store.get("heartbeat_date")
    if last is None:                    # first start: the startup message is enough today
        store.put("heartbeat_date", today)
        store.put("heartbeat_at", now.isoformat())
        store.commit()
        return
    if last == today or local.hour < settings.heartbeat_hour:
        return

    scans = int(store.get("scans_since_heartbeat") or 0)
    alerts = store.alerts_since(store.get("heartbeat_at") or "")
    sources = store.sources()
    watched = [w.url for w in watches]
    failing = [(url, sources[url]) for url in watched if url in sources and sources[url]["failures"]]
    empty = [url for url in watched if url in sources and not sources[url]["failures"]
             and sources[url]["last_count"] == 0]
    paused = store.paused_hosts(now.isoformat())
    ok = len(watched) - len(failing)

    lines = [f"✅ **Restock Watch is running**: {scans} scans since the last report, "
             f"{store.product_count()} products tracked, {ok}/{len(watched)} pages read OK, "
             f"{alerts} alert{'s' if alerts != 1 else ''} sent."]
    lines += [f"⏸️ `{host}` refused requests: paused until {until[11:16]} UTC" for host, until in paused.items()]
    lines += [f"⚠️ `{short_url(url)}` failing for {row['failures']} scans: {row['last_error']}"
              for url, row in failing]
    lines += [f"⚠️ `{short_url(url)}` lists no products" for url in empty]
    notifier.text(fit(lines))
    store.put("heartbeat_date", today)
    store.put("heartbeat_at", now.isoformat())
    store.put("scans_since_heartbeat", "0")
    store.commit()


def run(settings: Settings, watches: list[Watch], once: bool = False) -> int:
    store = Store(settings.data_dir / DB_FILE)
    notifier = notifier_for(settings)
    http = Http(settings.user_agent, settings.proxy_url, settings.relay_url, settings.relay_headers)

    def read(ws):
        return read_all(ws, store, http, settings)

    log.info(f"Restock Watch {__version__}: {len(watches)} URLs, every {settings.scan_interval_minutes} min, "
             f"state in {settings.data_dir / DB_FILE}")
    for warning in settings.warnings:
        log.warning(warning)
    if not settings.webhook_url:
        log.warning("DISCORD_WEBHOOK_URL is not set: alerts are only written to this log")

    stop = stop_event()
    if not once:
        notifier.text(startup_text(settings, watches, store))
    last_crash_alert = None

    while True:
        started = time.monotonic()
        try:
            report = run_scan(watches, store, notifier, settings, read)
            log.info(report.summary())
        except Exception as e:
            store.rollback()
            log.exception("Scan failed")
            if last_crash_alert is None or time.monotonic() - last_crash_alert > CRASH_ALERT_EVERY_SECONDS:
                notifier.text(f"❌ **Scan failed**: `{type(e).__name__}: {e}`"[:2000])
                last_crash_alert = time.monotonic()
            if once:
                return 1
        try:
            store.put("scans_since_heartbeat", str(int(store.get("scans_since_heartbeat") or 0) + 1))
            store.commit()
            maybe_heartbeat(settings, store, notifier, watches, utcnow())
        except Exception:
            log.exception("Heartbeat failed")
        if once:
            store.close()
            return 0
        wait = max(5.0, settings.scan_interval_minutes * 60 - (time.monotonic() - started))
        if stop.wait(wait):
            log.info("Stopping")
            store.close()
            return 0
