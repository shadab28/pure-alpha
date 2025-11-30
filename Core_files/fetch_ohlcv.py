#!/usr/bin/env python3
"""Fetch last N days of OHLCV for universe from database.

Reads `Support Files/param.yaml` to find `universe_list` and timeframe, loads
the symbol list from `Support Files/NiftySymbol.py`, queries Postgres via
`pgAdmin_database.db_connection.pg_cursor` and writes per-symbol CSVs under
`Core_files/data/`.

Usage: python Core_files/fetch_ohlcv.py --days 200
"""
import os
import csv
import argparse
from datetime import datetime, timedelta

import yaml

from pgAdmin_database.db_connection import pg_cursor

# load universe symbol lists from `Support Files/NiftySymbol.py` (path contains space)
def load_nifty_module():
    import importlib.util
    module_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Support Files', 'NiftySymbol.py')
    spec = importlib.util.spec_from_file_location('nifty_symbols', module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod




def load_params(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def get_universe(name: str):
    # Try to load the Support Files/NiftySymbol.py module by path and return the list
    try:
        mod = load_nifty_module()
        return getattr(mod, name)
    except Exception:
        return None


def fetch_symbol_ohlcv(cur, symbol, timeframe, limit):
    # Assumes table `ohlcv_data` with columns: symbol, timeframe, ts, open, high, low, close, volume
    sql = (
        "SELECT symbol, timeframe, ts, open, high, low, close, volume "
        "FROM ohlcv_data "
        "WHERE symbol = %s AND timeframe = %s "
        "ORDER BY ts DESC LIMIT %s"
    )
    cur.execute(sql, (symbol, timeframe, limit))
    rows = cur.fetchall()
    # rows may be tuples or dicts depending on cursor factory
    return rows


def save_csv(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not rows:
        return
    # handle dict rows
    first = rows[0]
    if isinstance(first, dict):
        fieldnames = list(first.keys())
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
    else:
        # assume tuple in order: symbol, timeframe, ts, open, high, low, close, volume
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['symbol', 'timeframe', 'ts', 'open', 'high', 'low', 'close', 'volume'])
            for r in rows:
                writer.writerow(r)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=20, help='Days of data to fetch (approx rows)')
    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    params = load_params(os.path.join(repo_root, 'Support Files', 'param.yaml'))
    universe_name = params.get('universe_list', 'allstocks')
    timeframe = params.get('timeframe', '15minute')

    symbols = get_universe(universe_name)
    if not symbols:
        print('Universe not found:', universe_name)
        return

    # rough approximation: if timeframe is daily, limit = days; if intraday, estimate bars per day
    if timeframe in ('day', '1d', 'daily'):
        limit = args.days
    else:
        # for 15minute assume ~26 bars per trading day -> days * 26
        per_day = 390 // int(timeframe.replace('minute', '')) if 'minute' in timeframe else 1
        per_day = max(per_day, 1)
        limit = args.days * per_day

    out_dir = os.path.join(os.path.dirname(__file__), 'data')

    try:
        with pg_cursor(dict_rows=True) as (cur, conn):
            for s in symbols:
                print('Fetching', s)
                rows = fetch_symbol_ohlcv(cur, s, timeframe, limit)
                # rows are returned newest first; reverse to chronological
                rows = list(rows)[::-1]
                out_path = os.path.join(out_dir, f"{s}_{timeframe}.csv")
                save_csv(out_path, rows)
                print('Saved', out_path)
    except Exception as e:
        print('Database fetch failed:', e)


if __name__ == '__main__':
    main()
