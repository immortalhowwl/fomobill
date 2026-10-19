# Setup

## Local research mode

Python 3.10+ and its standard library are sufficient. No pip install is needed.

```sh
HOST=127.0.0.1 PORT=8080 DATA_DIR="$PWD/.local-data" FOMOBILL_MONITOR_ENABLED=0 python3 app.py
```

Visit `/workspace/` for the current bundled workspace, `/` for the bundled homepage, and `/legacy/` for the previous interface where available. Use `/health` for process health, not proof of provider or Telegram delivery.

`.env.example` documents the variables; it is not loaded automatically. Export values in your shell or configure them in your hosting platform. Never commit real credentials or runtime data.

| Variable | Purpose |
| --- | --- |
| `HOST`, `PORT` | Bind address and HTTP port |
| `DATA_DIR` | Persistent private SQLite stores, ownership key and worker lock |
| `ALLOWED_HOSTS` | Comma-separated hostnames, without schemes or paths |
| `RAILWAY_PUBLIC_DOMAIN` | Additional allowed platform hostname, if supplied |
| `FOMOBILL_MONITOR_ENABLED` | `0` disables background collection and bot startup; runtime defaults to `1` |
| `FOMOBILL_TELEGRAM_BOT_TOKEN` | Secret for your own separately configured bot |
| `FOMOBILL_TELEGRAM_USERNAME` | Expected bot username |
| `RECEIPTS_DIR` | Optional local QA output; leave unset in production |

## Monitoring and Telegram

Configure your own bot credentials in the environment, then set `FOMOBILL_MONITOR_ENABLED=1`. Never run another instance with the production bot token: Telegram polling must have exactly one owner globally. The local storage lock prevents duplicate workers for one data directory; it cannot coordinate separate hosts or volumes.

Start with an empty private data directory. Wallet monitoring requires an explicit watch action. Pairing does not imply a subscription: confirm `/subscribe` in Telegram; participant-sale alerts require their own preference. `/help` explains the commands.

Public providers can throttle or fail. Empty, partial and unavailable coverage are distinct. Lookups and bounded collection are not complete chain indexing.

## Containers

```sh
docker build -t fomobill .
docker run --rm -p 8080:8080 -e FOMOBILL_MONITOR_ENABLED=0 -v fomobill-data:/data fomobill
```

These are optional Docker instructions, not a claim that a container was executed in this release check. Configure a TLS reverse proxy and explicit allowed hostnames for public hosting. Keep `/data` private and persistent. Back up SQLite with its backup API and preserve the ownership key when maintaining an existing instance; do not publish that backup.

## Tests

```sh
FOMOBILL_MONITOR_ENABLED=0 FOMOBILL_TELEGRAM_BOT_TOKEN= python3 -m unittest discover -v
```

The suite uses temporary stores, test doubles and archived public-chain receipts. It includes HTTP/session isolation, consent, notification formatting and transport, decoder history, pagination and image packaging. It does not send real Telegram messages or prove current provider availability. Browser harnesses from development are not required for the Python suite and are not included in this export.

## State and hosting

This is a source export, not a subscription migration or automatic deployment pipeline. Browser cookies and bookmarks are origin-specific. Keep the production worker and its storage untouched when trying this package. The separately maintained `fomobill.fun` frontend is outside this repository; the existing backend origin is `https://api.fomobill.fun`.
