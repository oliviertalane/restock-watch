"""Command line.

  python -m restock_watch            scan every SCAN_INTERVAL_MINUTES until stopped
  python -m restock_watch once       one scan, then exit (for a cron schedule)
  python -m restock_watch check      read every WATCH_URLS page and print what is found
  python -m restock_watch check URL  same for the given URL(s); options work too:
                                     "https://... | include=wireless"
`check` stores nothing and sends nothing.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from . import __version__
from .app import notifier_for, run, stop_event
from .config import ConfigError, Settings
from .engine import read_watch
from .http import Http
from .watchlist import parse_watchlist


def check(settings: Settings, urls: list[str], limit: int) -> int:
    text = "\n".join(urls) if urls else settings.watch_urls
    watches = parse_watchlist(text, settings.include_keywords, settings.exclude_keywords)
    http = Http(settings.user_agent, settings.proxy_url, settings.relay_url, settings.relay_headers)
    for watch in watches:
        result = read_watch(watch, None, http)
        products = result.products
        matching = [p for p in products if watch.filter.matches(p.title)]
        print(f"\n{watch.url}")
        state = "complete listing" if result.exhaustive else "partial view (one page)"
        print(f"  platform: {result.platform or '?'} · {len(products)} products · "
              f"{sum(p.available for p in products)} in stock · {len(matching)} match filters · {state}")
        if not result.ok:
            print(f"  ERROR: {result.error}")
        for p in products[:limit]:
            mark = "✓" if watch.filter.matches(p.title) else " "
            print(f"  [{'IN ' if p.available else 'OUT'}] {mark} {p.price or '-':>9}  {p.title[:70]}")
        if len(products) > limit:
            print(f"  ... {len(products) - limit} more (--limit)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="restock_watch", description="Restock Watch " + __version__)
    parser.add_argument("command", nargs="?", default="run", choices=("run", "once", "check"))
    parser.add_argument("urls", nargs="*", help="check: URLs to read instead of WATCH_URLS")
    parser.add_argument("--limit", type=int, default=25, help="check: products listed per URL")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Collect every configuration problem before reporting, so one message lists them all.
    problems: list[str] = []
    settings = watches = None
    try:
        settings = Settings.from_env()
    except ConfigError as e:
        problems.append(str(e))
    try:
        if args.command == "check":
            if settings:
                return check(settings, args.urls, args.limit)
        else:
            env = os.environ
            watches = parse_watchlist(env.get("WATCH_URLS") or "", env.get("INCLUDE_KEYWORDS") or "",
                                      env.get("EXCLUDE_KEYWORDS") or "")
    except ConfigError as e:
        problems.append(str(e))

    if problems:
        message = "Restock Watch can't start. Fix these service variables:\n" + "\n".join(problems)
        logging.getLogger("restock_watch").error(message)
        if args.command == "check":
            return 2
        webhook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
        if webhook.startswith("https://"):
            notifier_for(Settings(webhook_url=webhook)).text("⚠️ " + message)
        if args.command == "once":
            return 2
        # Exiting would make the platform restart the service in a loop. Wait
        # instead: changing a variable redeploys the service anyway.
        stop_event().wait()
        return 2

    return run(settings, watches, once=(args.command == "once"))


if __name__ == "__main__":
    sys.exit(main())
