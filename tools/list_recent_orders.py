#!/usr/bin/env python3
"""List recent momentum orders from the strategy SQLite DB with ranking info.

Run from repo root with the virtualenv active.
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'Webapp', 'momentum_strategy_paper.db')

def fmt(val):
    return val if val is not None else ''

def main(limit=20):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT trade_id, symbol, entry_time, entry_price, qty, stop_loss, target, status, order_id, gtt_id, rank_gm_at_entry, order_book_rank_score FROM trades ORDER BY trade_id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    print(f"Recent {len(rows)} trades (most recent first):\n")
    for r in rows:
        entry_time = r['entry_time']
        try:
            t = datetime.fromisoformat(entry_time)
            entry_time = t.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            pass
        print(f"{r['trade_id']:4} | {r['symbol']:10} | {entry_time} | Entry=₹{r['entry_price']} x{r['qty']} | SL=₹{r['stop_loss']} | Target={fmt(r['target'])} | Status={r['status']} | Order={fmt(r['order_id'])} | GTT={fmt(r['gtt_id'])} | Rank_GM_at_entry={fmt(r['rank_gm_at_entry'])} | OB_Score={fmt(r['order_book_rank_score'])}")

if __name__ == '__main__':
    main()
