#!/usr/bin/env python3
"""Download historical OHLCV (15m, 1d, 1w, 1m) for a universe and save CSV files.

By default this script writes per-symbol CSVs to --out-dir with filenames like SYMBOL_15m.csv

Usage examples:
  PYTHONPATH=. python Core_files/historical_ohlcv_data_download.py --timeframes 15m,1d,1w,1m --days 400 --out-dir data
  PYTHONPATH=. python Core_files/historical_ohlcv_data_download.py --symbols TCS,INFY --timeframes 1d --days 800 --out-dir data

This script requires a valid Kite token in `Core_files/token.txt` (same format as other scripts).
"""
import os
import sys
import time
import csv
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# Ensure repo root on path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from kiteconnect import KiteConnect

def load_token(path):
    try:
        with open(path, 'r') as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return {}
    try:
        import json
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {'access_token': raw}

def normalize_tf(tf: str):
    t = tf.strip().lower()
    if t in ('15m', '15minute', '15min'):
        return '15m', '15minute'
    if t in ('1d', 'day', 'daily'):
        return '1d', 'day'
    if t in ('1w','week','weekly'):
        return '1w', 'week'  # 'week' is not a kite interval; we'll aggregate from daily
    if t in ('1m','month','monthly'):
        return '1m', 'month'  # aggregate from daily
    raise ValueError('Unsupported timeframe: ' + tf)

def fetch_candles(kite, instrument_token, from_dt, to_dt, kite_interval):
    # kite_interval: '15minute' or 'day'
    try:
        if kite_interval == 'day':
            fstr = from_dt.date().isoformat() if isinstance(from_dt, datetime) else str(from_dt)
            tstr = to_dt.date().isoformat() if isinstance(to_dt, datetime) else str(to_dt)
        else:
            fstr = from_dt.strftime('%Y-%m-%d %H:%M:%S')
            tstr = to_dt.strftime('%Y-%m-%d %H:%M:%S')
        return kite.historical_data(instrument_token, fstr, tstr, kite_interval, continuous=False)
    except Exception as e:
        raise

def aggregate_to_period(candles, period: str):
    """Aggregate daily candles into weekly or monthly. Candles expected as list of dicts with 'date','open','high','low','close','volume'.
    Returns list of aggregated candles with 'date' as period end date string.
    """
    if not candles:
        return []
    out = {}
    for c in candles:
        # Kite daily 'date' may be a string 'YYYY-MM-DD' or datetime
        d = c.get('date') or c.get('timestamp')
        if isinstance(d, str):
            try:
                dt = datetime.fromisoformat(d)
            except Exception:
                dt = datetime.strptime(d.split('.')[0], '%Y-%m-%d %H:%M:%S') if ' ' in d else datetime.strptime(d, '%Y-%m-%d')
        elif isinstance(d, datetime):
            dt = d
        else:
            # fallback: skip
            continue

        if period == 'week':
            key = (dt.isocalendar()[0], dt.isocalendar()[1])  # year, week
        else:
            key = (dt.year, dt.month)

        recs = out.get(key) or []
        recs.append((dt, c))
        out[key] = recs

    agg = []
    # sort keys chronologically
    for key in sorted(out.keys()):
        recs = sorted(out[key], key=lambda x: x[0])
        opens = [r[1].get('open') for r in recs]
        highs = [r[1].get('high') for r in recs]
        lows = [r[1].get('low') for r in recs]
        closes = [r[1].get('close') for r in recs]
        volumes = [r[1].get('volume') or 0 for r in recs]
        # period end date = last record's date
        end_dt = recs[-1][0]
        agg.append({
            'date': end_dt.strftime('%Y-%m-%d'),
            'open': opens[0],
            'high': max(highs),
            'low': min(lows),
            'close': closes[-1],
            'volume': sum(int(v or 0) for v in volumes),
        })
    return agg

def write_csv(out_dir, symbol, timeframe_label, candles):
    os.makedirs(out_dir, exist_ok=True)
    fname = os.path.join(out_dir, f"{symbol}_{timeframe_label}.csv")
    with open(fname, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['date','open','high','low','close','volume'])
        for c in candles:
            # support various key names
            d = c.get('date') or c.get('timestamp') or c.get('datetime')
            if isinstance(d, datetime):
                dstr = d.strftime('%Y-%m-%d %H:%M:%S')
            else:
                dstr = str(d)
            writer.writerow([dstr, c.get('open'), c.get('high'), c.get('low'), c.get('close'), c.get('volume')])
    return fname

def main():
    parser = argparse.ArgumentParser(description='Download historical OHLCV and save CSVs (15m,1d,1w,1m).')
    parser.add_argument('--symbols', type=str, default=None, help='Comma-separated symbols (overrides universe param)')
    parser.add_argument('--timeframes', type=str, default='15m,1d,1w,1m')
    parser.add_argument('--days', type=int, default=400, help='Approx days to fetch for daily; intraday computed from days')
    parser.add_argument('--out-dir', type=str, default=os.path.join(REPO_ROOT, 'historical_data'))
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()

    token = load_token(os.path.join(os.path.dirname(__file__), 'token.txt'))
    api_key = token.get('api_key') or os.getenv('KITE_API_KEY') or os.getenv('KITE_APIKEY')
    access_token = token.get('access_token') or os.getenv('KITE_ACCESS_TOKEN')
    if not access_token or not api_key:
        print('Missing Kite credentials (Core_files/token.txt or env).')
        return

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    # load instruments and map tradingsymbol -> instrument_token (prefer NSE)
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

    # load universe from NiftySymbol if symbols not provided
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(',') if s.strip()]
    else:
        import importlib.util
        module_path = os.path.join(REPO_ROOT, 'Support Files', 'NiftySymbol.py')
        spec = importlib.util.spec_from_file_location('nifty_symbols', module_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        universe_name = getattr(mod, 'universe_list', None) or 'stocks2026'
        symbols = getattr(mod, universe_name, None) or getattr(mod, 'stocks2026', None) or []

    tf_pairs = [normalize_tf(x)[0] for x in args.timeframes.split(',') if x.strip()]

    now = datetime.utcnow()
    # basic fetching function per symbol
    def process_symbol(sym):
        sym = sym.upper()
        if sym not in token_map:
            return f'SKIP {sym}: instrument token not found'
        instr = token_map[sym]
        results = []
        # fetch daily if needed (useful for weekly/monthly aggregation)
        need_daily = any(tf in ('1d','1w','1m') for tf in tf_pairs)
        daily_candles = None
        if need_daily:
            start_dt = now - timedelta(days=args.days)
            try:
                c = fetch_candles(kite, instr, start_dt, now, 'day')
                daily_candles = list(c) if c else []
            except Exception as e:
                return f'FAIL {sym} daily fetch: {e}'
            if '1d' in tf_pairs:
                fname = write_csv(args.out_dir, sym, '1d', daily_candles)
                results.append(fname)
        # fetch 15m if requested
        if '15m' in tf_pairs:
            # estimate intraday window: days -> bars
            bars_per_day = int((6.5 * 60) / 15)
            est_days = max(1, int((args.days / bars_per_day) * 1.5) + 1)
            start_dt = now - timedelta(days=est_days)
            try:
                c15 = fetch_candles(kite, instr, start_dt, now, '15minute')
                c15_list = list(c15) if c15 else []
                fname = write_csv(args.out_dir, sym, '15m', c15_list)
                results.append(fname)
            except Exception as e:
                return f'FAIL {sym} 15m fetch: {e}'

        # aggregate weekly/monthly
        if '1w' in tf_pairs or '1m' in tf_pairs:
            if not daily_candles:
                return f'FAIL {sym}: no daily data to aggregate'
            if '1w' in tf_pairs:
                wk = aggregate_to_period(daily_candles, 'week')
                fname = write_csv(args.out_dir, sym, '1w', wk)
                results.append(fname)
            if '1m' in tf_pairs:
                mo = aggregate_to_period(daily_candles, 'month')
                fname = write_csv(args.out_dir, sym, '1m', mo)
                results.append(fname)

        return f'OK {sym}: wrote {len(results)} files'

    # parallelize
    workers = min(args.workers, max(1, len(symbols)))
    print(f'Starting download for {len(symbols)} symbols -> outdir={args.out_dir} using {workers} workers')
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_symbol, s): s for s in symbols}
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                print('ERROR', s, e)
                continue
            print(res)

if __name__ == '__main__':
    main()
