import pytest

from restock_watch.http import BlockedError, Http, Route


class Session:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        status = self.statuses.pop(0)

        class Response:
            status_code = status

            def raise_for_status(self):
                if status >= 400:
                    raise RuntimeError(status)
        return Response()


@pytest.fixture
def http(monkeypatch):
    h = Http("UA", proxy_url="http://proxy.example:8080", relay_url="https://relay.example/?u={url}",
             relay_headers={"X-Key": "k"})
    monkeypatch.setattr("restock_watch.http.time.sleep", lambda s: None)
    return h


def use(monkeypatch, http, statuses):
    session = Session(statuses)
    monkeypatch.setattr(http, "_session", lambda impersonate: session)
    return session


def test_relay_wraps_the_full_target_url(monkeypatch, http):
    session = use(monkeypatch, http, [200])
    http.client(Route(via="relay")).get("https://s.example/a", params={"page": 2})
    url, kwargs = session.calls[0]
    assert url == "https://relay.example/?u=https%3A%2F%2Fs.example%2Fa%3Fpage%3D2"
    assert kwargs["headers"] == {"X-Key": "k"}


def test_proxy(monkeypatch, http):
    session = use(monkeypatch, http, [200])
    http.client(Route(via="proxy")).get("https://s.example/a")
    assert session.calls[0][1]["proxies"] == {"http": "http://proxy.example:8080", "https": "http://proxy.example:8080"}


def test_overload_is_retried_once_then_refusal_raises(monkeypatch, http):
    assert use(monkeypatch, http, [503, 200]) and http.client().get("https://s.example/a")
    use(monkeypatch, http, [429, 429])
    with pytest.raises(BlockedError):
        http.client().get("https://s.example/a")
    use(monkeypatch, http, [403])
    with pytest.raises(BlockedError):
        http.client().get("https://s.example/a")


def test_route_needs_its_variable():
    with pytest.raises(ValueError, match="PROXY_URL"):
        Http("UA").client(Route(via="proxy"))
