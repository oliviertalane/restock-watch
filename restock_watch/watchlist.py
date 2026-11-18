"""Parse WATCH_URLS: the pages to watch, with optional labels, filters and routes.

    # lines starting with # are ignored
    [Headphones] | include=wireless,noise cancelling | exclude=refurbished,case
    https://store-a.example/collections/headphones
    https://store-b.example/catalog/audio/123 | exclude=open box
    [Kitchen]
    https://store-c.example/collections/espresso | via=proxy

A [Section] line sets the label shown in Discord and the options of the URLs
below it, until the next section. Options after a URL apply to that URL only.

  include=  comma-separated terms; a product is announced only if its title
            matches at least one of them (when any are set)
  exclude=  comma-separated terms; a product whose title matches one is not announced
  label=    text shown in front of the Discord alert
  platform= force a reader (shopify, lightspeed, crystalcommerce, ecwid, jsonld,
            generic) instead of auto-detection
  via=      proxy | relay (see http.py)
  impersonate=  browser fingerprint name (see http.py)

Terms are case-insensitive regular expressions, so plain words work. include
and exclude terms add up: INCLUDE_KEYWORDS / EXCLUDE_KEYWORDS (every URL) +
section + URL. Filters only decide what is announced; every product on a
watched page is tracked, so changing a filter never fakes a "new" alert.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace

from .config import ConfigError
from .http import Route
from .models import host_of
from .platforms import PLATFORM_NAMES

log = logging.getLogger(__name__)

OPTION_KEYS = ("include", "exclude", "label", "platform", "via", "impersonate")
VIA_VALUES = ("", "direct", "proxy", "relay")
# Several URLs on one line (a pasted single-line variable): split before each http(s)://,
# even with no separator left (a single-line input that drops the line breaks of a paste),
# except where it follows = ? & or % (a URL passed as a query parameter).
ENTRY_SPLIT = re.compile(r"\s*[,;]\s*(?=https?://)|\s+(?=https?://)|(?<=[^\s=?&%])(?=https?://)")


@dataclass(frozen=True)
class TitleFilter:
    include: tuple[re.Pattern, ...] = ()
    exclude: tuple[re.Pattern, ...] = ()

    def matches(self, title: str) -> bool:
        if any(p.search(title) for p in self.exclude):
            return False
        return not self.include or any(p.search(title) for p in self.include)


@dataclass(frozen=True)
class Watch:
    url: str
    label: str = ""
    filter: TitleFilter = field(default_factory=TitleFilter)
    platform: str = ""      # "" = auto-detect
    route: Route = field(default_factory=Route)

    @property
    def host(self) -> str:
        return host_of(self.url)


def split_terms(value: str) -> list[str]:
    """Split on commas, except inside (), [] or {} so that a pattern such as
    \\d{1,3} stays whole. A backslash escapes the next character."""
    terms, current, depth, escaped = [], [], 0, False
    for ch in value:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            terms.append("".join(current))
            current = []
            continue
        current.append(ch)
    terms.append("".join(current))
    return [t.strip() for t in terms if t.strip()]


def _entries(text: str):
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for entry in ENTRY_SPLIT.split(line):
            if entry.strip():
                yield lineno, entry.strip()


def parse_watchlist(text: str, include_keywords: str = "", exclude_keywords: str = "") -> list[Watch]:
    problems: list[str] = []

    def patterns(value: str, where: str) -> tuple[re.Pattern, ...]:
        compiled = []
        for term in split_terms(value):
            try:
                compiled.append(re.compile(term, re.IGNORECASE))
            except re.error as e:
                problems.append(f"{where}: {term!r} is not a valid pattern ({e})")
        return tuple(compiled)

    def options(parts: list[str], where: str) -> dict[str, str]:
        found = {}
        for part in parts:
            key, sep, value = part.partition("=")
            key, value = key.strip().lower(), value.strip()
            if not sep or key not in OPTION_KEYS:
                problems.append(f"{where}: unknown option {part!r} (use {', '.join(k + '=' for k in OPTION_KEYS)})")
            elif key == "platform" and value and value not in PLATFORM_NAMES:
                problems.append(f"{where}: platform must be one of {', '.join(PLATFORM_NAMES)}")
            elif key == "via" and value.lower() not in VIA_VALUES:
                problems.append(f"{where}: via must be proxy or relay")
            else:
                found[key] = value
        return found

    def apply(base: Watch, opts: dict[str, str], where: str) -> Watch:
        route = base.route
        if "via" in opts:
            route = replace(route, via="" if opts["via"].lower() == "direct" else opts["via"].lower())
        if "impersonate" in opts:
            route = replace(route, impersonate=opts["impersonate"].lower())
        return replace(
            base,
            label=opts.get("label", base.label),
            platform=opts.get("platform", base.platform),
            route=route,
            filter=TitleFilter(
                include=base.filter.include + patterns(opts.get("include", ""), where),
                exclude=base.filter.exclude + patterns(opts.get("exclude", ""), where),
            ),
        )

    everywhere = Watch(url="", filter=TitleFilter(
        include=patterns(include_keywords, "INCLUDE_KEYWORDS"),
        exclude=patterns(exclude_keywords, "EXCLUDE_KEYWORDS"),
    ))
    section = everywhere
    watches: list[Watch] = []
    seen: set[str] = set()

    for lineno, entry in _entries(text):
        where = f"WATCH_URLS line {lineno}"
        head, *option_parts = [p.strip() for p in entry.split("|")]
        opts = options(option_parts, where)
        if head.startswith("["):
            if not head.endswith("]"):
                problems.append(f"{where}: a section line looks like [Name] | option=value")
                continue
            section = apply(replace(everywhere, label=head[1:-1].strip()), opts, where)
        elif head.lower().startswith(("http://", "https://")):
            if head in seen:
                log.warning(f"{where}: {head} is listed twice; the first entry is used")
                continue
            seen.add(head)
            watches.append(apply(replace(section, url=head), opts, where))
        else:
            problems.append(f"{where}: expected a URL starting with https:// or a [Section] line, got {head[:60]!r}")

    if not watches and not problems:
        problems.append("WATCH_URLS is empty: add at least one store page to watch (one URL per line)")
    if problems:
        raise ConfigError("\n".join(f"- {p}" for p in problems))
    return watches
