#!/usr/bin/env python3
"""
Unified entry point for Pure Alpha Trading Webapp.

This script combines:
1. Flask web application (dashboard, APIs)
2. KiteTicker real-time data subscription
3. 15-minute candle aggregation and database storage
4. Momentum/Reversal scanners (CK/VCP strategies)

Usage:
    python Webapp/main.py [--host 0.0.0.0] [--port 5050] [--mode ltp] [--log-level INFO]

Architecture:
    - Flask runs in a background thread
    - KiteTicker connects in a threaded mode
    - Main loop handles 15m candle persistence at boundaries
    - Scanner data is refreshed periodically and served via API
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from datetime import time as dt_time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# -------------------------------------------------------------------
# Path setup
# -------------------------------------------------------------------
CURRENT_DIR = os.path.dirname(__file__)
REPO_ROOT = os.path.dirname(CURRENT_DIR)

if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# -------------------------------------------------------------------
# Load .env before importing kiteconnect
# -------------------------------------------------------------------
def load_dotenv(path: str):
    """Minimal .env loader (does not overwrite existing vars)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            os.environ.setdefault(k, v)

load_dotenv(os.path.join(REPO_ROOT, '.env'))

# -------------------------------------------------------------------
# Configure logging BEFORE importing any modules (especially app)
# -------------------------------------------------------------------
from logging_config import setup_logging

# Use centralized logging setup (configures console and quieter library loggers)
setup_logging(logging.INFO)

# Completely disable flask_limiter loggers by using NullHandler
flask_limiter_logger = logging.getLogger('flask_limiter')
flask_limiter_logger.handlers = []
flask_limiter_logger.addHandler(logging.NullHandler())
flask_limiter_logger.propagate = False

flask_limiter_errors_logger = logging.getLogger('flask_limiter.errors')
flask_limiter_errors_logger.handlers = []
flask_limiter_errors_logger.addHandler(logging.NullHandler())
flask_limiter_errors_logger.propagate = False

# -------------------------------------------------------------------
# Imports from local packages (AFTER logging is configured)
# -------------------------------------------------------------------
import yaml
from kiteconnect import KiteConnect, KiteTicker
from pgAdmin_database.db_connection import pg_cursor, test_connection

# Import Flask app and ltp_service (now that logging is set up)
from app import app  # Flask app instance
import ltp_service  # For populating caches with real-time data

# Ensure the Flask `app` module sees the canonical implementations of
# `fetch_ltp` and `get_kite` provided by ltp_service. This binds the
# authoritative functions into the `app` namespace at startup so that
# threaded workers and request handlers consistently reference the same
# implementations (avoids intermittent NameError in different import contexts).
try:
    # Prefer package-relative import when available
    try:
        from Webapp.ltp_service import fetch_ltp as _canonical_fetch_ltp, get_kite as _canonical_get_kite
    except Exception:
        _canonical_fetch_ltp = getattr(ltp_service, 'fetch_ltp', None)
        _canonical_get_kite = getattr(ltp_service, 'get_kite', None)

    # Bind into the imported `app` module's globals so code in app.py that
    # references `fetch_ltp` or `get_kite` at runtime finds them. `from app
    # import app` gives us the Flask app object, but handlers look up names
    # on the module object, so we must set them there (sys.modules['app']).
    try:
        import sys as _sys
        app_mod = _sys.modules.get('app')
        if app_mod is None:
            # If the module isn't yet present under 'app', try package name
            app_mod = _sys.modules.get('Webapp.app')

        if _canonical_fetch_ltp:
            # Set on the module object (module-level global)
            if app_mod is not None:
                setattr(app_mod, 'fetch_ltp', _canonical_fetch_ltp)
            # Also make available in this main.py module's globals for safety
            globals()['fetch_ltp'] = _canonical_fetch_ltp

        if _canonical_get_kite:
            if app_mod is not None:
                setattr(app_mod, 'get_kite', _canonical_get_kite)
            globals()['get_kite'] = _canonical_get_kite

        if _canonical_fetch_ltp or _canonical_get_kite:
            logging.info("Bound canonical fetch_ltp/get_kite into app module globals")
        else:
            logging.debug("No canonical fetch_ltp/get_kite found to bind")
    except Exception as _bind_e:
        logging.warning("Failed to bind into app module globals: %s", _bind_e)
except Exception as e:
    logging.warning("Failed to bind canonical ltp_service functions into app namespace: %s", e)

# -------------------------------------------------------------------
# Paths
# -------------------------------------------------------------------
PARAM_PATH = os.path.join(REPO_ROOT, "Support Files", "param.yaml")
NIFTY_SYMBOL_PATH = os.path.join(REPO_ROOT, "Support Files", "NiftySymbol.py")
TOKEN_PATH = os.path.join(REPO_ROOT, "Core_files", "token.txt")
INSTRUMENTS_CSV = os.path.join(REPO_ROOT, "Csvs", "instruments.csv")

# -------------------------------------------------------------------
# ANSI Color Codes for Terminal Output
# -------------------------------------------------------------------
class Colors:
    """ANSI color codes for terminal output."""
    # Text Colors
    GREEN = '\033[92m'      # Bright green
    CYAN = '\033[96m'       # Bright cyan
    YELLOW = '\033[93m'     # Bright yellow
    RED = '\033[91m'        # Bright red
    BLUE = '\033[94m'       # Bright blue
    MAGENTA = '\033[95m'    # Bright magenta
    WHITE = '\033[97m'      # Bright white
    
    # Background Colors
    BG_GREEN = '\033[102m'  # Bright green background
    BG_BLUE = '\033[104m'   # Bright blue background
    
    # Styles
    BOLD = '\033[1m'        # Bold text
    DIM = '\033[2m'         # Dim text
    
    # Reset
    RESET = '\033[0m'       # Reset all formatting
    
    @staticmethod
    def green(text):
        """Return text in green."""
        return f"{Colors.GREEN}{text}{Colors.RESET}"
    
    @staticmethod
    def cyan(text):
        """Return text in cyan."""
        return f"{Colors.CYAN}{text}{Colors.RESET}"
    
    @staticmethod
    def yellow(text):
        """Return text in yellow."""
        return f"{Colors.YELLOW}{text}{Colors.RESET}"
    
    @staticmethod
    def bold_green(text):
        """Return text in bold green."""
        return f"{Colors.BOLD}{Colors.GREEN}{text}{Colors.RESET}"
    
    @staticmethod
    def bold_cyan(text):
        """Return text in bold cyan."""
        return f"{Colors.BOLD}{Colors.CYAN}{text}{Colors.RESET}"

# -------------------------------------------------------------------
# Thread-safe LTP Store (shared with ltp_service)
# -------------------------------------------------------------------
class LTPStore:
    """Thread-safe container for last traded prices."""
    def __init__(self):
        self.by_token: Dict[int, float] = {}
        self.by_symbol: Dict[str, float] = {}
        self._lock = threading.RLock()

    def update_tick(self, instrument_token: int, last_price: float, token_to_symbol: Dict[int, str]):
        """Update LTP for a token and its corresponding symbol."""
        with self._lock:
            self.by_token[instrument_token] = last_price
            sym = token_to_symbol.get(instrument_token)
            if sym:
                self.by_symbol[sym] = last_price

    def snapshot(self) -> Tuple[Dict[int, float], Dict[str, float]]:
        with self._lock:
            return dict(self.by_token), dict(self.by_symbol)

    def get_ltp(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self.by_symbol.get(symbol)


# -------------------------------------------------------------------
# 15-Minute Candle Aggregator
# -------------------------------------------------------------------
class CandleAgg:
    """Aggregates tick data into 15-minute OHLC candles."""
    def __init__(self):
        self.open: Dict[str, float] = {}
        self.high: Dict[str, float] = {}
        self.low: Dict[str, float] = {}
        self.close: Dict[str, float] = {}
        self._lock = threading.RLock()

    def update(self, symbol: str, ltp: float):
        """Update candle aggregation with a new tick."""
        with self._lock:
            if symbol not in self.open:
                self.open[symbol] = ltp
                self.high[symbol] = ltp
                self.low[symbol] = ltp
            else:
                self.high[symbol] = max(self.high[symbol], ltp)
                self.low[symbol] = min(self.low[symbol], ltp)
            self.close[symbol] = ltp

    def snapshot_rows(self, ts_dt: datetime, timeframe: str = '15m') -> List[Tuple]:
        """Return rows ready for DB insert: (timeframe, stockname, candle_stock, open, high, low, close, volume)."""
        with self._lock:
            rows = []
            for sym in self.open:
                rows.append((
                    timeframe,
                    sym,
                    ts_dt,
                    self.open[sym],
                    self.high[sym],
                    self.low[sym],
                    self.close[sym],
                    0  # volume placeholder
                ))
            return rows

    def reset(self):
        """Clear aggregation for next period."""
        with self._lock:
            self.open.clear()
            self.high.clear()
            self.low.clear()
            self.close.clear()


# -------------------------------------------------------------------
# Global instances
# -------------------------------------------------------------------
ltp_store = LTPStore()
candle_agg = CandleAgg()


# -------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------
def load_params() -> Dict[str, Any]:
    """Load configuration from param.yaml."""
    if not os.path.exists(PARAM_PATH):
        raise FileNotFoundError(f"param.yaml not found at {PARAM_PATH}")
    with open(PARAM_PATH, "r") as f:
        return yaml.safe_load(f) or {}


def load_symbol_list(universe_list_name: str) -> List[str]:
    """Load symbol list from NiftySymbol.py."""
    import importlib.util
    if not os.path.exists(NIFTY_SYMBOL_PATH):
        raise FileNotFoundError(f"NiftySymbol.py not found at {NIFTY_SYMBOL_PATH}")
    spec = importlib.util.spec_from_file_location("NiftySymbol", NIFTY_SYMBOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, universe_list_name):
        raise AttributeError(f"Universe '{universe_list_name}' not found in NiftySymbol.py")
    return [str(s).strip() for s in getattr(module, universe_list_name)]


def load_instrument_tokens(csv_path: str) -> Dict[str, int]:
    """Load tradingsymbol -> instrument_token mapping from CSV."""
    mapping: Dict[str, int] = {}
    if not os.path.exists(csv_path):
        return mapping
    with open(csv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get('tradingsymbol')
            tok = row.get('instrument_token')
            if ts and tok:
                try:
                    mapping[ts] = int(tok)
                except ValueError:
                    continue
    return mapping


def ensure_instruments_csv(kite: KiteConnect, csv_path: str) -> None:
    """Download instruments CSV if it doesn't exist."""
    if os.path.exists(csv_path):
        return
    logging.info("Downloading instruments CSV from Kite...")
    instruments = kite.instruments('NSE')
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        if instruments:
            writer = csv.DictWriter(f, fieldnames=instruments[0].keys())
            writer.writeheader()
            writer.writerows(instruments)
    logging.info("Saved %d instruments to %s", len(instruments), csv_path)


def resolve_universe(symbols: List[str], sym_to_token: Dict[str, int]) -> Tuple[List[int], Dict[int, str]]:
    """Resolve symbols to tokens and create reverse mapping."""
    tokens = []
    token_to_sym = {}
    for sym in symbols:
        tok = sym_to_token.get(sym)
        if tok:
            tokens.append(tok)
            token_to_sym[tok] = sym
    return tokens, token_to_sym


def floor_to_15m(dt: datetime) -> datetime:
    """Round current time down to nearest 15-minute boundary."""
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)


def is_trading_bar(bar_end_dt: datetime) -> bool:
    """Return True if bar_end_dt (IST) falls within trading hours (Mon-Fri, 09:15–15:30)."""
    if bar_end_dt.weekday() >= 5:
        return False
    t = bar_end_dt.time()
    start = dt_time(9, 15)
    end = dt_time(15, 30)
    return start <= t <= end


def ensure_ohlcv_table():
    """Create ohlcv_data table if it doesn't exist."""
    if not test_connection():
        return
    try:
        with pg_cursor() as (cur, _):
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv_data (
                    timeframe VARCHAR(10) NOT NULL,
                    stockname VARCHAR(50) NOT NULL,
                    candle_stock TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                    open NUMERIC NOT NULL,
                    high NUMERIC NOT NULL,
                    low NUMERIC NOT NULL,
                    close NUMERIC NOT NULL,
                    volume BIGINT NOT NULL DEFAULT 0
                )
            """)
        logging.info("ohlcv_data table ensured.")
    except Exception as e:
        logging.warning("Failed to ensure ohlcv_data table: %s", e)


# -------------------------------------------------------------------
# Bridge to ltp_service: Update webapp's LTP cache
# -------------------------------------------------------------------
def update_ltp_service_cache(by_symbol: Dict[str, float]):
    """
    Push real-time LTP data to ltp_service's cache so the webapp
    can serve fresh prices without hitting Kite REST API.
    """
    # ltp_service uses _ltp_cache internally; we populate it here
    if hasattr(ltp_service, '_ltp_cache'):
        with getattr(ltp_service, '_ltp_cache_lock', threading.RLock()):
            ltp_service._ltp_cache.update(by_symbol)


# -------------------------------------------------------------------
# Scanner execution (momentum/reversal strategies)
# -------------------------------------------------------------------
def run_scanners_periodic(interval_seconds: int = 300):
    """
    Periodically trigger scanner computations (CK, VCP).
    
    This runs in a background thread and refreshes scanner caches
    so that /api/ck and /api/vcp return fresh data.
    """
    def worker():
        while True:
            try:
                # Trigger CK data refresh
                if hasattr(ltp_service, 'get_ck_data'):
                    ltp_service.get_ck_data()
                    logging.debug("CK scanner refreshed")
                
                # Trigger VCP data refresh
                if hasattr(ltp_service, 'get_vcp_data'):
                    ltp_service.get_vcp_data()
                    logging.debug("VCP scanner refreshed")
                
                # Trigger daily averages refresh
                if hasattr(ltp_service, 'manual_refresh_all_averages'):
                    ltp_service.manual_refresh_all_averages()
                    logging.debug("Daily averages refreshed")
                    
            except Exception as e:
                logging.warning("Scanner refresh failed: %s", e)
            
            time.sleep(interval_seconds)
    
    thread = threading.Thread(target=worker, name="scanner_worker", daemon=True)
    thread.start()
    interval_display = Colors.bold_green(f"interval={interval_seconds}s")
    logging.info(f"Scanner worker started ({interval_display})")
    return thread


# -------------------------------------------------------------------
# Flask server thread
# -------------------------------------------------------------------
def run_flask_server(host: str, port: int):
    """Run Flask in a background thread."""
    def worker():
        # Disable Flask's reloader and werkzeug logging in threaded mode
        import logging as flask_logging
        flask_logging.getLogger('werkzeug').setLevel(flask_logging.ERROR)
        flask_logging.getLogger('flask').setLevel(flask_logging.ERROR)
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
    
    thread = threading.Thread(target=worker, name="flask_server", daemon=True)
    thread.start()
    flask_url = f"http://{host}:{port}"
    logging.info(f"Flask server started on {Colors.bold_green(flask_url)}")
    return thread


# -------------------------------------------------------------------
# Main run function
# -------------------------------------------------------------------
def run(
    universe_override: Optional[str] = None,
    mode: str = 'ltp',
    log_level: str = 'INFO',
    host: str = '127.0.0.1',
    port: int = 5050,
    scanner_interval: int = 300
):
    """
    Main entry point that orchestrates all components.
    
    Args:
        universe_override: Override universe from param.yaml
        mode: Ticker mode ('ltp' or 'quote')
        log_level: Logging level
        host: Flask host
        port: Flask port
        scanner_interval: Scanner refresh interval in seconds
    """
    # Adjust logging level if specified (logging is already configured at module level)
    logging.getLogger().setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    logging.info("=" * 60)
    logging.info("Pure Alpha Trading Webapp - Starting...")
    logging.info("=" * 60)
    
    # Load configuration
    params = load_params()
    universe_name = universe_override or params.get('universe_list', 'allstocks')
    logging.info("Using universe: %s", universe_name)
    
    # Load symbols
    symbols = load_symbol_list(universe_name)
    symbols_msg = Colors.bold_green(f"Loaded {len(symbols)} symbols from {universe_name}")
    logging.info(symbols_msg)
    
    # Get Kite API credentials (optional)
    api_key = os.getenv("KITE_API_KEY")
    no_kite_mode = False
    if not api_key:
        logging.warning("KITE_API_KEY env not set — starting in NO-KITE mode (Flask + scanners only).")
        no_kite_mode = True

    sym_to_token = {}
    tokens = []
    token_to_sym = {}

    if not no_kite_mode:
        if not os.path.exists(TOKEN_PATH):
            raise RuntimeError("Access token file missing. Run Core_files/auth.py to generate token.txt.")

        with open(TOKEN_PATH, "r") as f:
            access_token = f.read().strip()

        # Initialize Kite
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        kite_msg = Colors.bold_green("Kite API initialized")
        logging.info(kite_msg)

        # Ensure instruments CSV exists
        ensure_instruments_csv(kite, INSTRUMENTS_CSV)

        # Load token mappings
        sym_to_token = load_instrument_tokens(INSTRUMENTS_CSV)
        tokens, token_to_sym = resolve_universe(symbols, sym_to_token)

        if not tokens:
            raise SystemExit("No tokens resolved for the selected universe. Check instruments CSV and symbols.")

        tokens_msg = Colors.bold_green(f"Resolved {len(tokens)} tokens for subscription")
        tokens_msg = f"Resolved {Colors.bold_green(len(tokens))} tokens for subscription"
        logging.info(tokens_msg)
    else:
        logging.info("Running in NO-KITE mode: ticker and live subscriptions disabled.")
    
    # Ensure database table
    ensure_ohlcv_table()
    
    # Start Flask server
    flask_thread = run_flask_server(host, port)
    
    # Start scanner worker
    scanner_thread = run_scanners_periodic(scanner_interval)

    # Trigger an immediate computation of daily EMAs so values appear in UI
    try:
        if hasattr(ltp_service, 'manual_refresh_all_averages'):
            ltp_service.manual_refresh_all_averages()
            logging.info("Initial daily averages (EMAs) computed")
    except Exception as e:
        logging.warning("Initial averages compute failed: %s", e)
    
    # -------------------------------------------------------------------
    # KiteTicker callbacks
    # -------------------------------------------------------------------
    def on_ticks(ws, ticks):
        """Handle incoming ticks."""
        for t in ticks:
            try:
                token = int(t.get('instrument_token'))
                ltp = float(t.get('last_price'))
            except (TypeError, ValueError):
                continue
            
            # Update local store
            ltp_store.update_tick(token, ltp, token_to_sym)
            
            # Update candle aggregator
            sym = token_to_sym.get(token)
            if sym:
                candle_agg.update(sym, ltp)
        
        # Push to ltp_service cache (bridge to webapp)
        _, by_symbol = ltp_store.snapshot()
        update_ltp_service_cache(by_symbol)
    
    def on_connect(ws, response):
        """Handle connection."""
        conn_msg = Colors.bold_green(f"Ticker connected. Subscribing to {len(tokens)} tokens in {mode} mode...")
        logging.info(conn_msg)
        ws.subscribe(tokens)
        if mode.lower() == 'ltp':
            ws.set_mode(ws.MODE_LTP, tokens)
        elif mode.lower() == 'quote':
            ws.set_mode(ws.MODE_QUOTE, tokens)
        else:
            ws.set_mode(ws.MODE_LTP, tokens)
    
    def on_close(ws, code, reason):
        logging.info("Ticker closed: %s %s", code, reason)
    
    def on_error(ws, code, reason):
        logging.error("Ticker error: %s %s", code, reason)
    
    # Start KiteTicker (only when Kite credentials are present)
    if not no_kite_mode:
        kws = KiteTicker(api_key, access_token, debug=False)
        kws.on_ticks = on_ticks
        kws.on_connect = on_connect
        kws.on_close = on_close
        kws.on_error = on_error
        kws.connect(threaded=True)

        kws_msg = Colors.bold_green("KiteTicker connected in threaded mode")
        logging.info(kws_msg)
    
    # -------------------------------------------------------------------
    # Main monitoring loop
    # -------------------------------------------------------------------
    last_status_print = 0.0
    last_saved_bar_end = None
    
    try:
        while True:
            time.sleep(5)
            now_ts = time.time()
            
            # Periodic status logging (every 60s)
            if now_ts - last_status_print >= 60:
                symbol_count = len(ltp_store.by_symbol)
                agg_count = len(candle_agg.open)
                logging.info("Status: %d symbols tracked, %d in aggregation", symbol_count, agg_count)
                last_status_print = now_ts
            
            # Check for 15-minute boundary
            now = datetime.now(ZoneInfo("Asia/Kolkata"))
            bar_end = floor_to_15m(now)
            
            # Save candles at boundary (within first 10 seconds)
            if now - bar_end < timedelta(seconds=10) and bar_end != last_saved_bar_end:
                if is_trading_bar(bar_end) and test_connection():
                    ts_naive = bar_end.replace(tzinfo=None)
                    try:
                        with pg_cursor() as (cur, _):
                            rows = candle_agg.snapshot_rows(ts_naive, '15m')
                            logging.info("Saving candles at %s. Symbols: %d", 
                                        ts_naive.isoformat(sep=' '), len(candle_agg.open))
                            if rows:
                                cur.executemany(
                                    """INSERT INTO ohlcv_data
                                       (timeframe, stockname, candle_stock, open, high, low, close, volume)
                                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                    rows,
                                )
                            logging.info("Saved %d 15m candles to ohlcv_data", len(rows))
                    except Exception as e:
                        logging.warning("Failed to save 15m candles: %s", e)
                else:
                    if not is_trading_bar(bar_end):
                        logging.debug("Skipped saving candle outside trading hours at %s", bar_end.isoformat())
                
                # Mark boundary processed and reset aggregation
                last_saved_bar_end = bar_end
                candle_agg.reset()
    
    except KeyboardInterrupt:
        logging.info("Interrupted; shutting down...")
        if not no_kite_mode:
            try:
                kws.stop()
            except Exception:
                pass
        logging.info("Shutdown complete.")


# -------------------------------------------------------------------
# CLI entry point
# -------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Pure Alpha Trading Webapp - Unified entry point",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python Webapp/main.py                          # Default settings
    python Webapp/main.py --port 5000              # Custom port
    python Webapp/main.py --mode quote             # Quote mode for more data
    python Webapp/main.py --scanner-interval 60    # Faster scanner refresh
        """
    )
    parser.add_argument('--universe', default=None, 
                        help='Universe variable name in NiftySymbol.py (default: from param.yaml)')
    parser.add_argument('--mode', default='ltp', choices=['ltp', 'quote'], 
                        help='Streaming mode')
    parser.add_argument('--log-level', default='INFO', 
                        help='Logging level (DEBUG, INFO, WARNING)')
    parser.add_argument('--host', default='127.0.0.1', 
                        help='Flask host (default: 127.0.0.1 - localhost only)')
    parser.add_argument('--port', type=int, default=5050, 
                        help='Flask port (default: 5050)')
    parser.add_argument('--scanner-interval', type=int, default=300, 
                        help='Scanner refresh interval in seconds (default: 300)')
    
    args = parser.parse_args(argv)
    
    run(
        universe_override=args.universe,
        mode=args.mode,
        log_level=args.log_level,
        host=args.host,
        port=args.port,
        scanner_interval=args.scanner_interval
    )


if __name__ == '__main__':
    main()
