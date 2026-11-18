# Deploy and Host Restock Watch with Railway

Restock Watch watches online store pages you choose and posts to a Discord channel when a new product appears or a sold-out product is back in stock. It reads Shopify, Lightspeed, CrystalCommerce and Ecwid stores natively, plus any product page with schema.org data, and needs no code changes: you paste URLs into a variable.

## About Hosting Restock Watch

Restock Watch is a single small Python worker. Every few minutes it reads the pages listed in `WATCH_URLS`, compares each product with what it saw before, and sends Discord alerts for new products and restocks. What it has seen is kept in a SQLite file on a Railway volume, so redeploys never re-announce old products. The first read of a new page is silent: its products are recorded, then only changes are announced. It runs on Railway's Hobby plan, needs no database service and no public domain, and is configured entirely through service variables.

## Common Use Cases

- Get pinged the moment a hard-to-find product is back in stock at the stores you follow
- Run a restock-alert channel for your Discord community
- Follow new arrivals across many independent shops from one channel
- Watch a single product page and hear when it becomes available

## Dependencies for Restock Watch Hosting

- A Discord webhook URL (Channel settings → Integrations → Webhooks → New Webhook → Copy Webhook URL)
- A Railway volume mounted at `/data` (included in the template)

### Deployment Dependencies

- [Discord: Intro to Webhooks](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks)
- [Railway volumes](https://docs.railway.com/volumes)

### Implementation Details

**WATCH_URLS** takes one page per line: a collection, category, search or product page. Optional `[Section]` lines group URLs under a label and shared filters:

```
[Headphones] | include=wireless,noise cancelling | exclude=refurbished,case
https://store-one.example/collections/headphones
https://store-two.example/catalog/audio/123 | exclude=open box
[Kitchen]
https://store-three.example/collections/espresso
https://store-four.example/products/some-grinder
```

| Option | Meaning |
|---|---|
| `include=` | Comma-separated terms; a product is announced only if its title matches one of them. |
| `exclude=` | Comma-separated terms; a product whose title matches one is not announced. |
| `label=` | Text shown in front of the Discord alert (a `[Section]` sets it for the URLs below it). |
| `platform=` | Force a reader instead of auto-detection: `shopify`, `lightspeed`, `crystalcommerce`, `ecwid`, `jsonld`, `generic`. |
| `via=` | `proxy` sends this URL's requests through `PROXY_URL`; `relay` through `RELAY_URL`. |
| `impersonate=` | Send this URL's requests with the TLS fingerprint of a browser (`chrome`, `safari`, `firefox`, ...) instead of Python's. |

Terms are case-insensitive regular expressions, so plain words work. Include and exclude terms add up: `INCLUDE_KEYWORDS`/`EXCLUDE_KEYWORDS` + section + URL. Filters only choose what is announced; every product on a watched page is tracked.

**Variables**

| Variable | Default | Description |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | required | Discord webhook that receives the alerts. |
| `WATCH_URLS` | required | Pages to watch, one per line (syntax above). |
| `SCAN_INTERVAL_MINUTES` | `10` | Minutes between scans (minimum 5). |
| `INCLUDE_KEYWORDS` | | Terms every announced title must match one of, for all URLs. |
| `EXCLUDE_KEYWORDS` | | Terms that block an announcement, for all URLs. |
| `ALERT_SOLD_OUT_NEW` | `true` | Announce new products even when they are listed as sold out (you are told again when they are in stock). |
| `DISCORD_MENTION` | | Text put in front of every alert, e.g. `@everyone` or `<@&ROLE_ID>`. |
| `DISCORD_USERNAME` | `Restock Watch` | Name the webhook posts under. |
| `CURRENCY_SYMBOL` | `$` | Shown in front of prices. |
| `TZ` | `UTC` | Time zone for the daily heartbeat, e.g. `America/Toronto`. |
| `HEARTBEAT_HOUR` | `9` | Hour of the daily "still running" report with failing pages; `off` to disable. |
| `BLOCK_BACKOFF_MINUTES` | `30` | How long to leave a store alone after it refuses requests (HTTP 403/429). |
| `FETCH_WORKERS` | `4` | Stores read in parallel (one request at a time per store). |
| `HOST_PAUSE_SECONDS` | `1` | Pause between two requests to the same store. |
| `PROXY_URL` | | HTTP(S) proxy used by URLs marked `via=proxy`, e.g. `http://user:pass@host:port`. |
| `RELAY_URL` | | Fetch-API URL template used by URLs marked `via=relay`; `{url}` is replaced by the encoded page URL, `{raw_url}` by the page URL as is. |
| `RELAY_HEADERS` | | JSON object of headers sent to `RELAY_URL`, e.g. an API key. |
| `USER_AGENT` | a desktop browser | User-Agent of plain requests. |

**How pages are read**

| Platform | Detected by | What is read |
|---|---|---|
| Shopify | `/products.json` answers | The whole collection (up to 1,000 products) or one product; stock per variant. |
| Lightspeed eCom | `?format=json` answers | The listing (100 per page) or one product. |
| CrystalCommerce | page markup | The listing, following its pages (up to 5). |
| Ecwid | page markup | One listing page. |
| Any site with schema.org data | JSON-LD in the page | Every product described with an availability, typically a product page. |
| Anything else | fallback | Guessed product tiles; check the result first. |

When a reader sees a complete listing, a product that disappears from it is recorded as sold out, so its return is announced as a restock (many stores hide sold-out items instead of labelling them).

Check what a page gives before deploying (nothing is stored or sent):

```
pip install -r requirements.txt
python -m restock_watch check "https://store.example/collections/headphones | include=wireless"
```

`python -m restock_watch` runs the scanner in a loop (the template's start command); `python -m restock_watch once` runs one scan and exits, for a cron schedule. Run the tests with `pip install -r requirements-dev.txt && pytest`.

**Responsible use.** You choose the pages, so you are responsible for complying with each site's terms of use and with Railway's policies. Restock Watch is built to be gentle: one request at a time per store, a five-minute minimum between scans, and it backs off when a store refuses requests.

## Why Deploy Restock Watch on Railway?

Railway is a singular platform to deploy your infrastructure stack. Railway will host your infrastructure so you don't have to deal with configuration, while allowing you to vertically and horizontally scale it.

By deploying Restock Watch on Railway, you are one step closer to supporting a complete full-stack application with minimal burden. Host your servers, databases, AI agents, and more on Railway.
