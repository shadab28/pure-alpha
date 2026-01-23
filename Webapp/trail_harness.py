"""Simple harness to simulate LTP ticks and trailing decisions using trail_utils.

Run to see when candidate triggers would be raised for two example positions (P2 and P3).
"""
from __future__ import annotations
import sys
import os
import time
from decimal import Decimal

# Ensure project root is on path so we can import Webapp package
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Webapp.trail_utils import compute_candidate_trigger


TRAIL_THRESHOLD = 0.001  # 0.1%


def simulate_trail(symbol: str, entry: float, sl_pct: float, tick: float, steps: list[float]):
    """Simulate LTP increases and print when the trailing logic would raise the stop.

    - entry: entry price
    - sl_pct: negative percentage (e.g., -2.5)
    - tick: tick size (e.g., 0.05)
    - steps: list of ltp values to simulate
    """
    print(f"\nSimulating {symbol}: entry={entry} sl_pct={sl_pct}% tick={tick} threshold={TRAIL_THRESHOLD}")
    # initial trigger: start at LTP-based threshold (as worker does when initializing)
    ltp0 = steps[0]
    current_trigger = compute_candidate_trigger(ltp0, tick, TRAIL_THRESHOLD)
    print(f"Initial LTP={ltp0:.2f} -> initial_trigger={current_trigger:.2f}")

    for ltp in steps[1:]:
        cand = compute_candidate_trigger(ltp, tick, TRAIL_THRESHOLD)
        if cand is None:
            continue
        current_gap_pct = (ltp - (current_trigger or 0)) / ltp
        # emulate worker decision: require gap_pct > TRAIL_THRESHOLD and candidate > current_trigger
        if current_gap_pct > TRAIL_THRESHOLD and cand > (current_trigger or 0):
            print(f"RAISE at LTP={ltp:.2f}: gap={current_gap_pct:.6f} -> new_trigger={cand:.2f} (old={current_trigger:.2f})")
            current_trigger = cand
        else:
            print(f"NOCHANGE at LTP={ltp:.2f}: gap={current_gap_pct:.6f} cand={cand:.2f} cur={current_trigger:.2f}")


def run():
    # Simulate ticks for P2 and P3
    # Use entry=100, tick=0.05 and LTP steps with small increments to show 0.1% threshold effects
    ltp_steps = [100.00]
    # generate increases of 0.02 upto +1.0 (enough to cross 0.1% increments)
    cur = 100.00
    for i in range(1, 61):
        cur = round(cur + 0.02, 4)
        ltp_steps.append(cur)

    simulate_trail('P2_EXAMPLE', entry=100.0, sl_pct=-2.5, tick=0.05, steps=ltp_steps)
    simulate_trail('P3_EXAMPLE', entry=100.0, sl_pct=-5.0, tick=0.05, steps=ltp_steps)


if __name__ == '__main__':
    run()
