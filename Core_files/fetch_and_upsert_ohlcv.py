#!/usr/bin/env python3
"""Fetch OHLCV from Kite and upsert into Postgres `ohlcv_data`.

Script behavior:
- Loads `Core_files/token.txt` for access_token and api_key.
- Reads `Support Files/param.yaml` for `universe_list` and `timeframe`.
- Loads universe symbols from `Support Files/NiftySymbol.py`.
- Uses KiteConnect.historical_data to fetch candles for each symbol's instrument_token.
- Upserts rows into `ohlcv_data` table (assumes columns: symbol, timeframe, ts, open, high, low, close, volume).

Run with PYTHONPATH=. to ensure local packages importable.
Example:
  PYTHONPATH=. python Core_files/fetch_and_upsert_ohlcv.py --days 200
"""
import os
import json
import time
from datetime import datetime, timedelta
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import yaml

from pgAdmin_database.db_connection import pg_cursor

from kiteconnect import KiteConnect


def load_token(path):
    with open(path) as f:
        return json.load(f)


def load_params(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_nifty_module():
    import importlib.util
    module_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Support Files', 'NiftySymbol.py')
    spec = importlib.util.spec_from_file_location('nifty_symbols', module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_instrument_token(kite, tradingsymbol):
    # kite.instruments() returns a list; cache outside for efficiency if needed
    for inst in kite.instruments():
        # inst fields differ; typical keys: tradingsymbol, instrument_token, exchange
        if inst.get('tradingsymbol') == tradingsymbol:
            return inst.get('instrument_token')
    return None


def fetch_candles(kite, instrument_token, from_date, to_date, interval):
    # interval: 'minute', '15minute', 'day' etc supported by kite.historical_data
    # kite.historical_data expects instrument_token (int), from_date, to_date, interval, continuous
    return kite.historical_data(instrument_token, from_date, to_date, interval, continuous=False)


def upsert_rows(cur, rows, symbol, timeframe):
    # Detect column names in existing ohlcv_data table
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='ohlcv_data'")
    existing = [c[0] for c in cur.fetchall()]

    # Map expected logical names to actual columns
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
        raise RuntimeError('ohlcv_data table does not have expected symbol/ts columns: ' + ','.join(existing))

    # construct SQL dynamically
    insert_cols = [col_map[k] for k in ['symbol', 'timeframe', 'ts', 'open', 'high', 'low', 'close', 'volume'] if col_map[k]]
    placeholders = ','.join(['%s'] * len(insert_cols))
    insert_list = ','.join(insert_cols)

    # conflict target: use (symbol, timeframe, ts) mapped
    conflict_cols = [col_map['symbol'], col_map['timeframe'], col_map['ts']] if col_map['timeframe'] else [col_map['symbol'], col_map['ts']]
    conflict = ','.join(conflict_cols)

    # build update set for numeric cols
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
                # try common kite format 'YYYY-MM-DD HH:MM:SS'
                try:
                    ts_val = datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
                except Exception:
                    ts_val = ts
        else:
            ts_val = ts

        vals = []
        for k in ['symbol', 'timeframe', 'ts', 'open', 'high', 'low', 'close', 'volume']:
            if k == 'symbol':
                vals.append(symbol)
            elif k == 'timeframe':
                vals.append(timeframe)
            elif k == 'ts':
                vals.append(ts_val)
            else:
                vals.append(r.get(k))

        # filter vals to match insert_cols order
        # insert_cols corresponds to the keys order above but filtered for actual columns
        # so map accordingly
        final_vals = []
        for col in insert_cols:
            # find logical key for this col
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=200)
    parser.add_argument('--rows', type=int, default=None, help='Approx number of rows per symbol to fetch (overrides --days if set)')
    parser.add_argument('--timeframes', type=str, default=None, help='Comma-separated list of timeframes (e.g. 15m,1d). If omitted, uses param.yaml timeframe.')
    parser.add_argument('--workers', type=int, default=4, help='Number of parallel worker threads for symbols')
    parser.add_argument('--since-now', action='store_true', help='Compute from time relative to now to fetch the most recent rows')
    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    token_path = os.path.join(os.path.dirname(__file__), 'token.txt')
    params = load_params(os.path.join(repo_root, 'Support Files', 'param.yaml'))
    universe_name = params.get('universe_list', 'allstocks')
    # support multiple timeframes; CLI overrides param.yaml
    # Only allow two canonical timeframes for this script: '15m' and '1d'
    def normalize_tf_strict(tf: str) -> tuple[str, str]:
        """Return (label, kite_interval) for allowed timeframes.

        label is what we store in DB ('15m' or '1d').
        kite_interval is what KiteConnect.historical_data expects ('15minute' or 'day').
        """
        t = tf.strip().lower()
        if t in ('15m', '15minute', '15min', '15mins'):
            return '15m', '15minute'
        if t in ('1d', 'day', 'daily'):
            return '1d', 'day'
        raise ValueError("Only '15m' and '1d' timeframes are supported")

    if args.timeframes:
        pairs = [normalize_tf_strict(x) for x in args.timeframes.split(',') if x.strip()]
    else:
        raw_tf = params.get('timeframe', '15minute')
        # if param.yaml provided '15minute' or 'day', map accordingly
        if isinstance(raw_tf, (list, tuple)):
            pairs = [normalize_tf_strict(x) for x in raw_tf]
        else:
            pairs = [normalize_tf_strict(raw_tf)]

    # Expand pairs into two parallel lists for labels and kite intervals
    timeframe_labels = [p[0] for p in pairs]
    kite_timeframes = [p[1] for p in pairs]

    token = load_token(token_path)
    api_key = token.get('api_key')
    access_token = token.get('access_token')
    if not api_key or not access_token:
        print('Missing api_key or access_token in token.txt')
        return

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    mod = load_nifty_module()
    symbols = getattr(mod, universe_name, None)
    if not symbols:
        print('Universe not found:', universe_name)
        return

    # compute date range if rows not requested
    to_date = datetime.utcnow().date()
    from_date = to_date - timedelta(days=args.days)

    # We'll parallelize symbol processing to speed up the run. Each worker will open its own DB connection.
    print('Downloading instruments list (this may take a while)')
    instruments = kite.instruments()
    token_map = {inst.get('tradingsymbol'): inst.get('instrument_token') for inst in instruments}

    api_semaphore = threading.Semaphore(2)  # limit concurrent Kite API calls

    def _call_historical(kite, instr, from_dt, to_dt, kite_tf):
        # Format dates per Kite expectations: intraday needs 'YYYY-MM-DD HH:MM:SS', daily can use date
        if kite_tf == 'day':
            fstr = from_dt.date().isoformat() if isinstance(from_dt, datetime) else str(from_dt)
            tstr = to_dt.date().isoformat() if isinstance(to_dt, datetime) else str(to_dt)
        else:
            fstr = from_dt.strftime('%Y-%m-%d %H:%M:%S') if isinstance(from_dt, datetime) else str(from_dt)
            tstr = to_dt.strftime('%Y-%m-%d %H:%M:%S') if isinstance(to_dt, datetime) else str(to_dt)
        with api_semaphore:
            time.sleep(0.05)
            return fetch_candles(kite, instr, fstr, tstr, kite_tf)

    def process_symbol(s: str):
        """Process one symbol across all requested timeframes. This runs in a worker thread."""
        # Each thread opens its own DB cursor/connection
        with pg_cursor(dict_rows=False) as (cur, conn):
            # Ensure table exists (simple create if not exists)
            create_sql = (
                "CREATE TABLE IF NOT EXISTS ohlcv_data ("
                "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts TIMESTAMP NOT NULL, "
                "open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, volume BIGINT, "
                "PRIMARY KEY (symbol, timeframe, ts) )"
            )
            cur.execute(create_sql)

            instr = token_map.get(s)
            if not instr:
                return f'Instrument token not found for {s}'

            for label, kite_tf in zip(timeframe_labels, kite_timeframes):
                # target rows
                target = args.rows or args.days
                # determine end and start times based on now if requested
                now_dt = datetime.utcnow()
                # For intraday (15m) align end_dt to the current 15-minute boundary.
                if kite_tf == '15minute':
                    minute = (now_dt.minute // 15) * 15
                    end_dt = now_dt.replace(minute=minute, second=0, microsecond=0)
                else:
                    end_dt = now_dt
                # Estimate how many days/minutes to step back per request to gather target quickly
                if kite_tf == 'day':
                    # assume 250 trading days ~ 1 year; request a window proportional to target
                    est_days = max(1, int(target * 1.5))
                    start_dt = now_dt - timedelta(days=est_days)
                else:
                    # intraday bars per day ~ (6.5*60)/15 = 26 for 15m; estimate days needed
                    bars_per_day = int((6.5 * 60) / 15)
                    est_days = max(1, int((target / bars_per_day) * 1.5) + 1)
                    start_dt = now_dt - timedelta(days=est_days)

                all_candles = []
                attempts = 0
                while len(all_candles) < target and attempts < 4:
                    try:
                        c = _call_historical(kite, instr, start_dt, end_dt, kite_tf)
                    except Exception as e:
                        return f'Failed to fetch for {s} {label}: {e}'
                    if c:
                        all_candles = list(c)
                    if len(all_candles) >= target:
                        break
                    # expand window and retry
                    attempts += 1
                    est_days = est_days * 2
                    start_dt = now_dt - timedelta(days=est_days)
                    time.sleep(0.1)

                if not all_candles:
                    continue

                final = all_candles[-target:]
                upsert_rows(cur, final, s, label)
                # return a brief success message
        return f'Upserted {len(final)} rows for {s} ({",".join(timeframe_labels)})'

    # Run ThreadPoolExecutor over symbols
    workers = max(1, min(args.workers, len(symbols)))
    print(f'Starting {workers} worker threads for {len(symbols)} symbols')
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_symbol, s): s for s in symbols}
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                print('Worker error for', s, e)
                res = f'Error for {s}: {e}'
            print(res)


if __name__ == '__main__':
    main()
