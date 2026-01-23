from datetime import datetime
import threading
from typing import Tuple, Optional

# Centralized per-symbol cooldown store used by API and strategy.
# Functions:
# - record(symbol: str) -> None
# - is_allowed(symbol: str, cooldown_seconds: int = 180) -> Tuple[bool, Optional[int]]

_lock = threading.RLock()
_last_stop_ts: dict[str, datetime] = {}


def record(symbol: str) -> None:
    """Record that a stop/exit happened for `symbol` now."""
    if not symbol:
        return
    with _lock:
        _last_stop_ts[symbol] = datetime.now()


def is_allowed(symbol: str, cooldown_seconds: int = 600) -> Tuple[bool, Optional[int]]:
    """Return (allowed, remaining_seconds).

    allowed is True when no recent stop within cooldown_seconds. If False, remaining_seconds
    is how many seconds left before entry is permitted.
    """
    with _lock:
        ts = _last_stop_ts.get(symbol)
    if not ts:
        return True, None
    delta = datetime.now() - ts
    rem = cooldown_seconds - int(delta.total_seconds())
    if rem <= 0:
        return True, None
    return False, rem


def get_last_timestamp(symbol: str) -> Optional[datetime]:
    """Return the last recorded timestamp for a symbol, or None."""
    with _lock:
        return _last_stop_ts.get(symbol)
