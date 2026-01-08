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
_low200_15m_cache: Dict[str, float] = {}
_last15m_close_cache: Dict[str, float] = {}
_rsi15m_cache: Dict[str, float] = {}
_sma15m_last_ts: Optional[datetime] = None
_sma15m_lock = threading.RLock()

# Daily volume ratio cache: avg(5d volume) / avg(200d volume)
_volratio_cache: Dict[str, float] = {}
_volratio_last_ts: Optional[datetime] = None
_volratio_lock = threading.RLock()

# Daily simple moving averages cache (5,8,10 day)
_daily_avg_cache: Dict[str, Dict[str, Optional[float]]] = {}
_daily_avg_last_ts: Optional[datetime] = None
_daily_avg_lock = threading.RLock()

# Days since last Golden Cross (SMA50 crossed above SMA200) per symbol
_days_since_gc_cache: Dict[str, Optional[int]] = {}
_days_since_gc_last_ts: Optional[datetime] = None
_days_since_gc_lock = threading.RLock()

# Support/Resistance cache
_sr_cache: Dict[str, Dict[str, Any]] = {}
_sr_last_ts: Optional[datetime] = None
_sr_lock = threading.RLock()

# VCP (Volatility Contraction Pattern) cache
_vcp_cache: Dict[str, Dict[str, Any]] = {}
_vcp_last_ts: Optional[datetime] = None
_vcp_lock = threading.RLock()

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
    # Use explicit None checks (avoid truthiness) so valid numeric values like 0 are handled.
    ratio = None
    if sma50 is not None and sma200 is not None and sma200 != 0:
        try:
            ratio = round(sma50 / sma200, 2)
        except Exception:
            ratio = None
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
                    SELECT stockname, high, low,
                           ROW_NUMBER() OVER (PARTITION BY stockname ORDER BY candle_stock DESC) AS rn
                    FROM ohlcv_data
                    WHERE timeframe='15m'
                )
                SELECT stockname,
                       MAX(CASE WHEN rn <= 200 THEN high END) AS high200_15m,
                       MIN(CASE WHEN rn <= 200 THEN low END) AS low200_15m
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
            _low200_15m_cache.clear()
            for stockname, high200, low200 in high_rows:
                try:
                    _high200_15m_cache[stockname] = float(high200) if high200 is not None else None
                except Exception:
                    _high200_15m_cache[stockname] = None
                try:
                    _low200_15m_cache[stockname] = float(low200) if low200 is not None else None
                except Exception:
                    _low200_15m_cache[stockname] = None
            _last15m_close_cache.clear()
            for stockname, close_val in last_rows:
                try:
                    _last15m_close_cache[stockname] = float(close_val)
                except Exception:
                    continue
            # Optionally compute a simple RSI(14) from recent closes if available in DB
            try:
                # Attempt to fetch last 50 closes per symbol to compute RSI(14)
                with pg_cursor() as (cur, _):
                    cur.execute(
                        """
                        SELECT stockname, array_agg(close ORDER BY candle_stock DESC) AS closes
                        FROM ohlcv_data
                        WHERE timeframe='15m'
                        GROUP BY stockname
                        """
                    )
                    rows_closes = cur.fetchall()
                _rsi15m_cache.clear()
                for stockname, closes_arr in rows_closes:
                    try:
                        closes = [float(x) for x in (closes_arr or [])][:50]
                        if len(closes) >= 15:
                            # closes ordered newest first; reverse to chronological
                            closes = list(reversed(closes))
                            # compute RSI(14)
                            gains = 0.0
                            losses = 0.0
                            for i in range(1, 15):
                                diff = closes[i] - closes[i-1]
                                if diff > 0:
                                    gains += diff
                                else:
                                    losses += abs(diff)
                            avg_gain = gains / 14.0
                            avg_loss = losses / 14.0
                            if avg_loss == 0:
                                rsi = 100.0
                            else:
                                rs = avg_gain / avg_loss
                                rsi = 100.0 - (100.0 / (1.0 + rs))
                            _rsi15m_cache[stockname] = round(rsi, 2)
                    except Exception:
                        continue
            except Exception:
                # DB not available or query failed -> leave cache as-is
                pass
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
                    # Explicitly check for None and zero to avoid skipping valid values
                    if a5 is not None and a200 is not None and a200 != 0:
                        try:
                            ratio = round(a5 / a200, 2)
                        except Exception:
                            ratio = None
                    _volratio_cache[stockname] = ratio  # may be None
                except Exception:
                    continue
            _volratio_last_ts = now
    except Exception:
        pass


def _refresh_daily_averages_if_needed(symbols: List[str]):
    """Compute simple moving averages (5,8,10 day) per symbol using daily ohlcv_data.

    Results are cached in _daily_avg_cache and refreshed at most once every 15 minutes.
    """
    if not test_connection() or pg_cursor is None:
        return
    global _daily_avg_last_ts
    now = datetime.now()
    # Refresh at most once every 15 minutes
    if _daily_avg_last_ts and (now - _daily_avg_last_ts) < timedelta(minutes=15):
        return
    try:
        with pg_cursor() as (cur, _):
            # Fetch recent daily closes per requested symbol (newest first)
            cur.execute(
                """
                SELECT stockname, array_agg(close ORDER BY candle_stock DESC) AS closes
                FROM ohlcv_data
                WHERE timeframe='1d' AND stockname = ANY(%s)
                GROUP BY stockname
                """,
                (symbols,)
            )
            rows = cur.fetchall()

        def compute_emas_from_closes(closes_list, periods):
            # closes_list expected chronological oldest->newest
            res = {}
            for p in periods:
                if len(closes_list) < p:
                    res[p] = None
                    continue
                # seed EMA with simple average of first p values
                seed = sum(closes_list[:p]) / float(p)
                ema = seed
                k = 2.0 / (p + 1)
                for price in closes_list[p:]:
                    ema = (price - ema) * k + ema
                res[p] = ema
            return res

        periods = [5, 8, 10, 20, 50, 100, 200]
        with _daily_avg_lock:
            _daily_avg_cache.clear()
            for stockname, closes_arr in rows:
                try:
                    closes_raw = list(closes_arr or [])
                    # convert to floats and reverse to chronological order
                    closes = [float(x) for x in closes_raw]
                    closes = list(reversed(closes))
                    emas = compute_emas_from_closes(closes, periods)
                    entry = {
                        'ema5': round(emas[5], 6) if emas.get(5) is not None else None,
                        'ema8': round(emas[8], 6) if emas.get(8) is not None else None,
                        'ema10': round(emas[10], 6) if emas.get(10) is not None else None,
                        'ema20': round(emas[20], 6) if emas.get(20) is not None else None,
                        'ema50': round(emas[50], 6) if emas.get(50) is not None else None,
                        'ema100': round(emas[100], 6) if emas.get(100) is not None else None,
                        'ema200': round(emas[200], 6) if emas.get(200) is not None else None,
                        'updated': now,
                    }
                    _daily_avg_cache[stockname] = entry
                except Exception:
                    _daily_avg_cache[stockname] = {'ema5': None, 'ema8': None, 'ema10': None, 'ema20': None, 'ema50': None, 'ema100': None, 'ema200': None, 'updated': now}
            _daily_avg_last_ts = now
    except Exception:
        # Ignore DB failures silently; caller will handle missing values
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


def _find_swings_from_closes(closes: List[float], window: int = 6, top_n: int = 3):
    """Return support and resistance levels from closes (newest-first).

    Simple local-extrema method: scan chronological closes and collect local highs/lows.
    """
    if not closes:
        return [], []
    vals = list(reversed(closes))  # chronological
    n = len(vals)
    highs = []
    lows = []
    for i in range(n):
        left = max(0, i - window)
        right = min(n - 1, i + window)
        v = vals[i]
        segment = vals[left:right + 1]
        if v == max(segment):
            highs.append((i, v))
        if v == min(segment):
            lows.append((i, v))
    highs_sorted = sorted(highs, key=lambda x: -x[0])
    lows_sorted = sorted(lows, key=lambda x: -x[0])
    res = []
    sup = []
    seen = set()
    for _, val in highs_sorted:
        if len(res) >= top_n:
            break
        v = round(float(val), 2)
        if v in seen:
            continue
        res.append(v)
        seen.add(v)
    seen = set()
    for _, val in lows_sorted:
        if len(sup) >= top_n:
            break
        v = round(float(val), 2)
        if v in seen:
            continue
        sup.append(v)
        seen.add(v)
    return sup, res


def _refresh_sr_if_needed(symbols: List[str]):
    """Populate `_sr_cache` with supports/resistances for given symbols (refresh every 15 minutes)."""
    if not test_connection() or pg_cursor is None:
        return
    global _sr_last_ts
    now = datetime.now()
    if _sr_last_ts and (now - _sr_last_ts) < timedelta(minutes=15):
        return
    # We'll replace the previous support/resistance computation with a simpler
    # rolling local extrema over the last 30 daily closes: the most recent local
    # low and local high within a 30-day rolling window. Store results in
    # `_sr_cache` as 'local_30d_low' and 'local_30d_high' for backward
    # compatibility with existing code that reads `_sr_cache`.
    if not test_connection() or pg_cursor is None:
        return
    try:
        with pg_cursor() as (cur, _):
            for sym in symbols:
                try:
                    cur.execute(
                        "SELECT array_agg(close ORDER BY candle_stock DESC) FROM ohlcv_data WHERE timeframe='1d' AND stockname=%s",
                        (sym,)
                    )
                    row = cur.fetchone()
                    closes_arr = row[0] if row and row[0] is not None else []
                    # closes are newest-first; convert to floats and keep up to 365*3 as safety
                    closes = [float(x) for x in (closes_arr or [])][:800]
                    if len(closes) == 0:
                        continue
                    # Compute rolling 30-day window local lows/highs using newest-first array
                    window = 30
                    recent = closes[:window]
                    if not recent:
                        continue
                    try:
                        local_low = round(float(min(recent)), 2)
                    except Exception:
                        local_low = None
                    try:
                        local_high = round(float(max(recent)), 2)
                    except Exception:
                        local_high = None
                    # Also compute support/resistance swing levels (older logic) from closes
                    try:
                        supports, resistances = _find_swings_from_closes(closes, window=6, top_n=3)
                    except Exception:
                        supports, resistances = [], []
                    with _sr_lock:
                        # keep both legacy supports/resistances and new local 30d low/high
                        _sr_cache[sym] = {
                            'local_30d_low': local_low,
                            'local_30d_high': local_high,
                            'supports': supports,
                            'resistances': resistances,
                        }
                except Exception:
                    continue
        _sr_last_ts = now
    except Exception:
        pass


def _detect_vcp(closes_arr: List[float], highs_arr: List[float], lows_arr: List[float], lookback: int = 20, min_contractions: int = 3) -> Dict[str, Any]:
    """Detect Volatility Contraction Pattern (VCP) - deterministic, non-repainting.
    
    VCP Logic (Mark Minervini):
    1. Stock shows a series of contracting price ranges (volatility decreases)
    2. Each contraction phase is lower in range than the previous
    3. After 3+ contractions, price breaks out with volume
    4. Entry on breakout above the highest high of the contraction phase
    
    Args:
        closes_arr: List of closing prices (oldest first, newest last)
        highs_arr: List of high prices (oldest first, newest last)
        lows_arr: List of low prices (oldest first, newest last)
        lookback: Number of candles to analyze for VCP pattern
        min_contractions: Minimum number of contractions required (default 3)
    
    Returns:
        Dict with VCP analysis: {
            'has_vcp': bool,
            'stage': str (e.g., 'contraction_1', 'breakout_ready', 'broken_out'),
            'num_contractions': int,
            'contraction_ranges': List[float],
            'consolidation_low': float,
            'consolidation_high': float,
            'breakout_level': float,
            'current_price': float,
            'distance_to_breakout': float (% above breakout level),
            'volatility_trend': str ('decreasing' or 'increasing'),
        }
    """
    result = {
        'has_vcp': False,
        'stage': 'No Pattern',
        'num_contractions': 0,
        'contraction_ranges': [],
        'consolidation_low': None,
        'consolidation_high': None,
        'breakout_level': None,
        'current_price': None,
        'distance_to_breakout': None,
        'volatility_trend': None,
    }
    
    try:
        # Need at least lookback candles
        if not closes_arr or not highs_arr or not lows_arr:
            return result
        
        data_len = min(len(closes_arr), len(highs_arr), len(lows_arr))
        if data_len < lookback:
            return result
        
        # Get the last lookback candles
        recent_closes = closes_arr[-lookback:]
        recent_highs = highs_arr[-lookback:]
        recent_lows = lows_arr[-lookback:]
        
        current_price = recent_closes[-1]
        
        # Divide lookback into chunks to identify contractions
        # Standard VCP: 3-4 contractions, each 10-15 candles, with decreasing range
        chunk_size = max(4, lookback // (min_contractions + 1))  # ~5-7 candles per chunk
        
        contraction_ranges = []
        for i in range(0, lookback - chunk_size, chunk_size):
            chunk_highs = recent_highs[i:i+chunk_size]
            chunk_lows = recent_lows[i:i+chunk_size]
            if chunk_highs and chunk_lows:
                high = max(chunk_highs)
                low = min(chunk_lows)
                rng = high - low
                contraction_ranges.append(rng)
        
        # Check if ranges are contracting (each smaller than previous)
        num_contractions = 0
        if len(contraction_ranges) >= min_contractions:
            for j in range(1, len(contraction_ranges)):
                if contraction_ranges[j] < contraction_ranges[j-1]:
                    num_contractions += 1
                else:
                    break  # Stop counting once contraction breaks
        
        if num_contractions >= (min_contractions - 1):  # At least 2-3 consecutive contractions
            result['has_vcp'] = True
            result['num_contractions'] = num_contractions
            result['contraction_ranges'] = [round(r, 2) for r in contraction_ranges]
            
            # Consolidation level: min/max of the most recent chunk (tightest contraction)
            if contraction_ranges:
                last_chunk_start = len(recent_highs) - chunk_size
                if last_chunk_start < 0:
                    last_chunk_start = 0
                
                consolidation_highs = recent_highs[last_chunk_start:]
                consolidation_lows = recent_lows[last_chunk_start:]
                
                result['consolidation_low'] = round(min(consolidation_lows), 2)
                result['consolidation_high'] = round(max(consolidation_highs), 2)
                result['breakout_level'] = result['consolidation_high']
                
                # Determine stage based on current price vs breakout level
                if current_price > result['breakout_level'] * 1.001:  # Above breakout + 0.1% buffer
                    result['stage'] = 'Broken Out'
                    distance_pct = ((current_price - result['breakout_level']) / result['breakout_level']) * 100
                    result['distance_to_breakout'] = round(distance_pct, 2)
                elif current_price >= result['breakout_level'] * 0.99:  # Within 1% of breakout
                    result['stage'] = 'Breakout Ready / At Level'
                    result['distance_to_breakout'] = 0.0
                else:  # Below breakout
                    result['stage'] = 'Consolidating'
                    distance_pct = ((result['breakout_level'] - current_price) / current_price) * 100
                    result['distance_to_breakout'] = -round(distance_pct, 2)  # Negative = below
            
            # Volatility trend
            if len(contraction_ranges) >= 2:
                if contraction_ranges[-1] < contraction_ranges[-2]:
                    result['volatility_trend'] = 'Decreasing'
                else:
                    result['volatility_trend'] = 'Increasing'
        
        result['current_price'] = round(current_price, 2)
    
    except Exception:
        pass
    
    return result


def _refresh_vcp_if_needed(symbols: List[str]):
    """Compute VCP patterns for given symbols using 15m data from ohlcv_data.
    
    Detects volatility contraction patterns and stores in _vcp_cache.
    Refreshes at most once per 5 minutes.
    """
    if not test_connection() or pg_cursor is None:
        return
    
    global _vcp_last_ts
    now = datetime.now()
    # Refresh at most once per 5 minutes
    if _vcp_last_ts and (now - _vcp_last_ts) < timedelta(minutes=5):
        return
    
    try:
        with pg_cursor() as (cur, _):
            # Fetch last 100 15m candles per symbol (enough for robust VCP detection)
            cur.execute(
                """
                SELECT stockname, 
                       array_agg(close ORDER BY candle_stock DESC) AS closes,
                       array_agg(high ORDER BY candle_stock DESC) AS highs,
                       array_agg(low ORDER BY candle_stock DESC) AS lows
                FROM ohlcv_data
                WHERE timeframe='15m' AND stockname = ANY(%s)
                GROUP BY stockname
                """,
                (symbols,)
            )
            rows = cur.fetchall()
        
        with _vcp_lock:
            _vcp_cache.clear()
            for stockname, closes_raw, highs_raw, lows_raw in rows:
                try:
                    closes = [float(x) for x in (closes_raw or [])]
                    highs = [float(x) for x in (highs_raw or [])]
                    lows = [float(x) for x in (lows_raw or [])]
                    
                    # Reverse to chronological order (oldest first)
                    closes = list(reversed(closes))
                    highs = list(reversed(highs))
                    lows = list(reversed(lows))
                    
                    # Detect VCP using last 80 candles (~13 hours of 15m data)
                    vcp_result = _detect_vcp(closes, highs, lows, lookback=80, min_contractions=3)
                    _vcp_cache[stockname] = vcp_result
                except Exception:
                    _vcp_cache[stockname] = {'has_vcp': False, 'stage': 'Error'}
        
        _vcp_last_ts = now
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
    _refresh_sr_if_needed(symbols)
    _refresh_vcp_if_needed(symbols)

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
    low200_15m_snapshot = dict(_low200_15m_cache)
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
        # Pullback: percent return from recent 200-candle low (15m)
        pullback_15m_200_pct = None
        low200 = low200_15m_snapshot.get(sym)
        if isinstance(last_price, (int, float)) and isinstance(low200, (int, float)) and low200:
            try:
                pullback_15m_200_pct = round((last_price - low200) / low200 * 100, 2)
            except Exception:
                pullback_15m_200_pct = None
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
            # Expose the new local 30-day low/high (if computed) from _sr_cache
            "local_30d_low": _sr_cache.get(sym, {}).get('local_30d_low'),
            "local_30d_high": _sr_cache.get(sym, {}).get('local_30d_high'),
            # Provide legacy supports/resistances for UI tables
            "supports": _sr_cache.get(sym, {}).get('supports'),
            "resistances": _sr_cache.get(sym, {}).get('resistances'),
            "pullback_15m_200_pct": pullback_15m_200_pct,
        }
        # If legacy supports/resistances are not present in _sr_cache, try a quick DB fallback
        try:
            sr_entry = _sr_cache.get(sym, {})
            if not sr_entry.get('supports') and pg_cursor is not None:
                # attempt a lightweight query to fetch recent daily closes and compute swings
                try:
                    with pg_cursor() as (cur, _):
                        cur.execute("SELECT array_agg(close ORDER BY candle_stock DESC) FROM ohlcv_data WHERE timeframe='1d' AND stockname=%s", (sym,))
                        row = cur.fetchone()
                        closes_arr = row[0] if row and row[0] is not None else []
                        closes = [float(x) for x in (closes_arr or [])][:800]
                        if closes:
                            supports, resistances = _find_swings_from_closes(closes, window=6, top_n=3)
                            out[sym]['supports'] = supports
                            out[sym]['resistances'] = resistances
                except Exception:
                    # ignore DB fallback errors
                    pass
            else:
                # ensure keys exist even if None
                out[sym]['supports'] = sr_entry.get('supports')
                out[sym]['resistances'] = sr_entry.get('resistances')
        except Exception:
            # swallow any errors here to avoid breaking LTP API
            pass
    meta = {
        "daily_ma_ready_count": sum(1 for v in daily_cache_snapshot.values() if v.get("ratio") is not None),
        "sma15m_last_refresh": _sma15m_last_ts.isoformat() if _sma15m_last_ts else None,
        "missing_last_close_15m": missing_last_close,
    }
    return {"universe": universe_name, "count": len(symbols), "meta": meta, "data": out}


def get_ck_data() -> Dict[str, Any]:
    """Return minimal CK view data: symbol -> { last_price, rsi_15m }.

    Relies on fetch_ltp() data for last_price and on `_rsi15m_cache` populated
    by `_refresh_sma15m_if_needed` when Postgres is available. If RSI missing,
    value will be null.
    """
    try:
        ltp_resp = fetch_ltp()
    except Exception as e:
        return {"error": str(e)}
    data = ltp_resp.get('data', {})
    out = {}
    # Best-effort refresh of daily averages (EMAs)
    try:
        _refresh_daily_averages_if_needed(list(data.keys()))
    except Exception:
        pass
    for sym, info in data.items():
        last_price = info.get('last_price')
        rsi = _rsi15m_cache.get(sym)
        # Strategy heuristics (CK final):
        # - Bullish setup: 15m sma50 > sma200 and daily sma50 > daily sma200
        # - Pullback: bullish but price below 15m sma200 or below sma50_15m
        # - Valuation stretch: daily_ratio_50_200 significantly > 1 (e.g., >1.08)
        # - Call Tops: if daily ratio is high but 15m momentum weak or rapid losses
        sma50_15m = info.get('sma50_15m')
        sma200_15m = info.get('sma200_15m')
        daily_ratio = info.get('daily_ratio_50_200')
        pct_vs_15m = info.get('pct_vs_15m_sma200')
        days_since_gc = info.get('days_since_golden_cross')

        signal = 'Neutral'
        action = 'Hold'
        try:
            bullish_15m = (isinstance(sma50_15m, (int,float)) and isinstance(sma200_15m, (int,float)) and sma50_15m > sma200_15m)
            bullish_daily = (isinstance(daily_ratio, (int,float)) and daily_ratio > 1.0)
            if bullish_15m and bullish_daily:
                # Strong multi-timeframe alignment
                signal = 'Bullish'
                # If price dipped below 15m SMA200 or sma50_15m -> pullback accumulation
                if isinstance(pct_vs_15m, (int,float)) and pct_vs_15m < 0:
                    action = 'Accumulate on pullback'
                else:
                    action = 'Trade / Add on strength'
            elif bullish_daily and not bullish_15m:
                signal = 'Daily Bull'
                # Daily strong but 15m lag -> watch for weekly follow-up
                action = 'Watch for pullback -> accumulate'
            # Valuation stretch
            if isinstance(daily_ratio, (int,float)) and daily_ratio > 1.08:
                # Mark as stretched; be cautious about adding new positions
                if signal.startswith('Bull'):
                    action = 'Caution: valuation stretched'
                else:
                    signal = 'Stretched'
                    action = 'Defer new buys'
            # Call tops: simple heuristic
            if isinstance(days_since_gc, int) and days_since_gc is not None and days_since_gc <= 5 and isinstance(pct_vs_15m, (int,float)) and pct_vs_15m < -3:
                # recent golden cross but sharp drop on 15m -> possible top/significant distribution
                signal = 'Top Risk'
                action = 'Call Top / Reduce'
        except Exception:
            pass

        out[sym] = {
            "last_price": last_price,
            "rsi_15m": rsi,
            "signal": signal,
            "action": action,
            # include drawdown and pullback so CK view can render and colorize them
            "drawdown_15m_200_pct": info.get('drawdown_15m_200_pct'),
            "pullback_15m_200_pct": info.get('pullback_15m_200_pct'),
        }
        # attach SR and trading zone percent if available
        try:
            sr = _sr_cache.get(sym, {})
            if sr:
                low = sr.get('local_30d_low')
                high = sr.get('local_30d_high')
                # convert to percent-away relative to last_price for CK view
                try:
                    if isinstance(last_price, (int, float)) and isinstance(low, (int, float)) and low:
                        low_pct = round((last_price - low) / low * 100.0, 2)
                    else:
                        low_pct = None
                    if isinstance(last_price, (int, float)) and isinstance(high, (int, float)) and high:
                        # show as positive distance below the high
                        high_pct = round(abs((last_price - high) / high) * 100.0, 2)
                    else:
                        high_pct = None
                except Exception:
                    low_pct = None
                    high_pct = None
                out[sym]['local_30d_low'] = low_pct
                out[sym]['local_30d_high'] = high_pct
                try:
                    if isinstance(low, (int, float)) and isinstance(high, (int, float)) and low != 0:
                        out[sym]['trading_zone_pct'] = round((high - low) / low, 4)
                    else:
                        out[sym]['trading_zone_pct'] = None
                except Exception:
                    out[sym]['trading_zone_pct'] = None
        except Exception:
            pass
        # Attach daily EMAs if available (may be None)
        try:
            with _daily_avg_lock:
                darr = _daily_avg_cache.get(sym) or {}
            out[sym]['ema5'] = darr.get('ema5') if darr else None
            out[sym]['ema8'] = darr.get('ema8') if darr else None
            out[sym]['ema10'] = darr.get('ema10') if darr else None
            out[sym]['ema20'] = darr.get('ema20') if darr else None
            out[sym]['ema50'] = darr.get('ema50') if darr else None
            out[sym]['ema100'] = darr.get('ema100') if darr else None
            out[sym]['ema200'] = darr.get('ema200') if darr else None
        except Exception:
            out[sym]['ema5'] = None
            out[sym]['ema8'] = None
            out[sym]['ema10'] = None
            out[sym]['ema20'] = None
            out[sym]['ema50'] = None
            out[sym]['ema100'] = None
            out[sym]['ema200'] = None
    return {"count": len(out), "data": out}


def get_vcp_data() -> Dict[str, Any]:
    """Return VCP (Volatility Contraction Pattern) breakout data.
    
    Returns symbols with active VCP patterns ranked by:
    1. Status (Broken Out > Breakout Ready > Consolidating)
    2. Distance to breakout (closest first)
    3. Number of contractions
    
    Uses 15m candle data analyzed from ohlcv_data.
    """
    try:
        ltp_resp = fetch_ltp()
    except Exception as e:
        return {"error": str(e)}
    
    data = ltp_resp.get('data', {})
    
    with _vcp_lock:
        vcp_snapshot = dict(_vcp_cache)
    
    out = {}
    for sym in data.keys():
        vcp_info = vcp_snapshot.get(sym, {})
        last_price = data[sym].get('last_price')
        
        # Only include symbols with active VCP patterns
        if vcp_info.get('has_vcp'):
            out[sym] = {
                'last_price': last_price,
                'stage': vcp_info.get('stage'),
                'num_contractions': vcp_info.get('num_contractions'),
                'consolidation_low': vcp_info.get('consolidation_low'),
                'consolidation_high': vcp_info.get('consolidation_high'),
                'breakout_level': vcp_info.get('breakout_level'),
                'current_price': vcp_info.get('current_price'),
                'distance_to_breakout': vcp_info.get('distance_to_breakout'),
                'volatility_trend': vcp_info.get('volatility_trend'),
                'contraction_ranges': vcp_info.get('contraction_ranges'),
                # Include CK signal for context
                'ck_signal': data[sym].get('signal'),
                'ck_action': data[sym].get('action'),
            }
    
    # Sort by stage priority and distance to breakout
    stage_priority = {'Broken Out': 0, 'Breakout Ready / At Level': 1, 'Consolidating': 2}
    
    def sort_key(item):
        sym, vcp_data = item
        stage = vcp_data.get('stage', 'Consolidating')
        priority = stage_priority.get(stage, 99)
        distance = vcp_data.get('distance_to_breakout') or 0
        # Closer to breakout (lower absolute distance) ranks higher
        return (priority, abs(distance))
    
    sorted_symbols = sorted(out.items(), key=sort_key)
    sorted_data = {sym: vcp_data for sym, vcp_data in sorted_symbols}
    
    return {
        "count": len(sorted_data),
        "last_updated": _vcp_last_ts.isoformat() if _vcp_last_ts else None,
        "data": sorted_data
    }

