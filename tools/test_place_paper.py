#!/usr/bin/env python3
"""Test placing a PAPER order via the momentum strategy module.
This script creates a Strategy instance in PAPER mode and attempts to open a P1 trade for a symbol.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Webapp.momentum_strategy import RankingRow, Trade, MomentumStrategy
from decimal import Decimal

s = MomentumStrategy(mode='PAPER')
# Create a synthetic ranking row for a low-priced stock to ensure qty>0
r = RankingRow(
    symbol='TESTSYM',
    rank=1.0,
    rank_gm=3.5,
    rank_final=3.7,
    last_price=Decimal('50.00'),
    lot_size=1,
    volume_ratio=1.0,
    order_book_rank_score=0.5
)

trade = s.open_position(r, position_type=1)
if trade:
    print('Trade opened:', trade.trade_id, trade.symbol, trade.entry_price, trade.qty)
else:
    print('Failed to open trade')
