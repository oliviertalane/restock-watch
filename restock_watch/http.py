"""HTTP access for the platform readers.

Every request gets one retry after a short pause on an overload answer
(429/5xx). A 403 or a repeated 429 raises BlockedError: the engine then
leaves that store alone for BLOCK_BACKOFF_MINUTES instead of insisting.

Per watched URL, requests can be routed differently (WATCH_URLS options):
  via=proxy          through the HTTP(S) proxy in PROXY_URL
  via=relay          through the fetch API in RELAY_URL, a URL template where
                     {url} is replaced by the percent-encoded target URL and
                     {raw_url} by the target URL as is
  impersonate=NAME   with curl_cffi using the TLS/HTTP fingerprint of browser
                     NAME (chrome, safari, firefox, edge, ...) instead of
                     python-requests
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests

RETRY_STATUSES = (429, 500, 502, 503, 504)
BLOCK_STATUSES = (403, 429)
RETRY_PAUSE_SECONDS = 5
TIMEOUT_SECONDS = 20


class BlockedError(Exception):
    """The site refused the request (HTTP 403, or 429 twice)."""


@dataclass(frozen=True)
class Route:
    via: str = ""           # "", "proxy" or "relay"
    impersonate: str = ""   # "" = python-requests, else a curl_cffi browser name


class Http:
    def __init__(self, user_agent: str, proxy_url: str = "", relay_url: str = "",
                 relay_headers: dict | None = None, timeout: float = TIMEOUT_SECONDS):
        self.user_agent = user_agent
        self.proxy_url = proxy_url
        self.relay_url = relay_url
        self.relay_headers = dict(relay_headers or {})
        self.timeout = timeout
        self._local = threading.local()     # sessions are not shared between threads

    def client(self, route: Route = Route()) -> "Client":
        if route.via == "proxy" and not self.proxy_url:
            raise ValueError("via=proxy needs the PROXY_URL variable")
        if route.via == "relay" and not self.relay_url:
            raise ValueError("via=relay needs the RELAY_URL variable")
        return Client(self, route)

    def _session(self, impersonate: str):
        sessions = self._local.__dict__.setdefault("sessions", {})
        if impersonate not in sessions:
            if impersonate:
                try:
                    from curl_cffi import requests as curl_requests
                except ImportError as e:
                    raise RuntimeError("impersonate= needs the curl_cffi package") from e
                sessions[impersonate] = curl_requests.Session(impersonate=impersonate)
            else:
                session = requests.Session()
                session.headers["User-Agent"] = self.user_agent
                sessions[impersonate] = session
        return sessions[impersonate]


class Client:
    """GET requests for one watched URL, following its Route."""

    def __init__(self, http: Http, route: Route):
        self.http = http
        self.route = route

    def get(self, url: str, params: dict | None = None):
        target = requests.Request("GET", url, params=params).prepare().url if params else url
        kwargs: dict = {"timeout": self.http.timeout}
        request_url = target
        if self.route.via == "relay":
            request_url = (self.http.relay_url.replace("{raw_url}", target)
                           .replace("{url}", quote(target, safe="")))
            kwargs["headers"] = dict(self.http.relay_headers)
        elif self.route.via == "proxy":
            kwargs["proxies"] = {"http": self.http.proxy_url, "https": self.http.proxy_url}

        session = self.http._session(self.route.impersonate)
        response = session.get(request_url, **kwargs)
        if response.status_code in RETRY_STATUSES:
            time.sleep(RETRY_PAUSE_SECONDS)
            response = session.get(request_url, **kwargs)
        if response.status_code in BLOCK_STATUSES:
            raise BlockedError(f"HTTP {response.status_code}")
        response.raise_for_status()
        return response
