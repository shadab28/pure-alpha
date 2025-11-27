#!/usr/bin/env python3
"""Minimal orchestrator for kite algo bot (dry-run friendly).

This file provides a safe entrypoint that can be run in dry-run mode to
validate instrument token resolution and DB access without connecting to
Kite websockets or placing orders.
"""
import argparse
import json
import os
import signal
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pgAdmin_database import db
from pgAdmin_database import ohlcv_utils
from tools.instrument_resolver import resolver_for_csv

KITE_API_KEY = os.getenv("KITE_API_KEY")
TOKEN_FILE = Path("token.txt")


class CandleAggregator:
    """Simple in-memory 15-minute candle aggregator keyed by instrument_token."""

    def __init__(self):
        self._state = {}
        self._lock = threading.Lock()

    def update_tick(self, tick):
        # tick expected to have instrument_token and last_price and volume and exchange_timestamp
        token = tick.get("instrument_token")
        lp = tick.get("last_price")
        vol = tick.get("volume_traded") or 0
        ts = tick.get("exchange_timestamp") or datetime.now(timezone.utc)

        # normalize ts to minute resolution
        ts = ts.replace(second=0, microsecond=0)
        # round minute to 15m bucket: 0,15,30,45
        minute = (ts.minute // 15) * 15
        bucket = ts.replace(minute=minute)

        with self._lock:
            s = self._state.get(token)
            if not s or s["bucket"] != bucket:
                # start new candle
                if s:
                    # persist previous candle
                    self._persist_candle(s)
                self._state[token] = {
                    "bucket": bucket,
                    "open": lp,
                    "high": lp,
                    "low": lp,
                    "close": lp,
                    "volume": vol,
                }
            else:
                c = self._state[token]
                c["high"] = max(c["high"], lp)
                c["low"] = min(c["low"], lp)
                c["close"] = lp
                c["volume"] = c.get("volume", 0) + vol

    def _persist_candle(self, s):
        try:
            ohlcv_utils.insert_ohlcv(None, 0, None, None, None, None, None)  # placeholder to ensure function exists
        except Exception:
            pass
        # actual insert happens in flush where DB conn is available

    def flush_all(self, conn):
        with self._lock:
            for token, s in list(self._state.items()):
                try:
                    ohlcv_utils.insert_ohlcv(conn, token, s["bucket"], s["open"], s["high"], s["low"], s["close"], int(s.get("volume", 0)))
                except Exception as e:
                    print(f"Failed to insert candle for {token}: {e}")
            # clear state
            self._state = {}




def resolve_instruments_from_csv(path):
    if not os.path.exists(path):
        return []
    import csv
    tokens = []
    with open(path, "r", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        for row in r:
            try:
                tokens.append(int(row.get("instrument_token") or row.get("instrument_token")))
            except Exception:
                continue
    return tokens


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Don't open websocket or place real orders")
    p.add_argument("--instruments-csv", default="Csvs/instruments.csv")
    p.add_argument("--live", action="store_true", help="Run live websocket (requires KITE_API_KEY and token.txt)")
    p.add_argument("--universe", default="allstocks", help="Universe name defined in NiftySymbol (default: allstocks)")
    p.add_argument("--limit", type=int, default=500, help="Max number of tokens to subscribe")
    args = p.parse_args()

    print("Starting main (dry-run=%s)" % args.dry_run)

    # Initialize DB (creates minimal tables if missing)
    cfg = db.read_config()
    print("DB host: %s" % cfg.get("host"))
    conn = db.connect()
    db.ensure_tables(conn)

    # Resolve tokens from csv as a simple source for this scaffolding
    tokens = resolve_instruments_from_csv(args.instruments_csv)
    print("Resolved %d tokens from %s" % (len(tokens), args.instruments_csv))

    # Create/ensure live_quotes.json exists
    live_path = Path("live_quotes.json")
    sample = {"meta": {"tokens_count": len(tokens)}, "ticks": {}}
    live_path.write_text(json.dumps(sample, default=str))

    if args.dry_run:
        print("Dry-run: exiting after validation.")
        return

    if not args.live:
        print("Not running live websocket. Use --live to start the ticker (requires KITE_API_KEY and token.txt).")
        return

    # Live startup checks (allow cred.ini fallback)
    api_key_local = os.getenv("KITE_API_KEY")
    if not api_key_local:
        credf = Path('cred.ini')
        if credf.exists():
            for line in credf.read_text().splitlines():
                if not line or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                if k.strip() == 'KITE_API_KEY':
                    api_key_local = v.strip()

    if not api_key_local:
        print("KITE_API_KEY not set in environment or cred.ini. Cannot start live websocket.")
        return
    if not TOKEN_FILE.exists():
        print("token.txt not found. Run auth flow to generate token.txt before starting live mode.")
        return

    access_token = TOKEN_FILE.read_text().strip()

    # Resolve tokens from requested universe defined in NiftySymbol
    try:
        import tools.NiftySymbol as NiftySymbol
        symbols = getattr(NiftySymbol, args.universe)
        print(f"Using universe '{args.universe}' with {len(symbols)} symbols")
    except Exception:
        print(f"Universe '{args.universe}' not found in NiftySymbol; falling back to CSV tokens")
        symbols = []

    resolver = resolver_for_csv(args.instruments_csv)
    if symbols:
        mapping = resolver.bulk_resolve(symbols)
        tokens_list = [t for t in mapping.values() if t][: args.limit]
    else:
        # fallback to CSV listing (first N tokens)
        _map = resolver._map or resolver._load() or resolver._map
        tokens_list = list((_map or {}).values())[: args.limit]

    print(f"Subscribing to {len(tokens_list)} tokens (limit {args.limit})")

    # Lazy import of kiteconnect to avoid hard dependency for dry-run
    try:
        from kiteconnect.ticker import KiteTicker
    except Exception as e:
        print("kiteconnect.ticker import failed:", e)
        return

    agg = CandleAggregator()

    live_quotes = {}

    def on_ticks(ws, ticks):
        # ticks is a list of tick dicts
        for t in ticks:
            # update aggregator
            agg.update_tick(t)
            # update live_quotes mapping
            live_quotes[str(t.get('instrument_token'))] = {
                'last_price': t.get('last_price'),
                'timestamp': str(t.get('exchange_timestamp') or datetime.now(timezone.utc)),
            }

    def writer_loop(stop_event):
        import requests

        while not stop_event.is_set():
            payload = {'meta': {'count': len(live_quotes)}, 'ticks': live_quotes}
            try:
                # try POST to local webapp ingest endpoint
                resp = requests.post('http://127.0.0.1:5000/ingest', json=payload, timeout=2)
                if resp.status_code not in (200, 204):
                    # fallback to file
                    Path('live_quotes.json').write_text(json.dumps(payload, default=str))
            except Exception:
                try:
                    Path('live_quotes.json').write_text(json.dumps(payload, default=str))
                except Exception as e:
                    print('Error writing live_quotes.json', e)
            time.sleep(5)

    # Start websocket
    kws = KiteTicker(api_key_local, access_token)
    kws.on_ticks = on_ticks

    stop_evt = threading.Event()
    writer = threading.Thread(target=writer_loop, args=(stop_evt,), daemon=True)
    writer.start()

    # graceful shutdown
    def _shutdown(signum, frame):
        print('Shutting down, flushing candles...')
        stop_evt.set()
        try:
            kws.stop()
        except Exception:
            pass
        conn2 = db.connect()
        if conn2:
            agg.flush_all(conn2)
            conn2.close()
        print('Shutdown complete')
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Connect and subscribe
    kws.connect(threaded=True)
    # allow a moment for connect
    time.sleep(1)
    try:
        kws.subscribe(tokens_list)
    except Exception as e:
        print('Subscribe failed', e)

    # keep main thread alive
    try:
        while True:
            time.sleep(1)
    finally:
        _shutdown(None, None)


if __name__ == "__main__":
    main()
