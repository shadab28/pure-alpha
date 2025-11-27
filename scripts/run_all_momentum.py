#!/usr/bin/env python3
"""Run momentum scanner across all symbols defined in NiftySymbol.allstocks.

Resolves symbols -> instrument_token via tools.instrument_resolver and invokes
momentum_strategy.scan_and_signal for each resolved token. Operates in dry-run
mode (no orders) and prints results.
"""
import argparse
import sys
from pathlib import Path

# Ensure project root is on sys.path so top-level modules are importable
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from tools.NiftySymbol import allstocks
from tools.instrument_resolver import resolver_for_csv
from pgAdmin_database import db
import momentum_strategy


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=50, help="limit number of symbols to scan (for testing)")
    p.add_argument("--csv", default="Csvs/instruments.csv")
    args = p.parse_args()

    resolver = resolver_for_csv(args.csv)
    mapping = resolver.bulk_resolve(allstocks[:args.limit])

    conn = db.connect()
    if not conn:
        print("DB not available: momentum scanner will print signals only if candles exist in DB")

    for sym, tok in mapping.items():
        if not tok:
            print(f"{sym}: token not found in CSV")
            continue
        print(f"Scanning {sym} -> token {tok}")
        momentum_strategy.scan_and_signal(conn, tok)


if __name__ == '__main__':
    main()
