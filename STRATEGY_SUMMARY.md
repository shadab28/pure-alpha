# Momentum Strategy — Summary

This document summarises the live momentum strategy implemented in `Webapp/momentum_strategy.py`.
It describes the position lifecycle, entry/exit rules, GTT behaviour, cooldowns, logging, and known limitations.

## High-level idea
- Trade momentum breakouts on a fixed universe of symbols.
- Allocate a fixed capital per position and scale into the same stock across up to three entries: P1, P2, P3.
- Manage risk with stop-losses (GTT triggers) and use trailing SLs for runners.

## Position lifecycle
- P1: first entry for a symbol. Treated as a fixed-target trade (has a defined target and SL).
- P2: add-on only when P1 is in profit above a configured PnL threshold. May be OCO (target + trailing SL) or runner.
- P3: final add when average(P1,P2) PnL meets configured threshold. Treated as a runner with trailing SL.

## Sizing
- Each position is sized to consume a fixed CAPITAL_PER_POSITION (per code). Quantity is rounded to nearest lot size.

## Stops and targets
- Stop-loss and target are position-specific.
- Typical configured values (see code constants):
  - P1 stop ~ -5% and target ~ +10% (fixed target)
  - P2 stop ~ -5% and target ~ +7.5% (may trail SL)
  - P3 stop trailing, no fixed target (runner)

## GTT & trailing behaviour
- GTTs are used for stop loss and target orders.
- P1 typically places an OCO (stop + target).
- P2 may be OCO or SL-only with trailing enabled.
- P3 uses SL-only trailing orders.
- Trailing is implemented by a local worker: it computes a candidate new stop based on the highest observed LTP and a TRAIL_THRESHOLD (0.1% in runtime configuration) and attempts a broker-side `modify_gtt`. If modify fails, it falls back to delete+place (replace).
- GTT ids are normalized and persisted. On trade exit, only the specific persisted GTT attached to that trade is cancelled (to avoid orphaned triggers).

## Cooldowns & anti-flip protections
- Central cooldown: a per-symbol timestamp prevents immediate re-entry for a configured window (3–10 minutes depending on code path).
- Price-proximity rule: the code blocks new entries when current LTP is not at least 0.25% below the most recent exit price for the same symbol (applies to all new entries). This prevents immediate flip re-entries.

## Logging & observability
- Centralized logging added: `Webapp/logging_config.py` provides date-foldered file handlers.
- Main loggers used:
  - `logs/YYYY-MM-DD/access.log` — HTTP access
  - `logs/YYYY-MM-DD/errors.log` — exceptions and errors
  - `logs/YYYY-MM-DD/trailing.log` — trailing worker events
  - `logs/YYYY-MM-DD/orders.log` — GTT/order actions
- All important broker/network errors, GTT modifies, replaces, and exit-cancel operations are logged.

## Known limitations & risk areas
- Broker connectivity/DNS issues occasionally prevent successful modify/delete/place operations — the system logs these and currently has a fallback but may leave orphaned triggers in rare races.
- There is no broker-side automatic trailing primitive — trailing is implemented client-side via repeated modify attempts.

## Recommended next steps
1. Add post-modify verification: after `modify_gtt`, fetch the broker's trigger and confirm change; if mismatch, retry or replace.
2. Add retries with exponential backoff for GTT network calls and persist attempt counts to avoid tight loops.
3. Add unit/integration tests for modify vs replace flows (use the existing `modify_test_harness.py`).
4. Consider structured (JSON) logs for downstream ingestion and alerts on repeated broker errors.

---
File: `Webapp/momentum_strategy.py` and `Webapp/app.py` are the canonical sources for the rules above. For exact numeric parameters check the constants near the top of `momentum_strategy.py` and the TRAIL_THRESHOLD in `Webapp/main.py`.
