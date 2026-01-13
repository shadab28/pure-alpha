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
import sys
import json
import time
from datetime import datetime, timedelta
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import yaml

# Ensure repository root is on sys.path so top-level packages import
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pgAdmin_database.db_connection import pg_cursor

from kiteconnect import KiteConnect


def load_local_env():
    """Load KEY=VALUE pairs from .env files into os.environ if not already set.

    Searches Core_files/.env and repo_root/.env.
    """
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
            # best-effort only
            pass


def load_token(path):
    """Load credentials from token file.

    Supports two formats:
    - JSON dict: {"api_key": "...", "access_token": "..."}
    - Plain text: a single-line access token string
    Returns a dict with at least 'access_token'.
    """
    try:
        with open(path, "r") as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return {}
    if not raw:
        return {}
    # Try JSON first
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # Fallback: treat the whole file as the access token
    return {"access_token": raw}


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
    # Load environment variables from local .env files early
    load_local_env()
    parser = argparse.ArgumentParser(description='Fetch OHLCV from Kite and upsert to Postgres (supports 15m and 1d). Use --daily or --timeframes 1d for daily candles.')
    parser.add_argument('--days', type=int, default=200)
    parser.add_argument('--rows', type=int, default=None, help='Approx number of rows per symbol to fetch (overrides --days if set)')
    parser.add_argument('--timeframes', type=str, default=None, help='Comma-separated list of timeframes (e.g. 15m,1d). If omitted, uses param.yaml timeframe.')
    parser.add_argument('--daily', action='store_true', help='Fetch only 1d timeframe (daily) for the given --days')
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

    if args.daily:
        pairs = [('1d', 'day')]
    elif args.timeframes:
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
    api_key = token.get('api_key') or os.getenv('KITE_API_KEY') or os.getenv('KITE_APIKEY')
    access_token = token.get('access_token') or os.getenv('KITE_ACCESS_TOKEN')
    if not access_token:
        print('Missing access_token. Put it in Core_files/token.txt (plain text or JSON) or set KITE_ACCESS_TOKEN env.')
        return
    if not api_key:
        print('Missing api_key. Provide in token.txt as JSON {"api_key": "...", "access_token": "..."} or set KITE_API_KEY env.')
        return

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    # Early validation to fail fast on invalid/expired tokens
    try:
        _ = kite.profile()
    except Exception as e:
        print('Kite access token validation failed:', e)
        print('Tip: Run "python Core_files/auth.py" to obtain a fresh token and save it to Core_files/token.txt')
        return

    mod = load_nifty_module()
    symbols = getattr(mod, universe_name, None)
    if not symbols:
        print('Universe not found:', universe_name)
        return

    # Use today's date for fetching candles (same day)
    today = datetime.utcnow().date()
    to_date = datetime.combine(today, datetime.max.time())
    from_date = datetime.combine(today, datetime.min.time())

    # Preflight: ensure table and unique index exist once to avoid duplicate index creation by workers
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

    # We'll parallelize symbol processing to speed up the run. Each worker will open its own DB connection.
    print('Downloading instruments list (this may take a while)')
    instruments = kite.instruments()
    # Prefer NSE tokens when duplicates exist across exchanges
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
                "stockname TEXT NOT NULL, timeframe TEXT NOT NULL, candle_stock TIMESTAMP WITHOUT TIME ZONE NOT NULL, "
                "open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, volume BIGINT DEFAULT 0, "
                "PRIMARY KEY (stockname, timeframe, candle_stock) )"
            )
            cur.execute(create_sql)

            instr = token_map.get(s)
            if not instr:
                return f'Instrument token not found for {s}'

            print(f"Timeframes to fetch: {timeframe_labels}")
            
            # Special handling for NIFTY 50: download historical daily data (2+ years) + 15m today
            if s == 'NIFTY 50':
                today = datetime.utcnow().date()
                
                # 1. Download historical daily data (2+ years)
                print(f"Fetching historical daily data for {s}")
                try:
                    hist_start = today - timedelta(days=730)  # ~2 years
                    hist_end = today + timedelta(days=1)
                    c = _call_historical(kite, instr, hist_start, hist_end, 'day')
                    hist_candles = list(c) if c else []
                    if hist_candles:
                        upsert_rows(cur, hist_candles, s, '1d')
                        print(f"Upserted {len(hist_candles)} historical daily candles for {s}")
                except Exception as e:
                    print(f"Warning: Failed to fetch historical daily data for {s}: {e}")
                
                # 2. Download 15m candles for today
                print(f"Fetching 15m intraday data for {s} (today only)")
                try:
                    intraday_start = datetime.combine(today, datetime.min.time())
                    intraday_end = datetime.combine(today, datetime.max.time())
                    c = _call_historical(kite, instr, intraday_start, intraday_end, '15minute')
                    intraday_candles = list(c) if c else []
                    if intraday_candles:
                        upsert_rows(cur, intraday_candles, s, '15m')
                        print(f"Upserted {len(intraday_candles)} 15m candles for {s}")
                except Exception as e:
                    print(f"Warning: Failed to fetch 15m data for {s}: {e}")
                
                return f'Completed NIFTY 50: historical daily + 15m today'
            
            # Enhanced processing for ALL stocks: fetch 15m candles + 200 daily candles
            # This enables VCP analysis across entire portfolio
            for label, kite_tf in zip(timeframe_labels, kite_timeframes):
                today = datetime.utcnow().date()
                yesterday = today - timedelta(days=1)
                
                # Always fetch 15m data for intraday VCP analysis (enabled for all stocks as of Jan 9)
                if kite_tf == '15minute' or label == '15m':
                    print(f"Fetching last 200 15m candles for {s} (spanning up to 8 days for holidays)")
                    try:
                        # Fetch last 8 days (spans market holidays) to get 200+ 15m candles
                        # 8 business days × ~26 candles/day = ~208 candles (accounts for holidays)
                        start_dt = datetime.combine(today - timedelta(days=8), datetime.min.time())
                        end_dt = datetime.combine(today, datetime.max.time())
                        c = _call_historical(kite, instr, start_dt, end_dt, '15minute')
                        intraday_candles = list(c) if c else []
                        
                        if intraday_candles:
                            # Take last 200 candles to ensure consistent data across all symbols
                            final_15m = intraday_candles[-200:]
                            upsert_rows(cur, final_15m, s, '15m')
                            total = len(intraday_candles)
                            used = len(final_15m)
                            print(f"Upserted {used} 15m candles for {s} (fetched {total}, kept last 200)")
                        else:
                            print(f"No 15m candles fetched for {s}")
                    except Exception as e:
                        print(f"Warning: Failed to fetch 15m data for {s}: {e}")
                
                # Fetch daily candles: 200 recent candles spanning last ~1 year
                elif kite_tf == 'day' or label == '1d':
                    print(f"Fetching daily candles for {s}")
                    try:
                        # Fetch last 300 days to ensure we get 200+ complete candles
                        start_dt = today - timedelta(days=300)
                        end_dt = today + timedelta(days=1)
                        c = _call_historical(kite, instr, start_dt, end_dt, 'day')
                        daily_candles = list(c) if c else []
                        if daily_candles:
                            # Take last 200 candles for ~1 year of data
                            final = daily_candles[-200:]
                            upsert_rows(cur, final, s, '1d')
                            print(f"Upserted {len(final)} daily candles for {s} (last 200 days)")
                    except Exception as e:
                        print(f"Warning: Failed to fetch daily data for {s}: {e}")
        
        return f'Completed {s}: 15m intraday + daily candles'

    # Run ThreadPoolExecutor over symbols
    # Force worker threads to 4 regardless of CLI
    workers = min(8, len(symbols))
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
