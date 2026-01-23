# Centralized Cooldown (Webapp)

This document describes the centralized cooldown mechanism introduced to keep entry-blocking state consistent across the web API and the strategy engine.

## Motivation

- Previously there were two independent cooldown stores: an API-level `_last_stop_ts` in `Webapp/app.py` and a strategy-level `_last_exit_time` in `Webapp/momentum_strategy.py`.
- That caused inconsistencies when an external order update (ticker callback) recorded a stop/exit but the strategy did not (or vice versa).
- The centralized store ensures both the API and strategy consult the same single source-of-truth for per-symbol cooldowns.

## What changed (files)

- Added: `Webapp/cooldown.py` — new centralized module implementing the cooldown store and helpers.
- Updated: `Webapp/app.py` — API now proxies cooldown checks/recording to `Webapp.cooldown` via `_can_enter_symbol()` and `_record_stop_trigger()`.
- Updated: `Webapp/momentum_strategy.py` — strategy uses `self._cooldown.is_allowed(...)` for entry checks and calls `self._cooldown.record(symbol)` when recording an exit; it still keeps a local `_last_exit_time` map for logging/compatibility.

## Module API (`Webapp.cooldown`)

Public functions:

- `record(symbol: str) -> None`
  - Record that a stop/exit happened for `symbol` at the current time.

 - `is_allowed(symbol: str, cooldown_seconds: int = 600) -> Tuple[bool, Optional[int]]`
  - Returns `(allowed, remaining_seconds)` where `allowed` is True when the symbol is free to take a new entry, and `remaining_seconds` (int) tells how many seconds remain until the cooldown expires.

- `get_last_timestamp(symbol: str) -> Optional[datetime]`
  - Returns the last recorded timestamp for the symbol or `None`.

Implementation notes:

- The module uses a thread-safe `RLock` and an in-memory dictionary keyed by symbol.
 - Default cooldown is 600 seconds (10 minutes). This default is enforced by callers but can be supplied to `is_allowed()`.

## How the API is used

- `Webapp/app.py`
  - Before accepting a buy entry (`/api/order/buy`) the endpoint calls `_can_enter_symbol(symbol, cooldown_seconds=600)` which now calls `Webapp.cooldown.is_allowed(...)`.
  - Order-update/ticker callbacks call `_record_stop_trigger(symbol)` which now calls `Webapp.cooldown.record(symbol)`.

- `Webapp/momentum_strategy.py`
  - When attempting to open a position the strategy calls `self._cooldown.is_allowed(symbol, cooldown_seconds=10 * 60)` (10 minutes).
  - When the strategy closes a trade it records the exit locally and also calls `self._cooldown.record(symbol)` so the API sees the exit right away.

## Caveats & recommended follow-ups

1. Symbol normalization
   - The cooldown store is keyed by the symbol string passed in. The inbound order-update handler in `app.py` must provide a canonical tradingsymbol (e.g., `UNIONBANK`). If order updates contain numeric tokens or different naming, consider adding a normalization layer (instrument_token → tradingsymbol) before calling `record()`.

2. Persistence
   - Current implementation is in-memory and will not survive process restarts. If you need cross-process persistence, consider adding a small SQLite table or a Redis key with TTL.

3. Observability
   - `get_last_timestamp(symbol)` was added to support quick inspection and debugging. Consider adding an admin API endpoint that returns cooldown status for a small set of symbols.

4. Tests
  - Add a unit test that calls `record('FOO')`, asserts `is_allowed('FOO', cooldown_seconds=600)` returns `(False, >0)`, and simulates time advance (or monkeypatch datetime) to assert it becomes allowed after expiry.

## Quick examples

Check if symbol is allowed (from code):

```py
from Webapp import cooldown
allowed, remaining = cooldown.is_allowed('SILVERBEES', cooldown_seconds=600)
if not allowed:
    print('Blocked for', remaining, 'seconds')
```

Record a stop/exit:

```py
from Webapp import cooldown
cooldown.record('SILVERBEES')
```

Get last recorded timestamp for debugging:

```py
from Webapp import cooldown
ts = cooldown.get_last_timestamp('SILVERBEES')
print(ts)
```

## Migration / roll-back

This change is low-risk and additive. Both the API and strategy now rely on the `Webapp.cooldown` module. To roll back, restore the previous `_last_stop_ts` and `_last_exit_time` handling in `app.py` and `momentum_strategy.py` respectively.

## Next steps (optional)

- Add symbol normalization (recommended) so order-update events map to canonical symbols before recording.
- Add persistence (SQLite/Redis) if cooldowns must survive restarts.
- Add tests and a small admin endpoint to list cooldowns for debugging.

---

If you want, I can add a short unit test now and/or an admin HTTP endpoint (`/admin/cooldowns`) that lists current cooldown timestamps for a set of symbols. Which should I do next?
