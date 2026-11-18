"""State kept on the volume, in one SQLite file: products seen, watched URLs,
paused stores, an event log and a few counters. Only the main thread uses it.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import FetchResult, Product

# After this many failed reads in a row, a URL's platform is detected again
# (the store may have moved to another platform).
REDETECT_AFTER_FAILURES = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    key         TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    url         TEXT,
    image       TEXT,
    price       TEXT,
    available   INTEGER NOT NULL,
    sources     TEXT NOT NULL,        -- JSON list of the watched URLs it was last seen on
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    last_change TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    url         TEXT PRIMARY KEY,     -- a watched URL, as written in WATCH_URLS
    platform    TEXT,
    first_ok_at TEXT,                 -- first successful read; changes are announced after it
    last_ok_at  TEXT,
    last_count  INTEGER,
    failures    INTEGER NOT NULL DEFAULT 0,   -- failed reads in a row
    last_error  TEXT
);
CREATE TABLE IF NOT EXISTS paused_hosts (
    host        TEXT PRIMARY KEY,
    until       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    kind        TEXT NOT NULL,        -- new | restock | soldout | missing
    key         TEXT NOT NULL,
    title       TEXT,
    url         TEXT,
    price       TEXT,
    alerted     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE TABLE IF NOT EXISTS meta (
    k           TEXT PRIMARY KEY,
    v           TEXT
);
"""


@dataclass(frozen=True)
class Tracked:
    available: bool
    sources: tuple[str, ...]
    title: str
    url: str


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def close(self):
        self.db.close()

    # -- counters and dates ---------------------------------------------------

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row["v"] if row else default

    def put(self, key: str, value: str):
        self.db.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                        (key, value))

    # -- watched URLs ---------------------------------------------------------

    def sources(self) -> dict[str, sqlite3.Row]:
        return {row["url"]: row for row in self.db.execute("SELECT * FROM sources")}

    def forget_sources_except(self, urls: list[str]):
        """Drop URLs no longer watched, so one re-added later gets a silent first read again."""
        keep = set(urls)
        gone = [(url,) for url in self.sources() if url not in keep]
        self.db.executemany("DELETE FROM sources WHERE url = ?", gone)

    def record_source(self, url: str, result: FetchResult, now: str):
        if result.ok:
            self.db.execute(
                """INSERT INTO sources (url, platform, first_ok_at, last_ok_at, last_count, failures, last_error)
                   VALUES (?, ?, ?, ?, ?, 0, NULL)
                   ON CONFLICT(url) DO UPDATE SET platform = excluded.platform,
                       first_ok_at = COALESCE(sources.first_ok_at, excluded.first_ok_at),
                       last_ok_at = excluded.last_ok_at, last_count = excluded.last_count,
                       failures = 0, last_error = NULL""",
                (url, result.platform, now, now, len(result.products)))
            return
        row = self.db.execute("SELECT failures FROM sources WHERE url = ?", (url,)).fetchone()
        failures = (row["failures"] if row else 0) + 1
        platform = result.platform if failures < REDETECT_AFTER_FAILURES else None
        self.db.execute(
            """INSERT INTO sources (url, platform, failures, last_error) VALUES (?, ?, ?, ?)
               ON CONFLICT(url) DO UPDATE SET platform = excluded.platform,
                   failures = excluded.failures, last_error = excluded.last_error""",
            (url, platform or None, failures, result.error))

    # -- stores that refused requests ------------------------------------------

    def paused_hosts(self, now: str) -> dict[str, str]:
        return {row["host"]: row["until"]
                for row in self.db.execute("SELECT host, until FROM paused_hosts WHERE until > ?", (now,))}

    def pause_host(self, host: str, until: str):
        self.db.execute("INSERT INTO paused_hosts (host, until) VALUES (?, ?) "
                        "ON CONFLICT(host) DO UPDATE SET until = excluded.until", (host, until))

    # -- products ---------------------------------------------------------------

    def tracked(self) -> dict[str, Tracked]:
        return {row["key"]: Tracked(bool(row["available"]), tuple(json.loads(row["sources"])),
                                    row["title"], row["url"])
                for row in self.db.execute("SELECT key, available, sources, title, url FROM products")}

    def save_product(self, product: Product, sources: list[str], now: str):
        self.db.execute(
            """INSERT INTO products (key, title, url, image, price, available, sources,
                                     first_seen, last_seen, last_change)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET title = excluded.title, url = excluded.url,
                   image = excluded.image, price = excluded.price, sources = excluded.sources,
                   last_change = CASE WHEN products.available != excluded.available
                                      THEN excluded.last_seen ELSE products.last_change END,
                   available = excluded.available, last_seen = excluded.last_seen""",
            (product.key, product.title, product.url, product.image, product.price,
             int(product.available), json.dumps(sources), now, now, now))

    def mark_unavailable(self, key: str, now: str):
        self.db.execute("UPDATE products SET available = 0, last_change = ? WHERE key = ?", (now, key))

    def product_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM products").fetchone()[0]

    # -- event log ----------------------------------------------------------------

    def add_event(self, now: str, kind: str, key: str, title: str, url: str | None,
                  price: str | None, alerted: bool):
        self.db.execute("INSERT INTO events (at, kind, key, title, url, price, alerted) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)", (now, kind, key, title, url, price, int(alerted)))

    def alerts_since(self, since: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events WHERE alerted = 1 AND at > ?",
                               (since,)).fetchone()[0]

    def prune_events(self, before: str):
        self.db.execute("DELETE FROM events WHERE at < ?", (before,))
