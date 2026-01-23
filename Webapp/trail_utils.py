"""Small helper utilities for trailing-stop calculations used by the app and tests."""
from typing import Optional

def _round_to_tick(price: float, tick: float) -> float:
    if not tick:
        tick = 0.05
    try:
        return round(round(price / tick) * tick, 2)
    except Exception:
        return float(round(price, 2))

def compute_candidate_trigger(ltp: float, tick: float, threshold: float) -> Optional[float]:
    """Compute the candidate stop trigger given LTP, tick size and threshold fraction.

    Returns the rounded trigger price (ltp * (1 - threshold)) snapped to tick, or
    None for invalid inputs.
    """
    try:
        if not isinstance(ltp, (int, float)) or ltp <= 0:
            return None
        if not isinstance(threshold, (int, float)) or threshold < 0:
            return None
        raw = ltp * (1 - threshold)
        return _round_to_tick(raw, tick)
    except Exception:
        return None
