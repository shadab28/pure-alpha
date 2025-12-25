#!/usr/bin/env python3
"""Download daily OHLCV for the last N years and upsert into Postgres `ohlcv_data`.

This script follows the same DB schema and credential loading conventions as
`Core_files/fetch_ohlcv.py` so it can run independently. It will create the
ohlcv_data table if missing and then upsert daily rows for the configured
universe.

Usage (from repo root):
  PYTHONPATH=. python3 Core_files/download_daily_2y_to_db.py --years 2 --workers 6

Credentials:
- Looks first in `Core_files/token.txt` for JSON {"api_key":..., "access_token":...}
- Then falls back to env vars KITE_API_KEY and KITE_ACCESS_TOKEN

Note: this file only creates the downloader. Run it after exporting your
Kite credentials and ensuring Postgres is reachable (see pgAdmin_database/config.yaml).
"""
import os
import sys
import json
import time
from datetime import datetime, timedelta
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pgAdmin_database.db_connection import pg_cursor
from kiteconnect import KiteConnect
import yaml


def load_local_env():
    here = os.path.dirname(__file__)
    candidates = [
        os.path.join(here, '.env'),
        os.path.join(os.path.dirname(here), '.env'),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k, v)
        except Exception:
            pass


def load_token(path):
    try:
        with open(path, 'r') as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {'access_token': raw}


def load_params(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def load_nifty_module():
    import importlib.util
    module_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Support Files', 'NiftySymbol.py')
    spec = importlib.util.spec_from_file_location('nifty_symbols', module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch_candles(kite, instrument_token, from_date, to_date, interval):
    return kite.historical_data(instrument_token, from_date, to_date, interval, continuous=False)


def upsert_rows(cur, rows, symbol, timeframe):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='ohlcv_data'")
    existing = [c[0] for c in cur.fetchall()]

    col_map = {
        'symbol': 'symbol' if 'symbol' in existing else ('stockname' if 'stockname' in existing else None),
        'timeframe': 'timeframe' if 'timeframe' in existing else None,
        'ts': 'ts' if 'ts' in existing else ('candle_stock' if 'candle_stock' in existing else None),
        'open': 'open' if 'open' in existing else None,
        'high': 'high' if 'high' in existing else None,
        'low': 'low' if 'low' in existing else None,
        'close': 'close' if 'close' in existing else None,
        'volume': 'volume' if 'volume' in existing else None,
    }

    if not col_map['symbol'] or not col_map['ts']:
        raise RuntimeError('ohlcv_data table missing expected symbol/ts columns: ' + ','.join(existing))

    insert_cols = [col_map[k] for k in ['symbol', 'timeframe', 'ts', 'open', 'high', 'low', 'close', 'volume'] if col_map[k]]
    placeholders = ','.join(['%s'] * len(insert_cols))
    insert_list = ','.join(insert_cols)

    conflict_cols = [col_map['symbol'], col_map['timeframe'], col_map['ts']] if col_map['timeframe'] else [col_map['symbol'], col_map['ts']]
    conflict = ','.join(conflict_cols)

    updates = []
    for k in ['open', 'high', 'low', 'close', 'volume']:
        if col_map[k]:
            updates.append(f"{col_map[k]} = EXCLUDED.{col_map[k]}")
    update_sql = ', '.join(updates) if updates else 'DO NOTHING'

    sql = f"INSERT INTO ohlcv_data({insert_list}) VALUES ({placeholders}) ON CONFLICT ({conflict}) DO UPDATE SET {update_sql}"

    for r in rows:
        ts = r.get('date') or r.get('timestamp') or r.get('ts')
        if isinstance(ts, str):
            try:
                ts_val = datetime.fromisoformat(ts)
            except Exception:
                try:
                    ts_val = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
                except Exception:
                    ts_val = ts
        else:
            ts_val = ts

        # build final_vals according to insert_cols order
        final_vals = []
        for col in insert_cols:
            logical = None
            for k, v in col_map.items():
                if v == col:
                    logical = k
                    break
            if logical == 'symbol':
                final_vals.append(symbol)
            elif logical == 'timeframe':
                final_vals.append(timeframe)
            elif logical == 'ts':
                final_vals.append(ts_val)
            else:
                final_vals.append(r.get(logical))
        cur.execute(sql, tuple(final_vals))


def main():
    load_local_env()
    parser = argparse.ArgumentParser(description='Download daily OHLCV for N years and upsert to Postgres')
    parser.add_argument('--years', type=int, default=2, help='Number of years to fetch (default 2)')
    parser.add_argument('--workers', type=int, default=6, help='Number of parallel workers')
    parser.add_argument('--universe', type=str, default=None, help='Universe name from Support Files/NiftySymbol.py (overrides param.yaml)')
    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    token_path = os.path.join(os.path.dirname(__file__), 'token.txt')
    params = load_params(os.path.join(repo_root, 'Support Files', 'param.yaml'))
    universe_name = args.universe or params.get('universe_list', 'allstocks')

    token = load_token(token_path)
    api_key = token.get('api_key') or os.getenv('KITE_API_KEY') or os.getenv('KITE_APIKEY')
    access_token = token.get('access_token') or os.getenv('KITE_ACCESS_TOKEN')
    if not access_token:
        print('Missing access_token. Put it in Core_files/token.txt or set KITE_ACCESS_TOKEN env.')
        return
    if not api_key:
        print('Missing api_key. Provide in token.txt as JSON {"api_key": ..., "access_token": ...} or set KITE_API_KEY env.')
        return

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    try:
        _ = kite.profile()
    except Exception as e:
        print('Kite access token validation failed:', e)
        return

    mod = load_nifty_module()
    symbols = getattr(mod, universe_name, None)
    if not symbols:
        print('Universe not found:', universe_name)
        return

    # Create table if missing
    try:
        with pg_cursor(dict_rows=False) as (cur, _):
            cur.execute(
                "CREATE TABLE IF NOT EXISTS ohlcv_data ("
                "stockname TEXT NOT NULL, timeframe TEXT NOT NULL, candle_stock TIMESTAMP WITHOUT TIME ZONE NOT NULL, "
                "open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, volume BIGINT DEFAULT 0, "
                "PRIMARY KEY (stockname, timeframe, candle_stock) )"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uniq_ohlcv_stockname_timeframe_candle_stock ON ohlcv_data(stockname, timeframe, candle_stock)"
            )
    except Exception as e:
        print('Preflight DB setup failed:', e)
        return

    # build instrument token map
    print('Downloading instruments list')
    instruments = kite.instruments()
    token_map = {}
    for inst in instruments:
        tsym = inst.get('tradingsymbol')
        tok = inst.get('instrument_token')
        exch = (inst.get('exchange') or '').upper()
        if not tsym or tok is None:
            continue
        prev = token_map.get(tsym)
        if prev is None or exch == 'NSE':
            token_map[tsym] = tok

    api_semaphore = threading.Semaphore(2)

    def _call_historical(kite, instr, from_dt, to_dt, kite_tf='day'):
        fstr = from_dt.date().isoformat() if isinstance(from_dt, datetime) else str(from_dt)
        tstr = to_dt.date().isoformat() if isinstance(to_dt, datetime) else str(to_dt)
        with api_semaphore:
            time.sleep(0.05)
            return fetch_candles(kite, instr, fstr, tstr, kite_tf)

    def process_symbol(s: str):
        with pg_cursor(dict_rows=False) as (cur, conn):
            instr = token_map.get(s)
            if not instr:
                return f'Instrument token not found for {s}'

            now_dt = datetime.utcnow()
            from_dt = now_dt - timedelta(days=args.years * 365)
            try:
                candles = _call_historical(kite, instr, from_dt, now_dt, 'day')
            except Exception as e:
                return f'Failed to fetch for {s}: {e}'

            if not candles:
                return f'No daily candles for {s}'

            # Kite returns ascending by time; upsert all
            try:
                upsert_rows(cur, candles, s, '1d')
            except Exception as e:
                return f'Upsert failed for {s}: {e}'
            return f'Upserted {len(candles)} daily rows for {s}'

    workers = min(max(1, args.workers), len(symbols))
    print(f'Starting {workers} workers for {len(symbols)} symbols')
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_symbol, s): s for s in symbols}
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = f'Error for {s}: {e}'
            print(res)


if __name__ == '__main__':
    main()
