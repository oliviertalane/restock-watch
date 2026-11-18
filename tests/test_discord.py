from restock_watch import discord
from restock_watch.discord import Discord, fit
from restock_watch.engine import FirstRead
from restock_watch.models import FetchResult, Product
from restock_watch.watchlist import parse_watchlist


class Response:
    def __init__(self, status=204, body=None):
        self.status_code = status
        self.body = body or {}

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def capture(monkeypatch, answers=None):
    sent = []
    answers = list(answers or [])

    def post(url, json, timeout):
        sent.append(json)
        return answers.pop(0) if answers else Response()
    monkeypatch.setattr(discord.requests, "post", post)
    monkeypatch.setattr(discord.time, "sleep", lambda s: None)
    return sent


def product(i, available=True):
    return Product(key=f"k{i}", title=f"Item {i}", url=f"https://s.example/p/{i}", available=available,
                   price="9.99", image="https://cdn.example/i.jpg")


def test_products_are_split_in_messages_of_ten(monkeypatch):
    sent = capture(monkeypatch)
    hook = Discord("https://discord.example/hook", username="Bot", mention="<@&1>")
    hook.products([product(i, available=i % 2 == 0) for i in range(12)], "new", "Audio")
    assert [len(m["embeds"]) for m in sent] == [10, 2]
    assert sent[0]["content"] == "<@&1> 🚨 **[Audio] 12 new products**" and "content" not in sent[1]
    assert all(m["username"] == "Bot" for m in sent)
    first = sent[0]["embeds"][0]
    assert first["url"] == "https://s.example/p/0" and first["thumbnail"]["url"] == "https://cdn.example/i.jpg"
    assert "$9.99" in first["description"] and "In stock" in first["description"]
    in_stock = [("In stock" in e["description"]) for m in sent for e in m["embeds"]]
    assert in_stock == sorted(in_stock, reverse=True)         # in-stock items first


def test_rate_limit_is_retried_once(monkeypatch):
    sent = capture(monkeypatch, [Response(429, {"retry_after": 0.5}), Response(204)])
    assert Discord("https://discord.example/hook").text("hi")
    assert len(sent) == 2


def test_without_webhook_nothing_is_posted(monkeypatch):
    sent = capture(monkeypatch)
    assert Discord("").text("hi")
    assert sent == []


def test_first_read_summary(monkeypatch):
    sent = capture(monkeypatch)
    watch = parse_watchlist("https://s.example/collections/audio | include=box")[0]
    result = FetchResult([product(1), product(2, available=False)], platform="shopify")
    Discord("https://discord.example/hook").first_reads(
        [FirstRead(watch, result, matching=1)], [(watch, "HTTP 404")])
    content = sent[0]["content"]
    assert "Now watching 1 new page" in content and "Shopify, 2 products (1 in stock, 1 match your filters)" in content
    assert "Could not read 1 page" in content and "HTTP 404" in content


def test_fit_truncates_with_a_note():
    text = fit([f"line {i} " + "x" * 50 for i in range(100)], limit=300)
    assert len(text) <= 300 and text.endswith("more")
