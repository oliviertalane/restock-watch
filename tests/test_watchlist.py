import pytest

from restock_watch.config import ConfigError
from restock_watch.watchlist import parse_watchlist, split_terms


def test_plain_list():
    watches = parse_watchlist("""
        # my stores
        https://a.example/collections/x
        https://b.example/catalog/y
    """)
    assert [w.url for w in watches] == ["https://a.example/collections/x", "https://b.example/catalog/y"]
    assert watches[0].label == ""
    assert watches[0].filter.matches("anything")


def test_sections_and_options_add_up():
    watches = parse_watchlist("""
        [Audio] | include=wireless,noise cancelling | exclude=case
        https://a.example/collections/audio
        https://b.example/audio | exclude=refurbished | label=B audio
        [Kitchen]
        https://c.example/kitchen | platform=shopify | via=proxy | impersonate=Safari
    """, include_keywords="", exclude_keywords="damaged")
    a, b, c = watches
    assert a.label == "Audio" and b.label == "B audio" and c.label == "Kitchen"
    assert a.filter.matches("Super Wireless Earbuds")
    assert not a.filter.matches("Stand")                    # include not matched
    assert not a.filter.matches("Wireless Case")             # section exclude
    assert not b.filter.matches("Refurbished Wireless Earbuds")       # URL exclude
    assert a.filter.matches("Refurbished Wireless Earbuds")           # ...only on that URL
    assert not c.filter.matches("Damaged box")                # global exclude
    assert c.filter.matches("Kettle")                     # new section: no include
    assert c.platform == "shopify" and c.route.via == "proxy" and c.route.impersonate == "safari"


def test_split_terms_keeps_regex_quantifiers():
    assert split_terms(r"\b\d{2,3}\s*ml\b, case ,(a|b),x\,y") == [
        r"\b\d{2,3}\s*ml\b", "case", "(a|b)", r"x\,y"]


def test_single_line_with_several_urls():
    watches = parse_watchlist("https://a.example/x, https://b.example/y https://c.example/z")
    assert [w.host for w in watches] == ["a.example", "b.example", "c.example"]


def test_www_is_ignored_in_host():
    assert parse_watchlist("https://www.a.example/x")[0].host == "a.example"


def test_duplicates_are_skipped():
    assert len(parse_watchlist("https://a.example/x\nhttps://a.example/x")) == 1


def test_all_problems_reported_at_once():
    with pytest.raises(ConfigError) as error:
        parse_watchlist("""
            a.example/no-scheme
            https://a.example/x | colour=red
            https://b.example/y | include=(unclosed
            https://c.example/z | platform=magento
            https://d.example/z | via=tor
        """)
    message = str(error.value)
    for part in ("line 2", "line 3", "line 4", "line 5", "line 6"):
        assert part in message


def test_empty_is_an_error():
    with pytest.raises(ConfigError, match="empty"):
        parse_watchlist("  \n # nothing\n")
