"""LTP data service utilities.

Separates data loading and Kite interaction from Flask routes.
"""
from __future__ import annotations

import os
import sys
import importlib.util
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
PARAM_PATH = os.path.join(REPO_ROOT, "Support Files", "param.yaml")
NIFTY_SYMBOL_PATH = os.path.join(REPO_ROOT, "Support Files", "NiftySymbol.py")
TOKEN_PATH = os.path.join(REPO_ROOT, "Core_files", "token.txt")
INSTRUMENTS_CSV_DEFAULT = os.path.join(REPO_ROOT, "Csvs", "instruments.csv")

# Ensure repo root is importable so `kiteconnect` local package resolves
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import yaml  # type: ignore
except Exception:
    yaml = None  # type: ignore

try:
    from kiteconnect import KiteConnect  # type: ignore
except Exception:
    kite_pkg = os.path.join(REPO_ROOT, 'kiteconnect')
    if os.path.isdir(kite_pkg):
        sys.path.insert(0, kite_pkg)
        try:
            from connect import KiteConnect  # type: ignore
        except Exception:
            KiteConnect = None  # type: ignore
    else:
        KiteConnect = None  # type: ignore

# DB access (optional)
try:
    from pgAdmin_database.db_connection import pg_cursor, test_connection  # type: ignore
except Exception:
    pg_cursor = None  # type: ignore
    test_connection = lambda: False  # type: ignore

# Caches
_daily_ma_cache: Dict[str, Dict[str, Any]] = {}
_daily_ma_lock = threading.RLock()
_daily_ma_thread_started = False

_sma15m_cache: Dict[str, float] = {}
_sma50_15m_cache: Dict[str, float] = {}
_high200_15m_cache: Dict[str, float] = {}
_last15m_close_cache: Dict[str, float] = {}
_sma15m_last_ts: Optional[datetime] = None
_sma15m_lock = threading.RLock()

# Daily volume ratio cache: avg(5d volume) / avg(200d volume)
_volratio_cache: Dict[str, float] = {}
_volratio_last_ts: Optional[datetime] = None
_volratio_lock = threading.RLock()

# Days since last Golden Cross (SMA50 crossed above SMA200) per symbol
_days_since_gc_cache: Dict[str, Optional[int]] = {}
_days_since_gc_last_ts: Optional[datetime] = None
_days_since_gc_lock = threading.RLock()

DAILY_REFRESH_SECONDS = 3600  # recompute daily MAs at most once per hour
FIFTEEN_MIN_REFRESH_SECONDS = 60  # recompute 15m SMA200 at most once per minute


def load_params() -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML not installed. Please install pyyaml.")
    if not os.path.exists(PARAM_PATH):
        raise FileNotFoundError(f"param.yaml not found at {PARAM_PATH}")
    with open(PARAM_PATH, "r") as f:
        return yaml.safe_load(f) or {}


def load_symbol_list(universe_list_name: str) -> List[str]:
    if not os.path.exists(NIFTY_SYMBOL_PATH):
        raise FileNotFoundError(f"NiftySymbol.py not found at {NIFTY_SYMBOL_PATH}")
    spec = importlib.util.spec_from_file_location("NiftySymbol", NIFTY_SYMBOL_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(module)  # type: ignore
    if not hasattr(module, universe_list_name):
        raise AttributeError(f"Universe '{universe_list_name}' not found in NiftySymbol.py")
    symbols = getattr(module, universe_list_name)
    return [str(s).strip() for s in symbols]


def get_kite() -> Any:
    if KiteConnect is None:
        raise RuntimeError("kiteconnect module not available (ensure local 'kiteconnect' package or pip install).")
    api_key = os.getenv("KITE_API_KEY")
    if not api_key:
        raise RuntimeError("KITE_API_KEY env not set. Add to .env or export before running.")
    if not os.path.exists(TOKEN_PATH):
        raise RuntimeError("Access token file missing. Run Core_files/auth.py to generate token.txt.")
    with open(TOKEN_PATH, "r") as f:
        access_token = f.read().strip()
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def build_ltp_query(symbols: List[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for s in symbols:
        result[s] = f"NSE:{s}"
    return result


def _load_instrument_tokens(csv_path: str) -> Dict[str, int]:
    mapping: Dict[str, int] = {}
    if not os.path.exists(csv_path):
        return mapping
    import csv as _csv
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = _csv.DictReader(f)
        for row in reader:
            ts = row.get('tradingsymbol')
            tok = row.get('instrument_token')
            if not ts or not tok:
                continue
            try:
                mapping[ts] = int(tok)
            except Exception:
                continue
    return mapping


def _compute_daily_ma_for_symbol(kite: Any, symbol: str, instrument_token: int):
    """Fetch historical daily data and compute SMA50, SMA200 and ratio.

    Uses kite.historical_data; caches results. Heavy operation – called in background.
    """
    # Fetch ~370 calendar days to cover 200 trading days.
    to_dt = datetime.now()
    from_dt = to_dt - timedelta(days=370)
    try:
        candles = kite.historical_data(instrument_token, from_dt, to_dt, 'day')
    except Exception as e:
        return {"error": str(e)}
    closes = [c['close'] for c in candles if 'close' in c]
    closes_sorted = closes  # already chronological per API (ascending)
    if len(closes_sorted) < 10:  # insufficient data
        return {}
    sma50 = sum(closes_sorted[-50:]) / 50 if len(closes_sorted) >= 50 else None
    sma200 = sum(closes_sorted[-200:]) / 200 if len(closes_sorted) >= 200 else None
    ratio = round(sma50 / sma200, 2) if (sma50 and sma200 and sma200 != 0) else None 
    return {"sma50": sma50, "sma200": sma200, "ratio": ratio, "updated": datetime.now()}


def _background_daily_ma_builder(kite: Any, symbols: List[str], sym_to_token: Dict[str, int]):
    global _daily_ma_thread_started
    if _daily_ma_thread_started:
        return
    _daily_ma_thread_started = True

    def worker():
        for sym in symbols:
            tok = sym_to_token.get(sym)
            if tok is None:
                continue
            with _daily_ma_lock:
                cache_entry = _daily_ma_cache.get(sym)
                if cache_entry and (datetime.now() - cache_entry.get('updated', datetime.min)) < timedelta(seconds=DAILY_REFRESH_SECONDS):
                    continue  # fresh
            data = _compute_daily_ma_for_symbol(kite, sym, tok)
            if data:
                with _daily_ma_lock:
                    _daily_ma_cache[sym] = data
        # Sleep until next refresh cycle
        while True:
            time.sleep(DAILY_REFRESH_SECONDS)
            for sym in symbols:
                tok = sym_to_token.get(sym)
                if tok is None:
                    continue
                data = _compute_daily_ma_for_symbol(kite, sym, tok)
                if data:
                    with _daily_ma_lock:
                        _daily_ma_cache[sym] = data

    t = threading.Thread(target=worker, name="daily_ma_builder", daemon=True)
    t.start()


def _refresh_sma15m_if_needed(symbols: List[str]):
    """Populate caches with:
    - 15m SMA200 (close) per symbol -> _sma15m_cache
    - Last completed 15m candle close -> _last15m_close_cache
    - Recent 200-candle high (15m) -> _high200_15m_cache
    """
    if not test_connection() or pg_cursor is None:
        return
    global _sma15m_last_ts
    now = datetime.now()
    if _sma15m_last_ts and (now - _sma15m_last_ts) < timedelta(seconds=FIFTEEN_MIN_REFRESH_SECONDS):
        return
    try:
        with pg_cursor() as (cur, _):
            # SMA200 15m
            cur.execute(
                """
                WITH ranked AS (
                    SELECT stockname, close,
                           ROW_NUMBER() OVER (PARTITION BY stockname ORDER BY candle_stock DESC) AS rn
                    FROM ohlcv_data
                    WHERE timeframe='15m'
                )
                SELECT stockname,
                       AVG(CASE WHEN rn <= 200 THEN close END) AS sma200_15m,
                       AVG(CASE WHEN rn <= 50 THEN close END) AS sma50_15m
                FROM ranked
                GROUP BY stockname
                """
            )
            sma_rows = cur.fetchall()
            # Recent 200-candle high (15m)
            cur.execute(
                """
                WITH ranked AS (
                    SELECT stockname, high,
                           ROW_NUMBER() OVER (PARTITION BY stockname ORDER BY candle_stock DESC) AS rn
                    FROM ohlcv_data
                    WHERE timeframe='15m'
                )
                SELECT stockname,
                       MAX(CASE WHEN rn <= 200 THEN high END) AS high200_15m
                FROM ranked
                GROUP BY stockname
                """
            )
            high_rows = cur.fetchall()
            # Last 15m candle close
            cur.execute(
                """
                SELECT DISTINCT ON (stockname) stockname, close
                FROM ohlcv_data
                WHERE timeframe='15m'
                ORDER BY stockname, candle_stock DESC
                """
            )
            last_rows = cur.fetchall()
        with _sma15m_lock:
            _sma15m_cache.clear()
            _sma50_15m_cache.clear()
            for stockname, sma200, sma50 in sma_rows:
                _sma15m_cache[stockname] = float(sma200) if sma200 is not None else None
                _sma50_15m_cache[stockname] = float(sma50) if sma50 is not None else None
            _high200_15m_cache.clear()
            for stockname, high200 in high_rows:
                try:
                    _high200_15m_cache[stockname] = float(high200) if high200 is not None else None
                except Exception:
                    continue
            _last15m_close_cache.clear()
            for stockname, close_val in last_rows:
                try:
                    _last15m_close_cache[stockname] = float(close_val)
                except Exception:
                    continue
            _sma15m_last_ts = now
    except Exception:
        pass


def _refresh_daily_volratio_if_needed(symbols: List[str]):
    """Compute avg 5-day volume / avg 200-day volume per symbol from ohlcv_data (timeframe='1d')."""
    if not test_connection() or pg_cursor is None:
        return
    global _volratio_last_ts
    now = datetime.now()
    # Refresh at most once every 15 minutes
    if _volratio_last_ts and (now - _volratio_last_ts) < timedelta(minutes=15):
        return
    try:
        with pg_cursor() as (cur, _):
            cur.execute(
                """
                WITH ranked AS (
                    SELECT stockname, volume,
                           ROW_NUMBER() OVER (PARTITION BY stockname ORDER BY candle_stock DESC) AS rn
                    FROM ohlcv_data
                    WHERE timeframe='1d'
                )
                SELECT stockname,
                       AVG(CASE WHEN rn <= 5 THEN volume END) AS avg5,
                       AVG(CASE WHEN rn <= 200 THEN volume END) AS avg200
                FROM ranked
                GROUP BY stockname
                """
            )
            rows = cur.fetchall()
        with _volratio_lock:
            _volratio_cache.clear()
            for stockname, avg5, avg200 in rows:
                try:
                    a5 = float(avg5) if avg5 is not None else None
                    a200 = float(avg200) if avg200 is not None else None
                    ratio = None
                    if a5 is not None and a200 and a200 != 0:
                        ratio = round(a5 / a200, 2)
                    _volratio_cache[stockname] = ratio  # may be None
                except Exception:
                    continue
            _volratio_last_ts = now
    except Exception:
        pass


def _refresh_days_since_golden_cross_if_needed(symbols: List[str]):
    """Compute how many trading days since the last Golden Cross (SMA50 crossed above SMA200).

    Uses ohlcv_data (timeframe='1d'). We approximate SMA50/SMA200 via rolling averages
    using window functions and detect the most recent index where ratio crossed from <=1 to >1.
    """
    if not test_connection() or pg_cursor is None:
        return
    global _days_since_gc_last_ts
    now = datetime.now()
    # Refresh at most once every 15 minutes
    if _days_since_gc_last_ts and (now - _days_since_gc_last_ts) < timedelta(minutes=15):
        return
    try:
        with pg_cursor() as (cur, _):
            # Compute rolling SMA50 and SMA200 and detect last golden cross per symbol
            cur.execute(
                """
                WITH ordered AS (
                    SELECT stockname, candle_stock, close,
                           ROW_NUMBER() OVER (PARTITION BY stockname ORDER BY candle_stock) AS rn
                    FROM ohlcv_data
                    WHERE timeframe='1d'
                ),
                ma AS (
                    SELECT stockname, candle_stock,
                           AVG(close) OVER (PARTITION BY stockname ORDER BY candle_stock ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50,
                           AVG(close) OVER (PARTITION BY stockname ORDER BY candle_stock ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200
                    FROM ordered
                ),
                ratio AS (
                    SELECT stockname, candle_stock,
                           CASE WHEN sma200 IS NULL OR sma200 = 0 THEN NULL ELSE sma50 / sma200 END AS r
                    FROM ma
                ),
                crossed AS (
                    SELECT stockname, candle_stock, r,
                           LAG(r) OVER (PARTITION BY stockname ORDER BY candle_stock) AS r_prev
                    FROM ratio
                ),
                last_gc AS (
                    SELECT stockname,
                           MAX(candle_stock) AS last_gc_ts
                    FROM crossed
                    WHERE r_prev <= 1 AND r > 1
                    GROUP BY stockname
                ),
                last_day AS (
                    SELECT stockname, MAX(candle_stock) AS last_day_ts
                    FROM ohlcv_data
                    WHERE timeframe='1d'
                    GROUP BY stockname
                )
                SELECT ld.stockname,
                       CASE WHEN gc.last_gc_ts IS NULL THEN NULL
                            ELSE (
                                SELECT COUNT(*) FROM ohlcv_data d
                                WHERE d.timeframe='1d' AND d.stockname=ld.stockname
                                  AND d.candle_stock > gc.last_gc_ts AND d.candle_stock <= ld.last_day_ts
                            )
                       END AS days_since_gc
                FROM last_day ld
                LEFT JOIN last_gc gc ON gc.stockname = ld.stockname
                """
            )
            rows = cur.fetchall()
        with _days_since_gc_lock:
            _days_since_gc_cache.clear()
            for stockname, days_since in rows:
                try:
                    _days_since_gc_cache[stockname] = int(days_since) if days_since is not None else None
                except Exception:
                    _days_since_gc_cache[stockname] = None
            _days_since_gc_last_ts = now
    except Exception:
        pass


def fetch_ltp() -> Dict[str, Any]:
    """Fetch last price, last close, SMA200 (15m) and daily ratio (SMA50/SMA200).

    Heavy daily MA computations are performed in a background thread and cached.
    15m SMA200 computed from Postgres table `ohlcv_data` if available.
    """
    params = load_params()
    universe_name = params.get("universe_list", "allstocks")
    symbols = load_symbol_list(universe_name)
    kite = get_kite()
    instruments = build_ltp_query(symbols)

    # Instrument token mapping for daily MAs
    inst_csv = params.get('instruments_csv') or INSTRUMENTS_CSV_DEFAULT
    inst_csv_path = os.path.join(REPO_ROOT, inst_csv) if not inst_csv.startswith(REPO_ROOT) else inst_csv
    sym_to_token = _load_instrument_tokens(inst_csv_path)

    # Kick off background thread for daily MAs if not started
    _background_daily_ma_builder(kite, symbols, sym_to_token)

    # Refresh 15m SMA200 cache if needed
    _refresh_sma15m_if_needed(symbols)
    _refresh_daily_volratio_if_needed(symbols)
    _refresh_days_since_golden_cross_if_needed(symbols)

    try:
        quote_data = kite.quote(list(instruments.values()))
    except Exception as e:
        raise RuntimeError(f"Quote API failed: {e}")

    out: Dict[str, Any] = {}
    with _daily_ma_lock:
        daily_cache_snapshot = dict(_daily_ma_cache)
    with _sma15m_lock:
        sma15m_snapshot = dict(_sma15m_cache)
        sma50_15m_snapshot = dict(_sma50_15m_cache)
    high200_15m_snapshot = dict(_high200_15m_cache)
    last15m_snapshot = dict(_last15m_close_cache)
    with _volratio_lock:
        volratio_snapshot = dict(_volratio_cache)
    with _days_since_gc_lock:
        days_since_gc_snapshot = dict(_days_since_gc_cache)

    missing_last_close = 0
    for sym, exch_sym in instruments.items():
        data = quote_data.get(exch_sym) or {}
        last_price = data.get("last_price")
        # last_close ONLY from last completed 15m candle cache; no daily fallback
        last_close = last15m_snapshot.get(sym)
        if last_close is None:
            missing_last_close += 1
        daily = daily_cache_snapshot.get(sym) or {}
        sma200_15m = sma15m_snapshot.get(sym)
        sma50_15m = sma50_15m_snapshot.get(sym)
        high200_15m = high200_15m_snapshot.get(sym)
        vol_ratio = volratio_snapshot.get(sym)
        days_since_gc = days_since_gc_snapshot.get(sym)

        ratio_15m_50_200 = None
        if sma50_15m and sma200_15m and sma200_15m != 0:
            ratio_15m_50_200 = round(sma50_15m / sma200_15m, 4)
        # Percent changes vs 15m SMA200 and daily SMA50
        pct_vs_15m_sma200 = None
        if isinstance(last_price, (int, float)) and isinstance(sma200_15m, (int, float)) and sma200_15m:
            pct_vs_15m_sma200 = round((last_price - sma200_15m) / sma200_15m * 100, 2)
        pct_vs_daily_sma50 = None
        daily_sma50_val = daily.get("sma50")
        if isinstance(last_price, (int, float)) and isinstance(daily_sma50_val, (int, float)) and daily_sma50_val:
            pct_vs_daily_sma50 = round((last_price - daily_sma50_val) / daily_sma50_val * 100, 2)
        # Drawdown from recent 200-candle high (15m) in %
        drawdown_15m_200_pct = None
        if isinstance(last_price, (int, float)) and isinstance(high200_15m, (int, float)) and high200_15m:
            try:
                drawdown_15m_200_pct = round((last_price - high200_15m) / high200_15m * 100, 2)
            except Exception:
                drawdown_15m_200_pct = None
        # Geometric-mean based ranking metric using only deviations vs 15m SMA200 and daily SMA50.
        # Idea: Convert each percentage deviation into a growth factor (1 + pct/100),
        # then take geometric mean of the two and map back to a percentage.
        rank_gm = None
        try:
            if pct_vs_15m_sma200 is not None and pct_vs_daily_sma50 is not None:
                g1 = 1 + (pct_vs_15m_sma200 / 100.0)
                g2 = 1 + (pct_vs_daily_sma50 / 100.0)
                if g1 > 0 and g2 > 0:
                    rank_gm = round(((g1 * g2)**0.5 - 1) * 100.0, 2)
        except Exception:
            rank_gm = None
        out[sym] = {
            "last_price": last_price,
            "last_close": last_close,
            "sma200_15m": sma200_15m,
            "sma50_15m": sma50_15m,
            "ratio_15m_50_200": ratio_15m_50_200,
            "pct_vs_15m_sma200": pct_vs_15m_sma200,
            "pct_vs_daily_sma50": pct_vs_daily_sma50,
            "rank_gm": rank_gm,
            "drawdown_15m_200_pct": drawdown_15m_200_pct,
            "volume_ratio_d5_d200": vol_ratio,
            "days_since_golden_cross": days_since_gc,
            "daily_sma50": daily.get("sma50"),
            "daily_sma200": daily.get("sma200"),
            "daily_ratio_50_200": daily.get("ratio"),
        }
    meta = {
        "daily_ma_ready_count": sum(1 for v in daily_cache_snapshot.values() if v.get("ratio") is not None),
        "sma15m_last_refresh": _sma15m_last_ts.isoformat() if _sma15m_last_ts else None,
        "missing_last_close_15m": missing_last_close,
    }
    return {"universe": universe_name, "count": len(symbols), "meta": meta, "data": out}
