
# kite-algo-bot
Make bot for automated process

## Credentials

Create a `.env` file at the project root with the following keys (or set the env vars in your shell):

```
KITE_API_KEY=your_api_key_here
# pure-alpha — KiteConnect algo bot (scaffold)

This repository is a development scaffold for an automated trading bot using Zerodha's Kite Connect.
It provides a safe dry-run orchestrator, a minimal momentum strategy, Postgres helpers, a small Flask web UI,
and authentication helpers to generate and persist Kite access tokens.

WARNING: this code may place live orders. Use only in a safe test environment and review thoroughly before
connecting to a live account.

## Quick start (development)

1) Create and activate a virtualenv

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2) Install dependencies

```bash
pip install -r requirements.txt
pip install autobahn twisted pyOpenSSL requests
```

3) Credentials

Place credentials in `cred.ini` (ignored by git) or export as environment variables:

```ini
# cred.ini
KITE_API_KEY=your_api_key
KITE_API_SECRET=your_api_secret
```

Generate an access token via Zerodha web flow. After you login and receive a `request_token` in the redirect,
exchange it and persist the access token:

```bash
# either set env vars first, or the script will read cred.ini
python3 auth.py --request-token <request_token>
```

This writes `token.txt` and validates the token using `profile()`.

4) Validate (dry-run)

```bash
python3 main.py --dry-run
```

5) Run live (after `token.txt` present)

```bash
python3 main.py --live --universe allstocks --limit 200
```

6) Web UI

```bash
python3 app.py
# Visit http://127.0.0.1:5000/live_quotes
```

## Key files

- `main.py` — orchestrator, manages subscription, aggregates 15m candles, persists to Postgres, writes `live_quotes.json` and posts to webapp (if configured).
- `auth.py` — helper to obtain and persist Kite access token (`token.txt`) using `KiteConnect.generate_session`.
- `pgAdmin_database/` — DB helpers and `config.yaml` for local Postgres.
- `NiftySymbol.py` — predefined universes (e.g. `allstocks`).
- `tools/instrument_resolver.py` — resolves `tradingsymbol` -> `instrument_token` from `Csvs/instruments.csv`.
- `scripts/run_all_momentum.py` — scan an entire universe with the momentum scanner.

## Notes & troubleshooting

- The live websocket uses Twisted/Autobahn. Run `main.py` in a dedicated terminal (not an IDE embedded terminal) to avoid reactor signal issues.
- If the process terminates immediately when attempting to connect, capture the terminal log to inspect the stacktrace:

```bash
python3 main.py --live --universe allstocks --limit 5 2>&1 | tee main-live.log
```

- For production use, add robust logging, batching DB writes, connection pooling, and tests.

## Gitignore and secrets

This repo ignores `token.txt`, `cred.ini`, `.env*`, `live_quotes.json`, and common virtualenv files. Ensure secrets do not get committed.

## Disclaimer
Educational only. Use at your own risk.
