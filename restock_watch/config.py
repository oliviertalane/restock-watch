"""Settings, read from environment variables (the Railway service variables)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MIN_SCAN_INTERVAL_MINUTES = 5

TRUE_WORDS = {"1", "true", "yes", "on"}
FALSE_WORDS = {"0", "false", "no", "off"}


class ConfigError(Exception):
    """A setting is missing or invalid. The message is shown to the user."""


@dataclass(frozen=True)
class Settings:
    webhook_url: str = ""
    watch_urls: str = ""
    scan_interval_minutes: int = 10
    include_keywords: str = ""
    exclude_keywords: str = ""
    alert_sold_out_new: bool = True
    timezone: str = "UTC"
    heartbeat_hour: int | None = 9
    data_dir: Path = Path("data")
    discord_username: str = "Restock Watch"
    discord_mention: str = ""
    currency_symbol: str = "$"
    user_agent: str = DEFAULT_USER_AGENT
    fetch_workers: int = 4
    host_pause_seconds: float = 1.0
    block_backoff_minutes: int = 30
    proxy_url: str = ""
    relay_url: str = ""
    relay_headers: dict = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        problems: list[str] = []
        warnings: list[str] = []

        def text(name, default=""):
            return (env.get(name) or default).strip()

        def integer(name, default, low=None, high=None):
            raw = text(name)
            if not raw:
                return default
            try:
                value = int(raw)
            except ValueError:
                problems.append(f"{name} must be a whole number, got {raw!r}")
                return default
            if (low is not None and value < low) or (high is not None and value > high):
                problems.append(f"{name} must be between {low} and {high}, got {value}")
                return default
            return value

        def number(name, default):
            raw = text(name)
            try:
                return float(raw) if raw else default
            except ValueError:
                problems.append(f"{name} must be a number, got {raw!r}")
                return default

        def boolean(name, default):
            raw = text(name).lower()
            if not raw:
                return default
            if raw in TRUE_WORDS:
                return True
            if raw in FALSE_WORDS:
                return False
            problems.append(f"{name} must be true or false, got {raw!r}")
            return default

        interval = integer("SCAN_INTERVAL_MINUTES", 10, low=1)
        if interval < MIN_SCAN_INTERVAL_MINUTES:
            warnings.append(f"SCAN_INTERVAL_MINUTES={interval} raised to {MIN_SCAN_INTERVAL_MINUTES} "
                            "(be gentle with the stores you watch)")
            interval = MIN_SCAN_INTERVAL_MINUTES

        timezone = text("TZ", "UTC")
        try:
            ZoneInfo(timezone)
        except Exception:
            problems.append(f"TZ must be a time zone name such as America/Toronto, got {timezone!r}")
            timezone = "UTC"

        raw_hour = text("HEARTBEAT_HOUR", "9").lower()
        heartbeat_hour = None if raw_hour in FALSE_WORDS | {"none", "-1"} else integer("HEARTBEAT_HOUR", 9, 0, 23)

        relay_url = text("RELAY_URL")
        if relay_url and "{url}" not in relay_url and "{raw_url}" not in relay_url:
            problems.append("RELAY_URL must contain {url} (percent-encoded target) or {raw_url} (target as is)")
        relay_headers = {}
        if text("RELAY_HEADERS"):
            try:
                relay_headers = json.loads(text("RELAY_HEADERS"))
                if not isinstance(relay_headers, dict):
                    raise ValueError
            except ValueError:
                problems.append('RELAY_HEADERS must be a JSON object, e.g. {"X-Api-Key": "..."}')
                relay_headers = {}

        data_dir = text("DATA_DIR") or text("RAILWAY_VOLUME_MOUNT_PATH")
        if not data_dir:
            data_dir = "data"
            if env.get("RAILWAY_ENVIRONMENT"):
                warnings.append("No volume attached: what has been seen is forgotten on every redeploy. "
                                "Attach a volume to this service (mount path /data).")

        settings = cls(
            webhook_url=text("DISCORD_WEBHOOK_URL"),
            watch_urls=env.get("WATCH_URLS") or "",
            scan_interval_minutes=interval,
            include_keywords=text("INCLUDE_KEYWORDS"),
            exclude_keywords=text("EXCLUDE_KEYWORDS"),
            alert_sold_out_new=boolean("ALERT_SOLD_OUT_NEW", True),
            timezone=timezone,
            heartbeat_hour=heartbeat_hour,
            data_dir=Path(data_dir),
            discord_username=text("DISCORD_USERNAME", "Restock Watch"),
            discord_mention=text("DISCORD_MENTION"),
            currency_symbol=env.get("CURRENCY_SYMBOL", "$"),
            user_agent=text("USER_AGENT", DEFAULT_USER_AGENT),
            fetch_workers=integer("FETCH_WORKERS", 4, 1, 16),
            host_pause_seconds=number("HOST_PAUSE_SECONDS", 1.0),
            block_backoff_minutes=integer("BLOCK_BACKOFF_MINUTES", 30, 0),
            proxy_url=text("PROXY_URL"),
            relay_url=relay_url,
            relay_headers=relay_headers,
            warnings=tuple(warnings),
        )
        if settings.webhook_url and not settings.webhook_url.startswith("https://"):
            problems.append("DISCORD_WEBHOOK_URL must be the https:// webhook URL copied from Discord")
        if problems:
            raise ConfigError("\n".join(f"- {p}" for p in problems))
        return settings
