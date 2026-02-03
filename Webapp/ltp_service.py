"""LTP data service utilities.

Separates data loading and Kite interaction from Flask routes.
"""
from __future__ import annotations

import os
import sys
import importlib.util
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from src.ranking import calculate_acceleration, calculate_rank_final  # type: ignore

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

# Setup logger (INFO level to reduce noise)
logger = logging.getLogger("ltp_service")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s [LTP_SERVICE] %(levelname)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

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

# LTP API Request Caching (2-second TTL to prevent rate limiting)
_ltp_cache: Dict[str, Any] = {}
_ltp_cache_ts: Optional[datetime] = None
_ltp_cache_lock = threading.RLock()
_LTP_CACHE_TTL = 2  # seconds

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

# Small cache to keep previous Rank_GM per-symbol for acceleration calc
_rank_gm_cache: Dict[str, float] = {}
_rank_gm_lock = threading.RLock()

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
    sma20 = sum(closes_sorted[-20:]) / 20 if len(closes_sorted) >= 20 else None
    sma50 = sum(closes_sorted[-50:]) / 50 if len(closes_sorted) >= 50 else None
    sma200 = sum(closes_sorted[-200:]) / 200 if len(closes_sorted) >= 200 else None
    # Use explicit None checks (avoid truthiness) so valid numeric values like 0 are handled.
    ratio = None
    if sma50 is not None and sma200 is not None and sma200 != 0:
        try:
            ratio = round(sma50 / sma200, 2)
        except Exception:
            ratio = None
    return {"sma20": sma20, "sma50": sma50, "sma200": sma200, "ratio": ratio, "updated": datetime.now()}


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


def _compute_emas_from_closes(closes_list, periods):
    """Calculate multiple EMAs from a chronological list of closes (oldest -> newest).
    
    Args:
        closes_list: List of floats in chronological order (oldest first)
        periods: List of periods to compute (e.g., [5, 8, 10, 20, 50, 100, 200])
    
    Returns:
        Dictionary mapping period -> EMA value (or None if insufficient data)
    """
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
        res[p] = round(ema, 2)  # Round to 2 decimal places for consistency
    return res


def _refresh_daily_averages_if_needed(symbols: List[str]):
    """Compute Exponential Moving Averages (5,8,10,20,50,100,200 day) per symbol using daily ohlcv_data.

    Results are cached in _daily_avg_cache and refreshed at most once every 15 minutes.
    Loads closing price data from PostgreSQL ohlcv_data table.
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
            # Fetch recent daily closes per requested symbol (newest first, up to 500 candles)
            cur.execute(
                """
                SELECT stockname, array_agg(close ORDER BY candle_stock DESC) AS closes
                FROM (
                    SELECT stockname, close, candle_stock
                    FROM ohlcv_data
                    WHERE timeframe='1d' AND stockname = ANY(%s)
                    ORDER BY stockname, candle_stock DESC
                ) subq
                GROUP BY stockname
                """,
                (symbols,)
            )
            rows = cur.fetchall()
            logging.info(f"[EMA REFRESH] Fetched daily data for {len(rows)} symbols out of {len(symbols)} requested")

        periods = [5, 8, 10, 20, 50, 100, 200]
        with _daily_avg_lock:
            # Keep existing cache for symbols not in this batch
            for stockname, closes_arr in rows:
                try:
                    closes_raw = list(closes_arr or [])
                    if not closes_raw:
                        # No data for this symbol
                        _daily_avg_cache[stockname] = {
                            'ema5': None, 'ema8': None, 'ema10': None, 'ema20': None,
                            'ema50': None, 'ema100': None, 'ema200': None, 'updated': now
                        }
                        continue
                    # Limit to last 500 candles (already sorted newest-first from DB)
                    closes_raw = closes_raw[:500]
                    # convert to floats and reverse to chronological order (oldest first)
                    # Handle Decimal objects from PostgreSQL
                    closes = [float(x) for x in closes_raw]
                    closes = list(reversed(closes))
                    
                    # Compute all EMAs at once
                    emas = _compute_emas_from_closes(closes, periods)
                    
                    entry = {
                        'ema5': emas.get(5),
                        'ema8': emas.get(8),
                        'ema10': emas.get(10),
                        'ema20': emas.get(20),
                        'ema50': emas.get(50),
                        'ema100': emas.get(100),
                        'ema200': emas.get(200),
                        'updated': now,
                        'candles_used': len(closes),  # Track how many candles were used
                    }
                    _daily_avg_cache[stockname] = entry
                except Exception as e:
                    # Log error but don't crash; set Nones
                    _daily_avg_cache[stockname] = {
                        'ema5': None, 'ema8': None, 'ema10': None, 'ema20': None,
                        'ema50': None, 'ema100': None, 'ema200': None, 'updated': now, 'error': str(e)
                    }
            _daily_avg_last_ts = now
    except Exception as e:
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


# =========================
# VCP CONFIGURATION
# =========================
VCP_EMA_TREND = 50
VCP_EMA_EXIT = 20
VCP_ATR_PERIOD = 14
VCP_VOL_MA = 20
VCP_LOOKBACK = 60
VCP_MIN_CONTRACTIONS = 3
VCP_VOL_MULTIPLIER = 1.5
VCP_RISK_REWARD = 3.0


def _ema(series: List[float], period: int) -> List[float]:
    """Calculate Exponential Moving Average."""
    if not series or len(series) < period:
        return []
    
    result = []
    multiplier = 2 / (period + 1)
    
    # Start with SMA for first period values
    sma = sum(series[:period]) / period
    result.extend([None] * (period - 1))
    result.append(sma)
    
    # EMA calculation
    for i in range(period, len(series)):
        ema_val = (series[i] - result[-1]) * multiplier + result[-1]
        result.append(ema_val)
    
    return result


def _atr(highs: List[float], lows: List[float], closes: List[float], period: int) -> List[float]:
    """Calculate Average True Range."""
    if not highs or len(highs) < period + 1:
        return []
    
    tr_list = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr_list.append(tr)
    
    # Simple moving average of TR
    atr_list = [None] * period
    for i in range(period, len(tr_list)):
        atr_val = sum(tr_list[i-period+1:i+1]) / period
        atr_list.append(atr_val)
    
    return atr_list


def _detect_vcp(
    closes_arr: List[float], 
    highs_arr: List[float], 
    lows_arr: List[float],
    opens_arr: Optional[List[float]] = None,
    volumes_arr: Optional[List[float]] = None,
    lookback: int = VCP_LOOKBACK,
    min_contractions: int = VCP_MIN_CONTRACTIONS
) -> Dict[str, Any]:
    """Detect Volatility Contraction Pattern (VCP) - Mark Minervini style.
    
    This is a comprehensive VCP detector with:
    ✔ EMA(50) trend filter
    ✔ 3-step volatility contraction
    ✔ Higher lows validation  
    ✔ ATR contraction
    ✔ Volume expansion breakout (if volume data available)
    ✔ False breakout rejection (if open data available)
    ✔ Stop-loss & 3R target
    ✔ Confidence scoring
    ✔ No repainting / no future bars
    
    Args:
        closes_arr: List of closing prices (oldest first, newest last)
        highs_arr: List of high prices
        lows_arr: List of low prices
        opens_arr: Optional list of open prices (for false breakout filter)
        volumes_arr: Optional list of volumes (for volume confirmation)
        lookback: Number of candles to analyze
        min_contractions: Minimum contraction phases required
    
    Returns:
        Dict with VCP analysis including entry, stop_loss, target, confidence
    """
    result = {
        'has_vcp': False,
        'stage': 'No Pattern',
        'pattern_stage': 'No Pattern Detected',
        'entry_signal': 'NONE',
        'num_contractions': 0,
        'contraction_ranges': [],
        'consolidation_low': None,
        'consolidation_high': None,
        'breakout_level': None,
        'current_price': None,
        'distance_to_breakout': None,
        'volatility_trend': None,
        'entry_price': None,
        'stop_loss': None,
        'target': None,
        'risk_reward': None,
        'confidence_score': None,
    }
    
    try:
        # Need minimum data
        if not closes_arr or not highs_arr or not lows_arr:
            return result
        
        data_len = min(len(closes_arr), len(highs_arr), len(lows_arr))
        if data_len < lookback:
            lookback = max(30, data_len - 5)
        
        if data_len < 30:
            return result
        
        # Get recent data
        closes = closes_arr[-lookback:]
        highs = highs_arr[-lookback:]
        lows = lows_arr[-lookback:]
        opens = opens_arr[-lookback:] if opens_arr and len(opens_arr) >= lookback else None
        volumes = volumes_arr[-lookback:] if volumes_arr and len(volumes_arr) >= lookback else None
        
        current_price = closes[-1]
        result['current_price'] = round(current_price, 2)
        
        # -------------------------
        # 1. TREND FILTER - EMA(50)
        # -------------------------
        ema50 = _ema(closes, VCP_EMA_TREND)
        ema20 = _ema(closes, VCP_EMA_EXIT)
        atr_values = _atr(highs, lows, closes, VCP_ATR_PERIOD)
        
        if not ema50 or ema50[-1] is None:
            return result
        
        # Price must be above EMA50
        if current_price <= ema50[-1]:
            result['pattern_stage'] = 'Below EMA50 - No Uptrend'
            return result
        
        # EMA50 must be rising (compare last vs 5 bars ago)
        if len(ema50) >= 6 and ema50[-1] is not None and ema50[-6] is not None:
            if ema50[-1] <= ema50[-6]:
                result['pattern_stage'] = 'EMA50 Not Rising - Weak Trend'
                return result
        
        # -------------------------
        # 2. FIND CONTRACTIONS
        # -------------------------
        chunk_size = lookback // min_contractions
        contractions = []
        
        for i in range(min_contractions):
            start_idx = i * chunk_size
            end_idx = (i + 1) * chunk_size
            
            seg_highs = highs[start_idx:end_idx]
            seg_lows = lows[start_idx:end_idx]
            seg_closes = closes[start_idx:end_idx]
            
            if not seg_highs or not seg_lows:
                continue
            
            seg_high = max(seg_highs)
            seg_low = min(seg_lows)
            seg_range = seg_high - seg_low
            
            # Get ATR for this segment
            seg_atr = None
            if atr_values:
                valid_atrs = [a for a in atr_values[start_idx:end_idx] if a is not None]
                if valid_atrs:
                    seg_atr = sum(valid_atrs) / len(valid_atrs)
            
            contractions.append({
                'range': seg_range,
                'atr': seg_atr,
                'low': seg_low,
                'high': seg_high
            })
        
        if len(contractions) < min_contractions:
            result['pattern_stage'] = f'Insufficient Data ({len(contractions)}/{min_contractions} segments)'
            return result
        
        # -------------------------
        # 3. VOLATILITY CONTRACTION CHECK
        # -------------------------
        valid_contractions = 0
        contraction_ranges = []
        
        for i in range(1, len(contractions)):
            range_contracting = contractions[i]['range'] < contractions[i-1]['range']
            atr_contracting = True
            higher_lows = contractions[i]['low'] >= contractions[i-1]['low'] * 0.995  # Allow 0.5% tolerance
            
            if contractions[i]['atr'] is not None and contractions[i-1]['atr'] is not None:
                atr_contracting = contractions[i]['atr'] < contractions[i-1]['atr']
            
            if range_contracting and higher_lows:
                valid_contractions += 1
                contraction_ranges.append(round(contractions[i]['range'], 2))
            else:
                break  # Stop on first failure
        
        # Need at least (min_contractions - 1) valid contractions
        if valid_contractions < min_contractions - 1:
            result['pattern_stage'] = f'Weak Contraction ({valid_contractions}/{min_contractions-1} valid)'
            result['contraction_ranges'] = contraction_ranges
            return result
        
        # Pattern found - calculate levels
        result['num_contractions'] = valid_contractions
        result['contraction_ranges'] = contraction_ranges
        
        # Resistance = highest high across all contractions
        resistance = max(c['high'] for c in contractions)
        result['consolidation_high'] = round(resistance, 2)
        result['breakout_level'] = round(resistance, 2)
        
        # Support = lowest low of tightest contraction
        result['consolidation_low'] = round(contractions[-1]['low'], 2)
        
        # Volatility trend
        if len(contraction_ranges) >= 2:
            result['volatility_trend'] = 'Decreasing' if contraction_ranges[-1] < contraction_ranges[0] else 'Increasing'
        else:
            result['volatility_trend'] = 'Decreasing'
        
        # -------------------------
        # 4. BREAKOUT CHECK
        # -------------------------
        breakout_confirmed = False
        volume_confirmed = True  # Assume true if no volume data
        candle_quality = True  # Assume true if no open data
        
        # Check if price broke resistance
        if current_price > resistance:
            breakout_confirmed = True
            
            # Volume confirmation (if data available)
            if volumes:
                vol_ma = sum(volumes[-VCP_VOL_MA:]) / VCP_VOL_MA if len(volumes) >= VCP_VOL_MA else sum(volumes) / len(volumes)
                current_vol = volumes[-1]
                volume_confirmed = current_vol > vol_ma * VCP_VOL_MULTIPLIER
                result['volume_ratio'] = round(current_vol / vol_ma, 2) if vol_ma > 0 else None
            
            # False breakout filter (check candle body vs range)
            if opens:
                candle_range = highs[-1] - lows[-1]
                candle_body = abs(closes[-1] - opens[-1])
                candle_quality = candle_body >= 0.6 * candle_range if candle_range > 0 else True
        
        # -------------------------
        # 5. DETERMINE STAGE & SIGNAL
        # -------------------------
        latest_atr = atr_values[-1] if atr_values and atr_values[-1] is not None else (resistance - contractions[-1]['low']) * 0.1
        
        if breakout_confirmed and volume_confirmed and candle_quality:
            # FULL BREAKOUT - BUY SIGNAL
            result['has_vcp'] = True
            result['stage'] = 'Broken Out'
            result['pattern_stage'] = 'Phase 4: Breakout Confirmed ✓'
            result['entry_signal'] = 'BUY'
            
            # Risk Management
            entry = current_price
            stop = contractions[-1]['low'] - latest_atr
            target = entry + VCP_RISK_REWARD * (entry - stop)
            
            result['entry_price'] = round(entry, 2)
            result['stop_loss'] = round(stop, 2)
            result['target'] = round(target, 2)
            result['risk_reward'] = VCP_RISK_REWARD
            
            # Confidence score based on volume
            if volumes:
                vol_ma = sum(volumes[-VCP_VOL_MA:]) / VCP_VOL_MA if len(volumes) >= VCP_VOL_MA else 1
                confidence = min(1.0, (volumes[-1] / vol_ma) / 3) if vol_ma > 0 else 0.5
            else:
                confidence = 0.6  # Moderate confidence without volume
            result['confidence_score'] = round(confidence, 2)
            
            distance_pct = ((current_price - resistance) / resistance) * 100
            result['distance_to_breakout'] = round(distance_pct, 2)
            
        elif breakout_confirmed and (not volume_confirmed or not candle_quality):
            # Breakout but weak - needs confirmation
            result['has_vcp'] = True
            result['stage'] = 'Breakout Ready / At Level'
            result['pattern_stage'] = 'Phase 3: Breakout Needs Confirmation'
            result['entry_signal'] = 'WATCH'
            
            result['entry_price'] = round(resistance * 1.005, 2)  # Suggest entry slightly above
            result['stop_loss'] = round(contractions[-1]['low'] - latest_atr, 2)
            result['target'] = round(result['entry_price'] + VCP_RISK_REWARD * (result['entry_price'] - result['stop_loss']), 2)
            result['risk_reward'] = VCP_RISK_REWARD
            result['confidence_score'] = 0.4
            result['distance_to_breakout'] = 0.0
            
        elif current_price >= resistance * 0.98:
            # Within 2% of breakout - ready
            result['has_vcp'] = True
            result['stage'] = 'Breakout Ready / At Level'
            result['pattern_stage'] = f'Phase 3: At Resistance ({valid_contractions} contractions)'
            result['entry_signal'] = 'WATCH'
            
            result['entry_price'] = round(resistance * 1.005, 2)
            result['stop_loss'] = round(contractions[-1]['low'] - latest_atr, 2)
            result['target'] = round(result['entry_price'] + VCP_RISK_REWARD * (result['entry_price'] - result['stop_loss']), 2)
            result['risk_reward'] = VCP_RISK_REWARD
            result['confidence_score'] = 0.5
            
            distance_pct = ((resistance - current_price) / current_price) * 100
            result['distance_to_breakout'] = -round(distance_pct, 2)
            
        else:
            # Still consolidating
            result['has_vcp'] = True
            result['stage'] = 'Consolidating'
            result['pattern_stage'] = f'Phase {min(valid_contractions + 1, 3)}: Consolidating ({valid_contractions} contractions)'
            result['entry_signal'] = 'WAIT'
            
            result['entry_price'] = round(resistance * 1.005, 2)
            result['stop_loss'] = round(contractions[-1]['low'] - latest_atr, 2)
            result['target'] = round(result['entry_price'] + VCP_RISK_REWARD * (result['entry_price'] - result['stop_loss']), 2)
            result['risk_reward'] = VCP_RISK_REWARD
            result['confidence_score'] = 0.3
            
            distance_pct = ((resistance - current_price) / current_price) * 100
            result['distance_to_breakout'] = -round(distance_pct, 2)
    
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
            # Fetch last 100 15m candles per symbol with OHLCV data
            cur.execute(
                """
                SELECT stockname, 
                       array_agg(open ORDER BY candle_stock DESC) AS opens,
                       array_agg(high ORDER BY candle_stock DESC) AS highs,
                       array_agg(low ORDER BY candle_stock DESC) AS lows,
                       array_agg(close ORDER BY candle_stock DESC) AS closes,
                       array_agg(volume ORDER BY candle_stock DESC) AS volumes
                FROM ohlcv_data
                WHERE timeframe='15m' AND stockname = ANY(%s)
                GROUP BY stockname
                """,
                (symbols,)
            )
            rows = cur.fetchall()
        
        with _vcp_lock:
            _vcp_cache.clear()
            for stockname, opens_raw, highs_raw, lows_raw, closes_raw, volumes_raw in rows:
                try:
                    opens = [float(x) for x in (opens_raw or []) if x is not None]
                    highs = [float(x) for x in (highs_raw or []) if x is not None]
                    lows = [float(x) for x in (lows_raw or []) if x is not None]
                    closes = [float(x) for x in (closes_raw or []) if x is not None]
                    volumes = [float(x) for x in (volumes_raw or []) if x is not None]
                    
                    # Reverse to chronological order (oldest first)
                    opens = list(reversed(opens))
                    highs = list(reversed(highs))
                    lows = list(reversed(lows))
                    closes = list(reversed(closes))
                    volumes = list(reversed(volumes))
                    
                    # Detect VCP using advanced algorithm with all OHLCV data
                    vcp_result = _detect_vcp(
                        closes_arr=closes, 
                        highs_arr=highs, 
                        lows_arr=lows,
                        opens_arr=opens,
                        volumes_arr=volumes,
                        lookback=VCP_LOOKBACK, 
                        min_contractions=VCP_MIN_CONTRACTIONS
                    )
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
    
    Includes request caching to prevent hitting Zerodha API rate limits.
    """
    global _ltp_cache, _ltp_cache_ts
    
    # Check if cache is still valid
    with _ltp_cache_lock:
        if _ltp_cache_ts and (datetime.now() - _ltp_cache_ts).total_seconds() < _LTP_CACHE_TTL:
            return _ltp_cache.copy()
    
    params = load_params()
    universe_name = params.get("universe_list", "allstocks")
    symbols = load_symbol_list(universe_name)
    kite = get_kite()
    
    # Set longer timeout IMMEDIATELY to prevent timeout errors across all Kite API calls
    kite.timeout = 30
    
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

    # Fetch quotes with retry logic for transient errors (timeouts, gateway errors,
    # and HTML/JSON-parse responses from the Kite endpoints). Treat server-side
    # 502/503/504 and HTML responses as transient and retry with backoff.
    quote_data = None
    max_retries = 3
    retry_delay = 1  # seconds

    transient_indicators = [
        "Read timed out",
        "timeout",
        "Couldn't parse the JSON response",
        "<html>",
        "504 Gateway",
        "502 Bad Gateway",
        "503 Service Unavailable",
        "gateway time-out",
        "kt-quotes",
    ]

    for attempt in range(max_retries):
        try:
            quote_data = kite.quote(list(instruments.values()))
            break  # Success, exit retry loop
        except Exception as e:
            error_msg = str(e) or repr(e)
            lower_err = error_msg.lower()
            is_transient = any(ind.lower() in lower_err for ind in transient_indicators)

            if is_transient:
                # Retry for transient conditions (including HTML 502/504 responses which
                # the requests JSON parser can't handle). Use exponential backoff.
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (attempt + 1)
                    logger.warning(
                        f"Quote API transient error (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {error_msg}"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error(f"Quote API failed after {max_retries} retries: {error_msg}")
                    raise RuntimeError(f"Quote API failed after {max_retries} retries: {error_msg}")
            else:
                # Non-transient error: log full message and re-raise as runtime error
                logger.error(f"Quote API failed (non-transient): {error_msg}")
                raise RuntimeError(f"Quote API failed: {error_msg}")

    out: Dict[str, Any] = {}
    with _daily_ma_lock:
        daily_cache_snapshot = dict(_daily_ma_cache)
    with _daily_avg_lock:
        daily_avg_snapshot = dict(_daily_avg_cache)
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
        # Prefer the computed daily EMA(20) from _daily_avg_cache (ema20).
        # _daily_ma_cache contains sma50/sma200 from Kite historical fetches but not ema20.
        daily_emas = daily_avg_snapshot.get(sym) or {}
        sma200_15m = sma15m_snapshot.get(sym)
        sma50_15m = sma50_15m_snapshot.get(sym)
        high200_15m = high200_15m_snapshot.get(sym)
        vol_ratio = volratio_snapshot.get(sym)
        days_since_gc = days_since_gc_snapshot.get(sym)

        ratio_15m_50_200 = None
        if sma50_15m and sma200_15m and sma200_15m != 0:
            ratio_15m_50_200 = round(sma50_15m / sma200_15m, 4)
        # Percent changes vs 15m SMA50 and daily SMA20 (updated per new ranking)
        pct_vs_15m_sma50 = None
        if isinstance(last_price, (int, float)) and isinstance(sma50_15m, (int, float)) and sma50_15m:
            pct_vs_15m_sma50 = round((last_price - sma50_15m) / sma50_15m * 100, 2)
        pct_vs_daily_sma20 = None
        # Prefer daily SMA20 (20-day simple moving average) — treat it like the daily SMA50
        # Fallback to EMA(20) only if SMA20 is not available.
        daily_sma20_val = daily.get("sma20") if daily.get("sma20") is not None else daily_emas.get('ema20')
        if isinstance(last_price, (int, float)) and isinstance(daily_sma20_val, (int, float)) and daily_sma20_val:
            pct_vs_daily_sma20 = round((last_price - daily_sma20_val) / daily_sma20_val * 100, 2)
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
        # Geometric-mean based ranking metric using deviations vs 15m SMA50 and daily SMA20.
        # Idea: Convert each percentage deviation into a growth factor (1 + pct/100),
        # then take geometric mean of the two and map back to a percentage.
        # Note: if one of the inputs is missing (common when daily EMA/SMA data is not available),
        # treat the missing percentage as 0.0 so we can still compute a sensible Rank_GM from
        # the available data. This prevents `rank_gm` from being null on the frontend.
        rank_gm = None
        try:
            p15 = pct_vs_15m_sma50 if pct_vs_15m_sma50 is not None else 0.0
            pdaily = pct_vs_daily_sma20 if pct_vs_daily_sma20 is not None else 0.0
            g1 = 1 + (p15 / 100.0)
            g2 = 1 + (pdaily / 100.0)
            if g1 > 0 and g2 > 0:
                rank_gm = round(((g1 * g2)**0.5 - 1) * 100.0, 2)
        except Exception:
            rank_gm = None
        # Calculate acceleration (delta vs previous Rank_GM stored in local cache)
        acceleration = None
        rank_final = None
        try:
            with _rank_gm_lock:
                prev = _rank_gm_cache.get(sym)
            if rank_gm is not None:
                acceleration = None if prev is None else round(rank_gm - prev, 2)
                # compute final score using same accel_weight as ranking module
                rank_final = calculate_rank_final(rank_gm, acceleration, accel_weight=0.3)
                # update cache
                with _rank_gm_lock:
                    _rank_gm_cache[sym] = rank_gm
        except Exception:
            acceleration = None
            rank_final = rank_gm
        out[sym] = {
            "last_price": last_price,
            "last_close": last_close,
            "sma200_15m": sma200_15m,
            "sma50_15m": sma50_15m,
            "ratio_15m_50_200": ratio_15m_50_200,
            "pct_vs_15m_sma50": pct_vs_15m_sma50,
            "pct_vs_daily_sma20": pct_vs_daily_sma20,
            "rank_gm": rank_gm,
            "acceleration": acceleration,
            "rank_final": rank_final,
            "drawdown_15m_200_pct": drawdown_15m_200_pct,
            "volume_ratio_d5_d200": vol_ratio,
            "days_since_golden_cross": days_since_gc,
            "daily_sma20": daily_sma20_val,
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
    result = {"universe": universe_name, "count": len(symbols), "meta": meta, "data": out}
    
    # Cache the result to prevent rate limiting on rapid successive requests
    with _ltp_cache_lock:
        _ltp_cache = result.copy()
        _ltp_cache_ts = datetime.now()
    
    return result


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
    
    # Get positions data to include position count
    positions_count = {}
    try:
        kite = get_kite()
        pos_data = kite.positions() or {}
        for pos_type in ['net', 'day']:
            if pos_type in pos_data:
                for pos in pos_data[pos_type]:
                    symbol = pos.get('tradingsymbol', '').replace('NSE:', '')
                    if symbol:
                        qty = abs(pos.get('quantity', 0))
                        positions_count[symbol] = positions_count.get(symbol, 0) + qty
    except Exception as e:
        logging.warning("Failed to fetch positions for CK data: %s", e)
    
    # Best-effort refresh of daily averages (EMAs)
    try:
        _refresh_daily_averages_if_needed(list(data.keys()))
    except Exception as e:
        logging.warning("Failed to refresh daily averages: %s", e)
    
    # Check if we have any daily EMA data; if not, log a warning for debugging
    with _daily_avg_lock:
        daily_avg_snapshot = dict(_daily_avg_cache)
    
    # Count how many symbols have EMAs
    symbols_with_ema = sum(1 for v in daily_avg_snapshot.values() if v.get('ema5') is not None)
    symbols_without_ema = len(daily_avg_snapshot) - symbols_with_ema
    
    if symbols_without_ema > 0:
        logging.warning(f"[EMA STATUS] {symbols_with_ema} symbols have EMA data, {symbols_without_ema} symbols missing. "
                       f"If missing, run: python3 Core_files/download_daily_2y_to_db.py")
    
    for sym, info in data.items():
        last_price = info.get('last_price')
        rsi = _rsi15m_cache.get(sym)
        position_count = positions_count.get(sym, 0)

        # Strategy heuristics (CK final):
        # - Bullish setup: 15m sma50 > sma200 and daily sma50 > daily sma200
        # - Pullback: bullish but price below 15m sma200 or below sma50_15m
        # - Valuation stretch: daily_ratio_50_200 significantly > 1 (e.g., >1.08)
        # - Call Tops: if daily ratio is high but 15m momentum weak or rapid losses
        sma50_15m = info.get('sma50_15m')
        sma200_15m = info.get('sma200_15m')
        daily_ratio = info.get('daily_ratio_50_200')
        pct_vs_15m = info.get('pct_vs_15m_sma50')
        days_since_gc = info.get('days_since_golden_cross')

        signal = 'Neutral'
        action = 'Hold'
        try:
            bullish_15m = (
                isinstance(sma50_15m, (int, float))
                and isinstance(sma200_15m, (int, float))
                and sma50_15m > sma200_15m
            )
            bullish_daily = (isinstance(daily_ratio, (int, float)) and daily_ratio > 1.0)

            if bullish_15m and bullish_daily:
                # Strong multi-timeframe alignment
                signal = 'Bullish'
                # If price dipped below 15m SMA200 or sma50_15m -> pullback accumulation
                if isinstance(pct_vs_15m, (int, float)) and pct_vs_15m < 0:
                    action = 'Accumulate on pullback'
                else:
                    action = 'Trade / Add on strength'
            elif bullish_daily and not bullish_15m:
                signal = 'Daily Bull'
                # Daily strong but 15m lag -> watch for weekly follow-up
                action = 'Watch for pullback -> accumulate'

            # Valuation stretch
            if isinstance(daily_ratio, (int, float)) and daily_ratio > 1.08:
                # Mark as stretched; be cautious about adding new positions
                if isinstance(signal, str) and signal.startswith('Bull'):
                    action = 'Caution: valuation stretched'
                else:
                    signal = 'Stretched'
                    action = 'Defer new buys'

            # Call tops: simple heuristic
            if (
                isinstance(days_since_gc, int)
                and days_since_gc is not None
                and days_since_gc <= 5
                and isinstance(pct_vs_15m, (int, float))
                and pct_vs_15m < -3
            ):
                # recent golden cross but sharp drop on 15m -> possible top/significant distribution
                signal = 'Top Risk'
                action = 'Call Top / Reduce'

        except Exception:
            # Fail-safe: leave defaults
            pass

        out[sym] = {
            "last_price": last_price,
            "rsi_15m": rsi,
            "position_count": position_count,
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
    """Return VCP (Volatility Contraction Pattern) breakout data for all stocks.
    
    Returns all symbols with their VCP analysis (active patterns first), ranked by:
    1. Active pattern status (has_vcp: True first)
    2. Status (Broken Out > Breakout Ready > Consolidating > No Pattern)
    3. Distance to breakout (closest first)
    4. Number of contractions
    
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
        
        # Include all symbols with their VCP analysis
        out[sym] = {
            'last_price': last_price,
            'stage': vcp_info.get('stage', 'No Pattern'),
            'pattern_stage': vcp_info.get('pattern_stage', 'No Pattern Detected'),
            'entry_signal': vcp_info.get('entry_signal', 'NONE'),
            'has_vcp': vcp_info.get('has_vcp', False),
            'num_contractions': vcp_info.get('num_contractions', 0),
            'consolidation_low': vcp_info.get('consolidation_low'),
            'consolidation_high': vcp_info.get('consolidation_high'),
            'breakout_level': vcp_info.get('breakout_level'),
            'current_price': vcp_info.get('current_price'),
            'distance_to_breakout': vcp_info.get('distance_to_breakout'),
            'volatility_trend': vcp_info.get('volatility_trend', ''),
            'contraction_ranges': vcp_info.get('contraction_ranges', []),
            # NEW: Risk management fields
            'entry_price': vcp_info.get('entry_price'),
            'stop_loss': vcp_info.get('stop_loss'),
            'target': vcp_info.get('target'),
            'risk_reward': vcp_info.get('risk_reward'),
            'confidence_score': vcp_info.get('confidence_score'),
            # Include CK signal for context
            'ck_signal': data[sym].get('signal'),
            'ck_action': data[sym].get('action'),
        }
    
    # Sort by active VCP patterns first, then by stage priority and distance to breakout
    stage_priority = {'Broken Out': 0, 'Breakout Ready / At Level': 1, 'Consolidating': 2, 'No Pattern': 3}
    
    def sort_key(item):
        sym, vcp_data = item
        has_vcp = vcp_data.get('has_vcp', False)
        stage = vcp_data.get('stage', 'No Pattern')
        priority = stage_priority.get(stage, 99)
        distance = vcp_data.get('distance_to_breakout') or 0
        confidence = vcp_data.get('confidence_score') or 0
        # Active VCP patterns first (has_vcp=True), then by stage, then by confidence (higher first), then by distance
        return (not has_vcp, priority, -confidence, abs(distance))
    
    sorted_symbols = sorted(out.items(), key=sort_key)
    sorted_data = {sym: vcp_data for sym, vcp_data in sorted_symbols}
    
    return {
        "count": len(sorted_data),
        "last_updated": _vcp_last_ts.isoformat() if _vcp_last_ts else None,
        "data": sorted_data
    }


def manual_refresh_all_averages(symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    """Manually trigger a refresh of all moving averages regardless of cache.
    
    Args:
        symbols: List of symbols to refresh. If None, loads from universe_list in params.
    
    Returns:
        Dictionary with refresh status and results.
    """
    try:
        if symbols is None:
            params = load_params()
            universe_name = params.get("universe_list", "allstocks")
            symbols = load_symbol_list(universe_name)
        
        global _daily_avg_last_ts
        _daily_avg_last_ts = None  # Force refresh by clearing timestamp
        
        _refresh_daily_averages_if_needed(symbols)
        
        with _daily_avg_lock:
            cache_snapshot = dict(_daily_avg_cache)
        
        success_count = sum(1 for v in cache_snapshot.values() if v.get('ema5') is not None)
        
        return {
            "status": "success",
            "symbols_requested": len(symbols),
            "symbols_cached": len(cache_snapshot),
            "symbols_with_data": success_count,
            "last_updated": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }


def get_ema_history() -> Dict[str, Any]:
    """Return historical EMA values for 15-minute candles.
    
    Fetches the most recent 15-minute OHLCV data for all tracked symbols
    and returns EMA 20, 50, 100, 200 values along with LTP.
    
    Response format:
    {
        "data": [
            {
                "timestamp": "2026-02-02 14:30:00",
                "symbol": "INFY",
                "ema_20": 1234.56,
                "ema_50": 1234.00,
                "ema_100": 1233.50,
                "ema_200": 1232.00,
                "ltp": 1235.00
            },
            ...
        ]
    }
    """
    try:
        if not pg_cursor:
            return {"data": [], "error": "Database not available"}
        
        with pg_cursor() as (cur, conn):
            # Get list of all tracked symbols
            cur.execute("""
                SELECT DISTINCT symbol FROM ohlcv_data
                ORDER BY symbol
            """)
            symbols = [row[0] for row in cur.fetchall()]
            
            rows = []
            
            for symbol in symbols:
                try:
                    # Get the latest 15-minute candles with EMA values
                    # We'll fetch recent candles to show the EMA history
                    cur.execute("""
                        SELECT 
                            timestamp,
                            ema_20,
                            ema_50,
                            ema_100,
                            ema_200,
                            close
                        FROM ohlcv_data
                        WHERE symbol = %s 
                            AND timeframe = '15m'
                        ORDER BY timestamp DESC
                        LIMIT 20
                    """, (symbol,))
                    
                    candles = cur.fetchall()
                    
                    # Add rows for each candle
                    for candle in reversed(candles):  # Reverse to show oldest first
                        timestamp = candle[0]
                        ema_20 = candle[1]
                        ema_50 = candle[2]
                        ema_100 = candle[3]
                        ema_200 = candle[4]
                        close = candle[5]
                        
                        # Format timestamp
                        if timestamp:
                            if hasattr(timestamp, 'isoformat'):
                                ts_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')
                            else:
                                ts_str = str(timestamp)
                        else:
                            ts_str = ''
                        
                        rows.append({
                            "timestamp": ts_str,
                            "symbol": symbol,
                            "ema_20": float(ema_20) if ema_20 is not None else None,
                            "ema_50": float(ema_50) if ema_50 is not None else None,
                            "ema_100": float(ema_100) if ema_100 is not None else None,
                            "ema_200": float(ema_200) if ema_200 is not None else None,
                            "ltp": float(close) if close is not None else None,
                        })
                except Exception as e:
                    logger.warning(f"Failed to fetch EMA history for {symbol}: {e}")
                    continue
            
            return {"data": rows}
            
    except Exception as e:
        logger.exception("get_ema_history failed: %s", e)
        return {"error": str(e)}


