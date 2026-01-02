#!/usr/bin/env python3
"""Fetch 1-minute OHLCV for all unique symbols found in Csvs/dataNSE_20250804.csv

Saves per-symbol CSV files into Csvs/minute_jul2025/ with header
date,open,high,low,close,volume

This script uses the existing KiteConnect token format in Core_files/token.txt
and reuses the instrument list from kite.instruments().
"""
import os
import sys
import csv
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

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


def read_unique_symbols(csv_path):
    syms = set()
    with open(csv_path, 'r') as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            ts = r.get('ticker') or r.get('symbol') or r.get('tradingsymbol')
            if ts:
                syms.add(ts.strip().upper())
    return sorted(syms)


def write_per_day_csvs(out_dir, symbol, candles, date_map):
    """Accumulate candle rows into date_map keyed by YYYY-MM-DD.

    date_map is a dict mapping date_str -> list of rows. Rows follow original CSV columns:
    ticker,time,open,high,low,close,volume
    """
    for c in candles:
        d = c.get('date') or c.get('timestamp')
        if isinstance(d, datetime):
            d_dt = d
        else:
            # try parse common kite string formats
            try:
                if isinstance(d, str) and ' ' in d:
                    d_dt = datetime.strptime(d.split('.')[0], '%Y-%m-%d %H:%M:%S')
                else:
                    d_dt = datetime.strptime(str(d), '%Y-%m-%d')
            except Exception:
                # skip unparsable
                continue
        date_key = d_dt.strftime('%Y-%m-%d')
        time_str = d_dt.strftime('%Y-%m-%d %H:%M:%S')
        row = [symbol, time_str, c.get('open'), c.get('high'), c.get('low'), c.get('close'), c.get('volume')]
        date_map.setdefault(date_key, []).append(row)


def fetch_minute_for_symbol(kite, token_map, symbol, out_dir, shared_date_maps):
    symbol = symbol.upper()
    if symbol not in token_map:
        return f'SKIP {symbol}: instrument token not found'
    instr = token_map[symbol]
    # July 2025: 2025-07-01 to 2025-07-31 inclusive
    start = datetime(2025,7,1,9,15,0)
    end = datetime(2025,7,31,15,30,0)
    try:
        # kite.historical_data expects strings for intraday
        fstr = start.strftime('%Y-%m-%d %H:%M:%S')
        tstr = end.strftime('%Y-%m-%d %H:%M:%S')
        time.sleep(0.05)
        candles = kite.historical_data(instr, fstr, tstr, 'minute', continuous=False)
        c_list = list(candles) if candles else []
        # accumulate into shared_date_maps (dict of symbol->date_map) to write later serially
        date_map = {}
        write_per_day_csvs(out_dir, symbol, c_list, date_map)
        shared_date_maps[symbol] = date_map
        return f'OK {symbol}: {len(c_list)} rows'
    except Exception as e:
        return f'FAIL {symbol}: {e}'


def main():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    token = load_token(os.path.join(repo_root, 'Core_files', 'token.txt'))
    api_key = token.get('api_key') or os.getenv('KITE_API_KEY') or os.getenv('KITE_APIKEY')
    access_token = token.get('access_token') or os.getenv('KITE_ACCESS_TOKEN')
    if not access_token or not api_key:
        print('Missing Kite credentials (Core_files/token.txt or env).')
        return

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

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

    csv_path = os.path.join(repo_root, 'Csvs', 'dataNSE_20250804.csv')
    out_dir = os.path.join(repo_root, 'Csvs')
    symbols = read_unique_symbols(csv_path)
    print(f'Found {len(symbols)} unique symbols; starting download -> {out_dir} (will write dataNSE_YYYYMMDD.csv files)')

    workers = min(8, max(1, len(symbols)))
    # shared container to collect per-symbol per-day rows
    manager_maps = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fetch_minute_for_symbol, kite, token_map, s, out_dir, manager_maps): s for s in symbols}
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                print('ERROR', s, e)
                continue
            print(res)

    # Now write per-date CSV files combined across symbols, matching original CSV layout
    # original file name pattern: dataNSE_YYYYMMDD.csv
    # header: ticker,time,open,high,low,close,volume
    per_date_rows = {}
    for sym, date_map in manager_maps.items():
        for date_key, rows in date_map.items():
            per_date_rows.setdefault(date_key, []).extend(rows)

    os.makedirs(out_dir, exist_ok=True)
    for date_key in sorted(per_date_rows.keys()):
        fname = os.path.join(out_dir, f"dataNSE_{date_key.replace('-', '')}.csv")
        with open(fname, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['ticker','time','open','high','low','close','volume'])
            for row in sorted(per_date_rows[date_key], key=lambda r: (r[0], r[1])):
                w.writerow(row)
        print(f'WROTE {fname} ({len(per_date_rows[date_key])} rows)')


if __name__ == '__main__':
    main()
