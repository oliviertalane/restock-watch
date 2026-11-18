from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from restock_watch.config import Settings
from restock_watch.engine import run_scan
from restock_watch.models import FetchResult, Product
from restock_watch.store import Store
from restock_watch.watchlist import parse_watchlist

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
A = "https://a.example/collections/audio"
B = "https://b.example/catalog/audio"


class Notifier:
    def __init__(self):
        self.alerts = []        # (kind, label, [titles])
        self.first = []

    def products(self, products, kind, label=""):
        self.alerts.append((kind, label, sorted(p.title for p in products)))

    def first_reads(self, first_reads, failures):
        self.first.append(([f.watch.url for f in first_reads], [w.url for w, _ in failures]))

    def titles(self, kind):
        return sorted(t for k, _, ts in self.alerts if k == kind for t in ts)


def product(key, title=None, available=True):
    return Product(key=key, title=title or key, url=f"https://shop.example/{key}", available=available)


class World:
    """Runs scans against scripted read results."""

    def __init__(self, tmp_path, watch_text=f"{A}\n{B}", **settings):
        self.store = Store(tmp_path / "state.db")
        self.settings = Settings(**settings)
        self.watches = parse_watchlist(watch_text)
        self.notifier = Notifier()
        self.scans = 0

    def scan(self, results):
        self.notifier = Notifier()
        now = T0 + timedelta(minutes=10 * self.scans)
        self.scans += 1
        return run_scan(self.watches, self.store, self.notifier, self.settings,
                        read=lambda ws: {w.url: results[w.url] for w in ws if w.url in results}, now=now)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_first_read_is_silent_then_changes_are_announced(world):
    report = world.scan({A: FetchResult([product("p1"), product("p2", available=False)]),
                         B: FetchResult([product("p3")])})
    assert world.notifier.alerts == []
    assert report.held_back == 3                     # sold-out p2 too: ALERT_SOLD_OUT_NEW defaults to true
    assert world.notifier.first == [([A, B], [])]

    world.scan({A: FetchResult([product("p1"), product("p2"), product("p4")]),
                B: FetchResult([product("p3")])})
    assert world.notifier.titles("restock") == ["p2"]
    assert world.notifier.titles("new") == ["p4"]
    assert world.notifier.first == []


def test_unchanged_products_are_not_announced_again(world):
    results = {A: FetchResult([product("p1")]), B: FetchResult([])}
    world.scan(results)
    world.scan(results)
    world.scan(results)
    assert world.notifier.alerts == []


def test_sold_out_new_listing_follows_setting(tmp_path):
    for setting, expected in ((True, ["late"]), (False, [])):
        w = World(tmp_path / str(setting), alert_sold_out_new=setting)
        w.scan({A: FetchResult([product("p1")]), B: FetchResult([])})
        w.scan({A: FetchResult([product("p1"), product("late", available=False)]), B: FetchResult([])})
        assert w.notifier.titles("new") == expected


def test_filters_choose_what_is_announced_but_everything_is_tracked(tmp_path):
    w = World(tmp_path, watch_text=f"{A} | include=wireless | exclude=case")
    w.scan({A: FetchResult([])})
    w.scan({A: FetchResult([product("b1", "Wireless Earbuds"), product("s1", "Wireless Earbuds Case"),
                            product("m1", "Stand")])})
    assert w.notifier.titles("new") == ["Wireless Earbuds"]
    assert set(w.store.tracked()) == {"b1", "s1", "m1"}


def test_product_missing_from_complete_listing_comes_back_as_restock(world):
    world.scan({A: FetchResult([product("p1")], exhaustive=True), B: FetchResult([])})
    world.scan({A: FetchResult([], exhaustive=True), B: FetchResult([])})       # store hid it
    assert world.store.tracked()["p1"].available is False
    world.scan({A: FetchResult([product("p1")], exhaustive=True), B: FetchResult([])})
    assert world.notifier.titles("restock") == ["p1"]


def test_missing_from_partial_or_failed_read_is_left_alone(world):
    world.scan({A: FetchResult([product("p1")], exhaustive=True), B: FetchResult([product("p2")])})
    world.scan({A: FetchResult([], ok=False, error="timeout"),                  # failed
                B: FetchResult([])})                                            # one page only
    tracked = world.store.tracked()
    assert tracked["p1"].available and tracked["p2"].available


def test_new_url_is_silent_even_when_others_are_known(tmp_path):
    w = World(tmp_path, watch_text=A)
    w.scan({A: FetchResult([product("p1")])})
    w.watches = parse_watchlist(f"{A}\n{B}")                                    # B added later
    w.scan({A: FetchResult([product("p1"), product("shared")]),
            B: FetchResult([product("only-b"), product("shared")])})
    assert w.notifier.titles("new") == ["shared"]          # also on known A: announced
    assert w.notifier.first == [([B], [])]


def test_removed_then_readded_url_gets_a_silent_read_again(tmp_path):
    w = World(tmp_path, watch_text=f"{A}\n{B}")
    w.scan({A: FetchResult([]), B: FetchResult([])})
    w.watches = parse_watchlist(A)
    w.scan({A: FetchResult([])})
    w.watches = parse_watchlist(f"{A}\n{B}")
    w.scan({A: FetchResult([]), B: FetchResult([product("b-new")])})
    assert w.notifier.alerts == []


def test_first_failure_reported_once(world):
    world.scan({A: FetchResult([]), B: FetchResult(ok=False, error="HTTP 404")})
    assert world.notifier.first == [([A], [B])]
    world.scan({A: FetchResult([]), B: FetchResult(ok=False, error="HTTP 404")})
    assert world.notifier.first == []


def test_blocked_store_is_paused(world):
    world.settings = replace(world.settings, block_backoff_minutes=30)
    world.scan({A: FetchResult([]), B: FetchResult(ok=False, blocked=True, error="refused")})
    assert "b.example" in world.store.paused_hosts((T0 + timedelta(minutes=5)).isoformat())
    assert not world.store.paused_hosts((T0 + timedelta(minutes=31)).isoformat())


def test_labels_group_alerts(tmp_path):
    w = World(tmp_path, watch_text=f"[One]\n{A}\n[Two]\n{B}")
    w.scan({A: FetchResult([]), B: FetchResult([])})
    w.scan({A: FetchResult([product("a1")]), B: FetchResult([product("b1")])})
    assert sorted(w.notifier.alerts) == [("new", "One", ["a1"]), ("new", "Two", ["b1"])]
