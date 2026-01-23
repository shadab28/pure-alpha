from decimal import Decimal
import sys
import os

# Ensure project root is on sys.path so tests can import application modules
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from Webapp.trail_utils import compute_candidate_trigger


def test_candidate_trigger_exact_example():
    # ltp=100, tick=0.05, threshold=0.001 -> raw=99.9? actually 100*(1-0.001)=99.9? wait compute
    ltp = 100.0
    tick = 0.05
    threshold = 0.001  # 0.1%
    cand = compute_candidate_trigger(ltp, tick, threshold)
    # expected raw = 100 * (1 - 0.001) = 99.9? careful: 1 - 0.001 = 0.999 -> 99.9
    # rounded to tick 0.05 -> nearest multiple of 0.05 is 99.9 -> 99.9
    assert cand == 99.9


def test_no_update_when_move_below_threshold():
    # current_trigger is close to ltp such that gap pct <= threshold -> no update
    ltp = 100.0
    current_trigger = 99.95
    threshold = 0.001  # 0.1%
    gap_pct = (ltp - current_trigger) / ltp
    assert gap_pct <= threshold
    # candidate trigger would be equal-or-lower than current_trigger -> we expect none or <= current_trigger
    cand = compute_candidate_trigger(ltp, 0.05, threshold)
    assert cand is not None
    assert cand <= current_trigger
