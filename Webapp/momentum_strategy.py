#!/usr/bin/env python3
"""
Momentum Strategy - Self-contained trading strategy with position ladder rules.

This module implements a complete momentum trading strategy with:
- Dynamic ranking-based stock selection
- 3-position ladder with conditional entry rules
- Trailing stop-loss management
- SQLite persistence for restart safety
- Paper trading broker abstraction

Usage:
    # As standalone script:
    python momentum_strategy.py
    
    # As imported module:
    from momentum_strategy import run_momentum_strategy, get_strategy_status, stop_strategy

Author: Pure Alpha Trading System
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Callable
import json

# ============================================================================
# CONFIGURATION
# ============================================================================

TOTAL_STRATEGY_CAPITAL = Decimal("150000")  # INR total capital
CAPITAL_PER_POSITION = Decimal("3000")  # INR per position (₹3,000 each trade)
MAX_POSITIONS = 50  # Max 50 positions
SCAN_INTERVAL_SECONDS = 60  # Check list every 1 minute

# Entry Filters
MIN_RANK_GM_THRESHOLD = 2.5  # HARD filter: Only trade when Rank_GM > 2.5

# Position-specific rules
POSITION_1_STOP_LOSS_PCT = Decimal("-2.5")    # Fixed -2.5% from entry (50% reduction)
POSITION_1_TARGET_PCT = Decimal("5.0")        # Fixed +5% target (50% reduction)

POSITION_2_STOP_LOSS_PCT = Decimal("-2.5")    # Floating -2.5% from entry
POSITION_2_TARGET_PCT = None       # No target (runner)
POSITION_2_ENTRY_CONDITION_PNL = Decimal("0.25") # P1 must be > 0.25%

POSITION_3_STOP_LOSS_PCT = Decimal("-5")    # Floating -5% (trails, 50% reduction)
POSITION_3_TARGET_PCT = None                  # No target (runner)
POSITION_3_ENTRY_CONDITION_AVG_PNL = Decimal("1.0")  # Avg of P1 & P2 >= 1%

# Database path
DB_DIR = os.path.dirname(__file__)
DB_PATH_PAPER = os.path.join(DB_DIR, "momentum_strategy_paper.db")
DB_PATH_LIVE = os.path.join(DB_DIR, "momentum_strategy_live.db")

def get_db_path(mode: str = "PAPER") -> str:
    """Get database path based on trading mode."""
    return DB_PATH_LIVE if mode == "LIVE" else DB_PATH_PAPER

# ANSI Color codes
class Colors:
    GREEN = '\033[92m'
    BLUE = '\033[94m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

# Logging setup - Console and Date-wise File Logs
def setup_logging():
    """Setup logging with both console and date-wise file handlers."""
    # Create logs directory if it doesn't exist
    logs_dir = os.path.join(os.path.dirname(__file__), "strategy_logs")
    # Use logs/YYYY-MM-DD/ directory structure
    today = date.today().isoformat()
    logs_dir = os.path.join(os.path.dirname(__file__), "..", "logs", today)
    
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    
    # Main logger
    logger = logging.getLogger("momentum_strategy")
    logger.setLevel(logging.DEBUG)
    
    # Custom formatter with colors for console
    class ColoredFormatter(logging.Formatter):
        def format(self, record):
            msg = super().format(record)
            # Green for trade entries/exits and important trade info
            if any(keyword in msg for keyword in ['Opened P', 'Saving closed trade', 'Loaded', 'Verification', 'Opened P']):
                msg = f"{Colors.GREEN}{msg}{Colors.RESET}"
            return msg
    
    # Console handler (INFO level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = ColoredFormatter(
        '%(asctime)s [MOMENTUM] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)
    
    # File handler - strategy.log in logs/YYYY-MM-DD/ (DEBUG level for detailed logs)
    log_file = os.path.join(logs_dir, "strategy.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        '%(asctime)s [MOMENTUM] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)
    
    return logger

logger = setup_logging()


# ============================================================================
# ENUMS & DATA CLASSES
# ============================================================================

class TradeStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Trade:
    """Represents a single trade/position."""
    trade_id: int
    symbol: str
    entry_time: datetime
    entry_price: Decimal
    qty: int
    stop_loss: Decimal
    target: Optional[Decimal]
    position_number: int  # 1, 2, or 3
    status: TradeStatus
    exit_time: Optional[datetime] = None
    exit_price: Optional[Decimal] = None
    pnl: Optional[Decimal] = None
    highest_price_since_entry: Optional[Decimal] = None  # For trailing stops
    execution_mode: str = "PAPER"  # PAPER or LIVE
    order_book_rank_score: Optional[float] = None  # Order book quality score at entry
    rank_gm_at_entry: Optional[float] = None  # Rank GM at time of entry
    # Broker/order identifiers (optional)
    order_id: Optional[str] = None
    central_trade_id: Optional[str] = None  # trade_id used in central trade_journal (Postgres)
    
    def current_pnl_pct(self, current_price: Decimal) -> Decimal:
        """Calculate current PnL percentage."""
        if self.entry_price == 0:
            return Decimal("0")
        return ((current_price - self.entry_price) / self.entry_price) * Decimal("100")
    
    def calculate_trailing_stop(self, current_price: Decimal) -> Decimal:
        """
        Calculate trailing stop for positions 2 and 3.
        
        Note: highest_price_since_entry should be updated by the caller before calling this.
        """
        if self.position_number == 1:
            # Position 1 has fixed stop loss
            return self.stop_loss
        
        # Use the highest price to calculate trail stop (5% below highest)
        if self.highest_price_since_entry is None:
            # Fallback to entry price if not set
            highest = self.entry_price
        else:
            highest = self.highest_price_since_entry
        
        # Trail stop is 5% below highest price
        trail_stop = highest * (Decimal("1") + POSITION_2_STOP_LOSS_PCT / Decimal("100"))
        
        # Stop can only move up, never down
        return max(self.stop_loss, trail_stop)


@dataclass
class RankingRow:
    """Represents a row from the ranking table."""
    symbol: str
    rank: float
    rank_gm: float  # Rank GM for filtering (HARD filter: must be > 3)
    last_price: Decimal
    lot_size: int
    volume_ratio: float
    order_book_rank_score: Optional[float] = None  # Order book quality score


@dataclass
class StrategyState:
    """Current state of the strategy."""
    timestamp: datetime
    allocated_capital: Decimal
    active_positions: int
    total_pnl: Decimal
    open_trades: List[Trade] = field(default_factory=list)


# ============================================================================
# PAPER TRADING BROKER
# ============================================================================

class Broker:
    """
    Broker abstraction for order execution.
    
    Supports PAPER and LIVE trading modes.
    PAPER mode simulates trades, LIVE mode places real orders via Kite API.
    """
    
    def __init__(self, mode: str = "PAPER"):
        self.mode = mode
        self._order_id_counter = 1000
        self._lock = threading.Lock()
        self._ltp_cache: Dict[str, Decimal] = {}
        self._ltp_callback: Optional[Callable[[str], Optional[Decimal]]] = None
        self._kite = None  # Kite Connect instance for LIVE mode
        self._tick_map: Dict[str, Decimal] = {}
        self._tick_map_loaded = False
        logger.info(f"Broker initialized in {mode} mode")
    
    def set_mode(self, mode: str):
        """Change execution mode (PAPER or LIVE)."""
        if mode not in ("PAPER", "LIVE"):
            raise ValueError(f"Invalid mode: {mode}. Must be PAPER or LIVE")
        old_mode = self.mode
        self.mode = mode
        logger.info(f"Broker mode changed: {old_mode} → {mode}")
    
    def _get_kite(self):
        """Get or initialize Kite Connect instance for LIVE trading."""
        if self._kite is None:
            try:
                import sys
                base_dir = os.path.dirname(os.path.dirname(__file__))
                if base_dir not in sys.path:
                    sys.path.insert(0, base_dir)
                
                from kiteconnect import KiteConnect
                
                # Read API credentials from environment
                api_key = os.getenv("KITE_API_KEY")
                if not api_key:
                    raise RuntimeError(
                        "KITE_API_KEY environment variable not set. "
                        "Please set it before running the application."
                    )
                
                token_path = os.path.join(base_dir, "Core_files", "token.txt")
                with open(token_path, "r") as f:
                    access_token = f.read().strip()
                
                self._kite = KiteConnect(api_key=api_key)
                self._kite.set_access_token(access_token)
                logger.info("Kite Connect initialized for LIVE trading")
            except Exception as e:
                logger.error(f"Failed to initialize Kite Connect: {e}")
                raise
        return self._kite

    def _load_tick_sizes(self):
        """Load tick sizes from Csvs/instruments.csv into _tick_map.

        Fallback: if no tick found, leave symbol absent (caller will use default 0.05).
        """
        if self._tick_map_loaded:
            return
        try:
            import csv
            import sys
            base_dir = os.path.dirname(os.path.dirname(__file__))
            csv_path = os.path.join(base_dir, os.getenv('INSTRUMENTS_CSV', os.path.join('Csvs','instruments.csv')))
            if not os.path.exists(csv_path):
                self._tick_map_loaded = True
                return
            with open(csv_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = (row.get('tradingsymbol') or row.get('symbol') or '').strip()
                    if not ts:
                        continue
                    tick = row.get('tick_size') or row.get('tick') or row.get('tickSize') or row.get('tickSize')
                    if tick:
                        try:
                            # normalize to Decimal
                            t = Decimal(str(tick))
                            if t > 0:
                                self._tick_map[ts] = t
                        except Exception:
                            continue
        except Exception:
            # silent
            pass
        finally:
            self._tick_map_loaded = True

    def _get_tick(self, symbol: str, fallback: Decimal = Decimal('0.05')) -> Decimal:
        """Return tick size for symbol as Decimal. Load map lazily."""
        if not self._tick_map_loaded:
            self._load_tick_sizes()
        # exact match
        if symbol in self._tick_map:
            return self._tick_map[symbol]
        # try NSE:SYMBOL key variants (in instruments.csv some symbols stored differently)
        for k in (symbol, f'NSE:{symbol}', symbol + '.NS'):
            if k in self._tick_map:
                return self._tick_map[k]
        return fallback

    def _round_price_to_tick(self, price: Decimal, tick: Decimal, mode: str = 'nearest') -> Decimal:
        """Round a Decimal price to the nearest multiple of tick.

        mode: 'nearest'|'floor'|'ceil' - choose rounding strategy.
        Returns Decimal quantized to tick precision.
        """
        if tick is None or tick == 0:
            return price
        # determine decimal places for quantize
        # tick like 0.05 -> exponent -2
        try:
            # ensure Decimal
            p = Decimal(str(price))
            t = Decimal(str(tick))
            # compute multiplier
            multiplier = (p / t)
            if mode == 'floor':
                m = int(multiplier.to_integral_value(rounding=ROUND_HALF_UP))
                if Decimal(m) > multiplier:
                    m = m - 1
            elif mode == 'ceil':
                m = int(multiplier.to_integral_value(rounding=ROUND_HALF_UP))
                if Decimal(m) < multiplier:
                    m = m + 1
            else:
                # nearest
                m = int(multiplier.to_integral_value(rounding=ROUND_HALF_UP))
            rounded = (Decimal(m) * t).quantize(t)
            return rounded
        except Exception:
            return price
    
    def set_ltp_callback(self, callback: Callable[[str], Optional[Decimal]]):
        """Set callback function to fetch LTP from external source."""
        self._ltp_callback = callback
    
    def update_ltp(self, symbol: str, price: Decimal):
        """Update LTP cache for a symbol."""
        with self._lock:
            self._ltp_cache[symbol] = price
    
    def get_ltp(self, symbol: str) -> Optional[Decimal]:
        """
        Get Last Traded Price for a symbol.
        
        Tries callback first, then falls back to cache.
        """
        # Try callback first
        if self._ltp_callback:
            try:
                price = self._ltp_callback(symbol)
                if price is not None:
                    self._ltp_cache[symbol] = Decimal(str(price))
                    return self._ltp_cache[symbol]
            except Exception as e:
                logger.warning(f"LTP callback failed for {symbol}: {e}")
        
        # Fallback to cache
        with self._lock:
            return self._ltp_cache.get(symbol)
    
    def get_mid_price(self, symbol: str) -> Optional[Decimal]:
        """
        Get mid-price (bid+ask)/2 for a symbol using Kite quote API.
        
        Falls back to LTP if quote fetch fails or in PAPER mode.
        """
        if self.mode == "LIVE":
            try:
                kite = self._get_kite()
                instrument = f"NSE:{symbol}"
                quote_data = kite.quote(instrument)
                
                if instrument in quote_data:
                    depth = quote_data[instrument].get('depth', {})
                    buy_depth = depth.get('buy', [])
                    sell_depth = depth.get('sell', [])
                    
                    # Get best bid and ask
                    best_bid = buy_depth[0]['price'] if buy_depth and buy_depth[0]['price'] > 0 else None
                    best_ask = sell_depth[0]['price'] if sell_depth and sell_depth[0]['price'] > 0 else None
                    
                    if best_bid and best_ask:
                        mid_price = Decimal(str((best_bid + best_ask) / 2))
                        logger.debug(f"Mid-price for {symbol}: Bid=₹{best_bid}, Ask=₹{best_ask}, Mid=₹{mid_price:.2f}")
                        return mid_price
                    elif best_bid:
                        return Decimal(str(best_bid))
                    elif best_ask:
                        return Decimal(str(best_ask))
                
            except Exception as e:
                logger.warning(f"Failed to get mid-price for {symbol}: {e}")
        
        # Fallback to LTP
        return self.get_ltp(symbol)
    
    def place_order(
        self,
        symbol: str,
        qty: int,
        side: OrderSide = OrderSide.BUY,
        price: Optional[Decimal] = None
    ) -> Dict[str, Any]:
        """
        Place an order (BUY or SELL).
        
        In PAPER mode, simulates immediate fill at current LTP or specified price.
        In LIVE mode, places LIMIT order at mid-price (bid+ask)/2 via Kite API.
        
        Returns:
            dict with order_id, fill_price, fill_qty, status
        """
        with self._lock:
            self._order_id_counter += 1
            order_id = f"MOM_{self._order_id_counter}"
        
        # Get execution price - use mid-price in LIVE mode, LTP in PAPER mode
        if price is not None:
            exec_price = price
        elif self.mode == "LIVE":
            # Use mid-price for LIVE orders
            mid_price = self.get_mid_price(symbol)
            if mid_price is None:
                logger.error(f"Cannot place order for {symbol}: No mid-price available")
                return {
                    "order_id": order_id,
                    "status": "REJECTED",
                    "reason": "No mid-price available",
                    "fill_price": None,
                    "fill_qty": 0
                }
            exec_price = mid_price
        else:
            ltp = self.get_ltp(symbol)
            if ltp is None:
                logger.error(f"Cannot place order for {symbol}: No LTP available")
                return {
                    "order_id": order_id,
                    "status": "REJECTED",
                    "reason": "No LTP available",
                    "fill_price": None,
                    "fill_qty": 0
                }
            exec_price = ltp
        
        if self.mode == "PAPER":
            # Simulate immediate fill
            logger.info(
                f"[PAPER] {side.value} Order: {symbol} qty={qty} @ ₹{exec_price}"
            )
            return {
                "order_id": order_id,
                "status": "COMPLETE",
                "fill_price": exec_price,
                "fill_qty": qty,
                "timestamp": datetime.now()
            }
        else:
            # LIVE mode - place LIMIT order at mid-price via Kite API
            try:
                kite = self._get_kite()
                transaction_type = kite.TRANSACTION_TYPE_BUY if side == OrderSide.BUY else kite.TRANSACTION_TYPE_SELL
                # Determine instrument tick and round price to tick.
                tick = self._get_tick(symbol, fallback=Decimal('0.05'))
                # Choose rounding direction: for BUY prefer floor (bid), for SELL prefer ceil
                rounding_mode = 'floor' if side == OrderSide.BUY else 'ceil'
                limit_price_dec = self._round_price_to_tick(Decimal(exec_price), tick, mode=rounding_mode)
                limit_price = float(limit_price_dec)
                
                live_order_id = kite.place_order(
                    variety=kite.VARIETY_REGULAR,
                    exchange=kite.EXCHANGE_NSE,
                    tradingsymbol=symbol,
                    transaction_type=transaction_type,
                    quantity=qty,
                    product=kite.PRODUCT_CNC,
                    order_type=kite.ORDER_TYPE_LIMIT,
                    price=limit_price
                )
                
                logger.info(f"[LIVE] {side.value} LIMIT Order: {symbol} qty={qty} @ ₹{limit_price:.2f} (mid-price) | Order ID: {live_order_id}")
                
                return {
                    "order_id": str(live_order_id),
                    "status": "COMPLETE",
                    "fill_price": Decimal(str(limit_price)),
                    "fill_qty": qty,
                    "timestamp": datetime.now()
                }
            except Exception as e:
                # Detect tick-size error from Kite and retry with corrected rounding
                err_text = str(e)
                logger.error(f"[LIVE] Order failed for {symbol}: {err_text}")
                try:
                    import re
                    m = re.search(r"tick size for this script is\s*([0-9]*\.?[0-9]+)", err_text, flags=re.IGNORECASE)
                    if m:
                        kite_tick = Decimal(m.group(1))
                        logger.info(f"Detected exchange tick for {symbol}: {kite_tick}. Retrying with rounded price.")
                        rounded_retry = float(self._round_price_to_tick(Decimal(exec_price), kite_tick, mode=rounding_mode))
                        logger.info(f"Retrying LIVE order for {symbol} qty={qty} @ ₹{rounded_retry:.2f}")
                        live_order_id = kite.place_order(
                            variety=kite.VARIETY_REGULAR,
                            exchange=kite.EXCHANGE_NSE,
                            tradingsymbol=symbol,
                            transaction_type=transaction_type,
                            quantity=qty,
                            product=kite.PRODUCT_CNC,
                            order_type=kite.ORDER_TYPE_LIMIT,
                            price=rounded_retry
                        )
                        logger.info(f"[LIVE] RETRY {side.value} LIMIT Order: {symbol} qty={qty} @ ₹{rounded_retry:.2f} | Order ID: {live_order_id}")
                        return {
                            "order_id": str(live_order_id),
                            "status": "COMPLETE",
                            "fill_price": Decimal(str(rounded_retry)),
                            "fill_qty": qty,
                            "timestamp": datetime.now()
                        }
                except Exception as e2:
                    logger.error(f"Retry after tick-size parse failed for {symbol}: {e2}")

                return {
                    "order_id": order_id,
                    "status": "REJECTED",
                    "reason": str(e),
                    "fill_price": None,
                    "fill_qty": 0
                }
    
    def place_gtt(
        self,
        symbol: str,
        trigger_price: float,
        target_price: float,
        qty: int,
        order_type: str = "SELL",
        label: str = ""
    ) -> Dict[str, Any]:
        """
        Place a GTT (Good Till Triggered) order.
        
        In PAPER mode, simulates GTT placement.
        In LIVE mode, places real GTT via Kite API.
        """
        if self.mode == "PAPER":
            logger.info(f"[PAPER] GTT {label}: {symbol} qty={qty} trigger=₹{trigger_price:.2f}")
            return {"status": "SUCCESS", "gtt_id": f"GTT_PAPER_{self._order_id_counter}"}
        else:
            try:
                kite = self._get_kite()

                # Round trigger and target to instrument tick
                tick = self._get_tick(symbol, fallback=Decimal('0.05'))
                trigger_dec = self._round_price_to_tick(Decimal(trigger_price), tick, mode='floor')
                target_dec = self._round_price_to_tick(Decimal(target_price), tick, mode='ceil')

                # Place single-leg GTT order
                gtt_id = kite.place_gtt(
                    trigger_type=kite.GTT_TYPE_SINGLE,
                    tradingsymbol=symbol,
                    exchange=kite.EXCHANGE_NSE,
                    trigger_values=[float(trigger_dec)],
                    last_price=float(self.get_ltp(symbol) or float(trigger_dec)),
                    orders=[{
                        "transaction_type": kite.TRANSACTION_TYPE_SELL,
                        "quantity": qty,
                        "product": kite.PRODUCT_CNC,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": float(target_dec)
                    }]
                )

                logger.info(f"[LIVE] GTT {label}: {symbol} qty={qty} trigger=₹{trigger_dec:.2f} | GTT ID: {gtt_id}")
                return {"status": "SUCCESS", "gtt_id": str(gtt_id)}
            except Exception as e:
                err_text = str(e)
                logger.error(f"[LIVE] GTT failed for {symbol}: {err_text}")
                try:
                    import re
                    m = re.search(r"tick size for this script is\s*([0-9]*\.?[0-9]+)", err_text, flags=re.IGNORECASE)
                    if m:
                        kite_tick = Decimal(m.group(1))
                        logger.info(f"Detected exchange tick for {symbol}: {kite_tick}. Retrying GTT with rounded trigger/target.")
                        trigger_retry = float(self._round_price_to_tick(Decimal(trigger_price), kite_tick, mode='floor'))
                        target_retry = float(self._round_price_to_tick(Decimal(target_price), kite_tick, mode='ceil'))
                        gtt_id = kite.place_gtt(
                            trigger_type=kite.GTT_TYPE_SINGLE,
                            tradingsymbol=symbol,
                            exchange=kite.EXCHANGE_NSE,
                            trigger_values=[trigger_retry],
                            last_price=float(self.get_ltp(symbol) or trigger_retry),
                            orders=[{
                                "transaction_type": kite.TRANSACTION_TYPE_SELL,
                                "quantity": qty,
                                "product": kite.PRODUCT_CNC,
                                "order_type": kite.ORDER_TYPE_LIMIT,
                                "price": target_retry
                            }]
                        )
                        logger.info(f"[LIVE] GTT RETRY {label}: {symbol} qty={qty} trigger=₹{trigger_retry:.2f} | GTT ID: {gtt_id}")
                        return {"status": "SUCCESS", "gtt_id": str(gtt_id)}
                except Exception as e2:
                    logger.error(f"Retry GTT after tick-size parse failed for {symbol}: {e2}")

                return {"status": "FAILED", "reason": str(e)}
    
    def exit_order(self, trade: Trade) -> Dict[str, Any]:
        """
        Exit an existing position.
        
        Returns:
            dict with order_id, fill_price, fill_qty, status
        """
        return self.place_order(
            symbol=trade.symbol,
            qty=trade.qty,
            side=OrderSide.SELL
        )


# ============================================================================
# DATABASE LAYER
# ============================================================================

class StrategyDB:
    """SQLite database manager for strategy persistence."""
    
    def __init__(self, mode: str = "PAPER"):
        self.mode = mode
        self.db_path = get_db_path(mode)
        self._lock = threading.Lock()
        self._init_db()
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with row factory."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_db(self):
        """Initialize database schema."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                
                # Trades table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        entry_time TEXT NOT NULL,
                        entry_price TEXT NOT NULL,
                        qty INTEGER NOT NULL,
                        stop_loss TEXT NOT NULL,
                        target TEXT,
                        exit_time TEXT,
                        exit_price TEXT,
                        pnl TEXT,
                        position_number INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'OPEN',
                        highest_price_since_entry TEXT,
                        trading_date TEXT NOT NULL
                    )
                """)
                
                # Strategy state table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS strategy_state (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        allocated_capital TEXT NOT NULL,
                        active_positions INTEGER NOT NULL,
                        total_pnl TEXT NOT NULL
                    )
                """)
                
                # Traded symbols today (no re-entry rule)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS traded_today (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        trading_date TEXT NOT NULL,
                        UNIQUE(symbol, trading_date)
                    )
                """)
                
                conn.commit()
                logger.info(f"Database initialized at {self.db_path}")
            finally:
                conn.close()
    
    def save_trade(self, trade: Trade) -> int:
        """Save a new trade to database. Returns trade_id."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO trades (
                        symbol, entry_time, entry_price, qty, stop_loss, target,
                        position_number, status, highest_price_since_entry, trading_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade.symbol,
                    trade.entry_time.isoformat(),
                    str(trade.entry_price),
                    trade.qty,
                    str(trade.stop_loss),
                    str(trade.target) if trade.target else None,
                    trade.position_number,
                    trade.status.value,
                    str(trade.highest_price_since_entry) if trade.highest_price_since_entry else None,
                    trade.entry_time.date().isoformat()
                ))
                conn.commit()
                trade_id = cursor.lastrowid
                logger.info(f"Saved trade {trade_id}: {trade.symbol} P{trade.position_number}")
                return trade_id
            finally:
                conn.close()
    
    def update_trade(self, trade: Trade):
        """Update an existing trade."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE trades SET
                        stop_loss = ?,
                        exit_time = ?,
                        exit_price = ?,
                        pnl = ?,
                        status = ?,
                        highest_price_since_entry = ?
                    WHERE trade_id = ?
                """, (
                    str(trade.stop_loss),
                    trade.exit_time.isoformat() if trade.exit_time else None,
                    str(trade.exit_price) if trade.exit_price else None,
                    str(trade.pnl) if trade.pnl else None,
                    trade.status.value,
                    str(trade.highest_price_since_entry) if trade.highest_price_since_entry else None,
                    trade.trade_id
                ))
                conn.commit()
            finally:
                conn.close()
    
    def get_open_trades(self) -> List[Trade]:
        """Get all open trades."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM trades WHERE status = 'OPEN'
                    ORDER BY position_number ASC
                """)
                rows = cursor.fetchall()
                trades = []
                for row in rows:
                    trades.append(Trade(
                        trade_id=row['trade_id'],
                        symbol=row['symbol'],
                        entry_time=datetime.fromisoformat(row['entry_time']),
                        entry_price=Decimal(row['entry_price']),
                        qty=row['qty'],
                        stop_loss=Decimal(row['stop_loss']),
                        target=Decimal(row['target']) if row['target'] else None,
                        position_number=row['position_number'],
                        status=TradeStatus(row['status']),
                        exit_time=datetime.fromisoformat(row['exit_time']) if row['exit_time'] else None,
                        exit_price=Decimal(row['exit_price']) if row['exit_price'] else None,
                        pnl=Decimal(row['pnl']) if row['pnl'] else None,
                        highest_price_since_entry=Decimal(row['highest_price_since_entry']) if row['highest_price_since_entry'] else None
                    ))
                return trades
            finally:
                conn.close()
    
    def get_trades_today(self) -> List[Trade]:
        """Get all trades (open + closed) for today."""
        # Include trades that were entered today OR trades that were closed (exit_time) today
        today = date.today().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM trades
                    WHERE (trading_date = ?)
                       OR (exit_time IS NOT NULL AND DATE(exit_time) = ?)
                    ORDER BY entry_time ASC
                """, (today, today))
                rows = cursor.fetchall()
                trades = []
                for row in rows:
                    trades.append(Trade(
                        trade_id=row['trade_id'],
                        symbol=row['symbol'],
                        entry_time=datetime.fromisoformat(row['entry_time']),
                        entry_price=Decimal(row['entry_price']),
                        qty=row['qty'],
                        stop_loss=Decimal(row['stop_loss']),
                        target=Decimal(row['target']) if row['target'] else None,
                        position_number=row['position_number'],
                        status=TradeStatus(row['status']),
                        exit_time=datetime.fromisoformat(row['exit_time']) if row['exit_time'] else None,
                        exit_price=Decimal(row['exit_price']) if row['exit_price'] else None,
                        pnl=Decimal(row['pnl']) if row['pnl'] else None,
                        highest_price_since_entry=Decimal(row['highest_price_since_entry']) if row['highest_price_since_entry'] else None
                    ))
                
                return trades
            finally:
                conn.close()
    
    def mark_traded_today(self, symbol: str):
        """Mark a symbol as traded today (no re-entry allowed)."""
        today = date.today().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR IGNORE INTO traded_today (symbol, trading_date)
                    VALUES (?, ?)
                """, (symbol, today))
                conn.commit()
            finally:
                conn.close()
    
    def is_traded_today(self, symbol: str) -> bool:
        """Check if symbol was already traded today."""
        today = date.today().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT 1 FROM traded_today
                    WHERE symbol = ? AND trading_date = ?
                """, (symbol, today))
                return cursor.fetchone() is not None
            finally:
                conn.close()
    
    def save_strategy_state(self, state: StrategyState):
        """Save current strategy state snapshot."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO strategy_state (
                        timestamp, allocated_capital, active_positions, total_pnl
                    ) VALUES (?, ?, ?, ?)
                """, (
                    state.timestamp.isoformat(),
                    str(state.allocated_capital),
                    state.active_positions,
                    str(state.total_pnl)
                ))
                conn.commit()
            finally:
                conn.close()
    
    def get_total_pnl_today(self) -> Decimal:
        """Get total realized PnL for today."""
        # Compute realized PnL for trades closed today (exit_time date) or trades entered and closed today
        today = date.today().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT COALESCE(SUM(CAST(pnl AS REAL)), 0) as total_pnl
                    FROM trades
                    WHERE status = 'CLOSED' AND pnl IS NOT NULL
                      AND (DATE(exit_time) = ? OR trading_date = ?)
                """, (today, today))
                row = cursor.fetchone()
                total_pnl = Decimal(str(row['total_pnl'])) if row else Decimal("0")

                # Also count how many closed trades
                cursor.execute("""
                    SELECT COUNT(*) as count FROM trades
                    WHERE status = 'CLOSED' AND pnl IS NOT NULL
                      AND (DATE(exit_time) = ? OR trading_date = ?)
                """, (today, today))
                count_row = cursor.fetchone()
                closed_count = count_row['count'] if count_row else 0

                return total_pnl
            finally:
                conn.close()
    
    def cleanup_old_traded_today(self, days_to_keep: int = 7):
        """Remove old entries from traded_today table."""
        cutoff = (date.today() - timedelta(days=days_to_keep)).isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    DELETE FROM traded_today WHERE trading_date < ?
                """, (cutoff,))
                conn.commit()
            finally:
                conn.close()


# ============================================================================
# RANKING DATA SOURCE
# ============================================================================

def get_live_rankings() -> List[RankingRow]:
    """
    Fetch live rankings from the webapp's ltp_service.
    
    This function integrates with the existing webapp infrastructure.
    Rankings are based on the CK data with computed metrics.
    """
    try:
        # Try to import from ltp_service (webapp context)
        import sys
        webapp_dir = os.path.dirname(__file__)
        if webapp_dir not in sys.path:
            sys.path.insert(0, webapp_dir)
        
        from ltp_service import get_ck_data, fetch_ltp
        
        # Get CK data which includes rankings and RSI
        ck_data = get_ck_data()
        if 'error' in ck_data:
            logger.warning(f"get_ck_data error: {ck_data['error']}")
            return []
        
        # Get LTP data for prices
        ltp_data = fetch_ltp()
        ltp_dict = ltp_data.get('data', {})
        
        rankings = []
        for symbol, info in ck_data.get('data', {}).items():
            try:
                last_price = info.get('last_price')
                if last_price is None:
                    continue
                
                # Get rank from LTP data (rank_gm field)
                ltp_info = ltp_dict.get(symbol, {})
                rank = ltp_info.get('rank_gm', 0) or 0
                rank_gm = float(rank)  # Store actual rank_gm value for filtering
                
                # Volume ratio from LTP data
                volume_ratio = ltp_info.get('volume_ratio', 1.0) or 1.0
                
                # Order book rank score (if available)
                order_book_rank_score = ltp_info.get('order_book_rank_score')
                
                # Lot size - default to 1 for equity
                lot_size = 1
                
                rankings.append(RankingRow(
                    symbol=symbol,
                    rank=float(rank),
                    rank_gm=rank_gm,
                    last_price=Decimal(str(last_price)),
                    lot_size=lot_size,
                    volume_ratio=float(volume_ratio),
                    order_book_rank_score=float(order_book_rank_score) if order_book_rank_score else None
                ))
            except Exception as e:
                logger.debug(f"Skipping {symbol}: {e}")
                continue
        
        # Sort by rank descending (higher rank = better)
        rankings.sort(key=lambda r: r.rank, reverse=True)
        return rankings
        
    except ImportError as e:
        logger.warning(f"Cannot import ltp_service: {e}")
        return []
    except Exception as e:
        logger.error(f"get_live_rankings failed: {e}")
        return []


# ============================================================================
# MOMENTUM STRATEGY ENGINE
# ============================================================================

class MomentumStrategy:
    """
    Main strategy engine implementing the position ladder rules.
    
    Position Ladder with OCO GTT Orders:
    - Position 1: Entry when no active positions. Fixed SL -2.5%, Target +5%
      * GTT: OCO (One-Cancels-Other) with fixed SL and fixed target
    - Position 2: Entry only if P1 PnL > 0.25%. Trailing SL -2.5%, Target +3.75%
      * GTT: OCO with trailing SL and fixed target (SL updates, target stays same)
    - Position 3: Entry only if avg(P1, P2) PnL >= +1%. Trailing SL -2.5%, No target (runner)
      * GTT: Single SL order only (no target, SL trails upward)
    """
    
    def __init__(self, broker: Optional[Broker] = None, db: Optional[StrategyDB] = None, mode: str = "PAPER"):
        """Initialize the momentum strategy engine."""
        self.broker = broker or Broker(mode=mode)
        self.db = db or StrategyDB(mode=mode)
        self.open_trades: List[Trade] = []
        self._running = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # Track last scan time
        self._last_scan_time: Optional[datetime] = None
        # Track last exit time per symbol to enforce cooldowns (symbol -> datetime)
        self._last_exit_time: Dict[str, datetime] = {}
        # Per-symbol GTT/trailing update control
        self._gtt_locks: Dict[str, threading.Lock] = {}
        self._last_gtt_update: Dict[str, datetime] = {}

        # Load existing open trades from DB (restart safety)
        try:
            self._load_open_trades()
        except Exception:
            # If loading fails, continue with an empty open_trades list
            self.open_trades = []

        logger.info(f"MomentumStrategy initialized (mode={mode}, db={self.db.db_path})")
    
    def switch_mode(self, new_mode: str):
        """Switch trading mode and reload database accordingly."""
        if new_mode not in ("PAPER", "LIVE"):
            raise ValueError(f"Invalid mode: {new_mode}. Must be PAPER or LIVE")
        
        old_mode = self.broker.mode
        if old_mode == new_mode:
            logger.info(f"Already in {new_mode} mode")
            return
        
        # Change broker mode
        self.broker.set_mode(new_mode)
        
        # Switch to mode-specific database
        self.db = StrategyDB(mode=new_mode)
        
        # Reload open trades from new database
        self._load_open_trades()
        
        logger.info(f"Switched from {old_mode} to {new_mode} mode")
        logger.info(f"  Database: {self.db.db_path}")
        logger.info(f"  Open trades loaded: {len(self.open_trades)}")
    
    def _load_open_trades(self):
        """Load open trades from database on startup."""
        self.open_trades = self.db.get_open_trades()
        if self.open_trades:
            logger.info(f"Loaded {len(self.open_trades)} open trades from database")
            for trade in self.open_trades:
                logger.info(f"  - {trade.symbol} P{trade.position_number} @ ₹{trade.entry_price}")
    
    def get_allocated_capital(self) -> Decimal:
        """Calculate currently allocated capital."""
        total = Decimal("0")
        for trade in self.open_trades:
            total += trade.entry_price * Decimal(trade.qty)
        return total
    
    def get_remaining_capital(self) -> Decimal:
        """Calculate remaining capital available for new positions."""
        return TOTAL_STRATEGY_CAPITAL - self.get_allocated_capital()
    
    def get_position_count(self) -> int:
        """Get number of active positions."""
        return len(self.open_trades)
    
    def _get_position_by_number(self, position_number: int) -> Optional[Trade]:
        """Get trade by position number."""
        for trade in self.open_trades:
            if trade.position_number == position_number:
                return trade
        return None
    
    def _calculate_qty(self, price: Decimal, lot_size: int) -> int:
        """
        Calculate quantity to buy based on CAPITAL_PER_POSITION and lot size.
        
        Each position gets exactly CAPITAL_PER_POSITION (₹5,000).
        """
        if price <= 0:
            return 0
        
        max_qty_by_capital = int(CAPITAL_PER_POSITION / price)
        
        # Round down to nearest lot size
        lots = max_qty_by_capital // lot_size
        qty = lots * lot_size
        
        return max(0, qty)
    
    def _can_open_position(self, position_number: int) -> Tuple[bool, str]:
        """
        Check if a new position can be opened.
        
        Simple rule: Can open if we have room (active positions < MAX_POSITIONS)
        
        Returns:
            (can_open: bool, reason: str)
        """
        current_positions = self.get_position_count()
        
        if current_positions >= MAX_POSITIONS:
            return False, f"Max positions ({MAX_POSITIONS}) reached"
        
        # Simple check - just need room for more positions
        return True, f"Room available ({current_positions}/{MAX_POSITIONS})"
    
    def _get_position_type_for_symbol(self, symbol: str) -> int:
        """
        Get the next position type (P1, P2, or P3) for a given symbol.
        
        P1, P2, P3 are entry levels for the SAME stock:
        - P1 = First entry in a stock
        - P2 = Second entry (adding to position) - requires P1 in profit
        - P3 = Third entry (final add) - requires avg(P1,P2) >= 1%

        Returns 0 if no more entries allowed for this symbol.
        """
        # Get existing positions for this symbol
        symbol_positions = [t for t in self.open_trades if t.symbol == symbol]
        existing_types = {t.position_number for t in symbol_positions}
        
        # If no positions in this symbol, it's P1
        if not existing_types:
            return 1
        
        # If P1 exists but not P2, check P2 entry condition
        if 1 in existing_types and 2 not in existing_types:
            p1_trade = next(t for t in symbol_positions if t.position_number == 1)
            ltp = self.broker.get_ltp(symbol)
            if ltp and p1_trade.current_pnl_pct(ltp) > POSITION_2_ENTRY_CONDITION_PNL:
                return 2
            return 0  # P1 not in profit yet
        
        # If P1 and P2 exist but not P3, check P3 entry condition
        if 1 in existing_types and 2 in existing_types and 3 not in existing_types:
            ltp = self.broker.get_ltp(symbol)
            if ltp:
                p1_pnl = next(t for t in symbol_positions if t.position_number == 1).current_pnl_pct(ltp)
                p2_pnl = next(t for t in symbol_positions if t.position_number == 2).current_pnl_pct(ltp)
                avg_pnl = (p1_pnl + p2_pnl) / 2
                if avg_pnl >= POSITION_3_ENTRY_CONDITION_AVG_PNL:
                    return 3
            return 0  # Avg PnL not high enough
        
        # All 3 positions filled for this symbol
        return 0
    
    def _get_next_position_number(self) -> int:
        """
        Get the next position number to open (for new stocks, always P1).
        
        Returns 0 if max positions reached.
        """
        if self.get_position_count() >= MAX_POSITIONS:
            return 0
        return 1  # New stocks always start with P1
    
    def _calculate_stop_loss(self, entry_price: Decimal, position_number: int) -> Decimal:
        """Calculate initial stop loss: -5% from entry."""
        sl_pct = POSITION_1_STOP_LOSS_PCT  # -5% for all positions
        return entry_price * (Decimal("1") + sl_pct / Decimal("100"))
    
    def _calculate_target(self, entry_price: Decimal, position_number: int) -> Optional[Decimal]:
        """Calculate target: +10% from entry."""
        target_pct = POSITION_1_TARGET_PCT  # +10% for all positions
        return entry_price * (Decimal("1") + target_pct / Decimal("100"))
    
    def open_position(self, ranking: RankingRow, position_type: int = None) -> Optional[Trade]:
        """
        Attempt to open a new position for the given symbol.
        
        Args:
            ranking: The stock ranking data
            position_type: Force a specific position type (1, 2, or 3). 
                          If None, uses _get_position_type_for_symbol()
        
        Returns Trade if successful, None otherwise.
        """
        symbol = ranking.symbol

        # Cooldown enforcement: if this symbol was exited recently, wait at least 3 minutes
        try:
            last_exit = self._last_exit_time.get(symbol)
            if last_exit:
                elapsed = (datetime.now() - last_exit).total_seconds()
                COOLDOWN_SECONDS = 3 * 60  # 3 minutes
                if elapsed < COOLDOWN_SECONDS:
                    logger.info(f"Cooldown active for {symbol}: last exit {int(elapsed)}s ago, need {COOLDOWN_SECONDS}s")
                    return None
        except Exception:
            # If anything fails, don't block entry (fail-open)
            pass
        
        # Determine position type
        if position_type is None:
            # For new stocks (no existing position), always P1
            position_type = self._get_position_type_for_symbol(symbol)
            if position_type == 0:
                logger.debug(f"{symbol}: Cannot add more positions (conditions not met)")
                return None
        
        # Check if we have room
        if self.get_position_count() >= MAX_POSITIONS:
            logger.debug("All position slots filled")
            return None
        
        # SAFETY ENFORCEMENT for LIVE mode
        if self.broker.mode == "LIVE":
            # Block if Rank_GM is below threshold
            if ranking.rank_gm <= MIN_RANK_GM_THRESHOLD:
                logger.warning(
                    f"[LIVE SAFETY] Blocked {symbol}: Rank_GM ({ranking.rank_gm:.2f}) <= "
                    f"MIN_THRESHOLD ({MIN_RANK_GM_THRESHOLD})"
                )
                return None
            
            # Block if order_book_rank_score is missing (optional: uncomment to enforce)
            # if ranking.order_book_rank_score is None:
            #     logger.warning(f"[LIVE SAFETY] Blocked {symbol}: order_book_rank_score missing")
            #     return None
        
        # Get current price from broker
        ltp = self.broker.get_ltp(symbol)
        if ltp is None:
            # Use ranking price as fallback
            ltp = ranking.last_price
            self.broker.update_ltp(symbol, ltp)
        
        # Calculate quantity based on CAPITAL_PER_POSITION (₹5,000 per position)
        qty = self._calculate_qty(ltp, ranking.lot_size)
        
        if qty <= 0:
            logger.debug(f"{symbol}: Price too high for CAPITAL_PER_POSITION (₹{CAPITAL_PER_POSITION})")
            return None
        
        # Calculate stop loss and target first (for LIVE mode safety check)
        entry_price_estimate = ltp
        stop_loss = self._calculate_stop_loss(entry_price_estimate, position_type)
        target = self._calculate_target(entry_price_estimate, position_type)
        
        # LIVE mode safety: Ensure SL is defined (block if target undefined for non-P3)
        if self.broker.mode == "LIVE":
            if stop_loss is None or stop_loss <= 0:
                logger.warning(f"[LIVE SAFETY] Blocked {symbol}: Stop Loss undefined")
                return None
            if position_type != 3 and (target is None or target <= 0):
                logger.warning(f"[LIVE SAFETY] Blocked {symbol}: Target undefined for P{position_type}")
                return None
        
        # Place order
        order_result = self.broker.place_order(symbol, qty, OrderSide.BUY)
        
        if order_result['status'] != 'COMPLETE':
            logger.warning(f"Order failed for {symbol}: {order_result.get('reason', 'Unknown')}")
            return None
        
        fill_price = order_result['fill_price']
        fill_qty = order_result['fill_qty']
        
        # Recalculate stop loss and target with actual fill price
        stop_loss = self._calculate_stop_loss(fill_price, position_type)
        target = self._calculate_target(fill_price, position_type)
        
        # Create trade object with additional tracking fields
        trade = Trade(
            trade_id=0,  # Will be set by DB
            symbol=symbol,
            entry_time=datetime.now(),
            entry_price=fill_price,
            qty=fill_qty,
            stop_loss=stop_loss,
            target=target,
            position_number=position_type,
            status=TradeStatus.OPEN,
            highest_price_since_entry=fill_price,
            execution_mode=self.broker.mode,  # Track execution mode (PAPER/LIVE)
            order_book_rank_score=ranking.order_book_rank_score,  # Track order book quality
            rank_gm_at_entry=ranking.rank_gm  # Track Rank_GM at entry time
        )
        
        # Save to database
        trade.trade_id = self.db.save_trade(trade)

        # Also log to central trade_journal (Postgres) so all traders appear in the shared journal
        try:
            from pgAdmin_database.db_connection import pg_cursor
            try:
                trade_id_str = f"MOM_{str(order_result.get('order_id') or '')}_{int(time.time())}"
            except Exception:
                trade_id_str = f"MOM_{int(time.time())}"

            try:
                with pg_cursor() as (cur, conn):
                    # create table if missing (keep schema compatible with app._log_trade_entry)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS trade_journal (
                            id SERIAL PRIMARY KEY,
                            trade_id TEXT UNIQUE,
                            symbol TEXT NOT NULL,
                            entry_date TIMESTAMP,
                            entry_price NUMERIC,
                            entry_qty INTEGER,
                            exit_date TIMESTAMP,
                            exit_price NUMERIC,
                            pnl NUMERIC,
                            pnl_pct NUMERIC,
                            duration NUMERIC,
                            strategy TEXT,
                            status TEXT,
                            notes TEXT,
                            order_id TEXT,
                            gtt_id TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)

                    cur.execute("""
                        INSERT INTO trade_journal (trade_id, symbol, entry_date, entry_price, entry_qty, status, order_id, gtt_id, notes, strategy)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (trade_id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                    """, (
                        trade_id_str,
                        trade.symbol,
                        trade.entry_time.isoformat(),
                        str(trade.entry_price),
                        int(trade.qty),
                        'open',
                        str(order_result.get('order_id') or ''),
                        str(getattr(trade, 'gtt_id', None) or ''),
                        'Auto-logged from momentum_strategy',
                        'MOMENTUM'
                    ))
                    conn.commit()
                    # Persist broker/order identifiers on the trade for reliable exit updates
                    try:
                        trade.order_id = str(order_result.get('order_id') or '')
                    except Exception:
                        trade.order_id = None
                    trade.central_trade_id = trade_id_str
                    logger.info(f"Logged trade to central trade_journal: {trade.symbol} trade_id={trade_id_str}")
            except Exception as e:
                logger.warning(f"Failed to insert momentum trade into central trade_journal: {e}")
        except Exception:
            # pg_cursor or psycopg2 not available - skip central logging silently
            pass

        # Add to open trades
        with self._lock:
            self.open_trades.append(trade)
        
        # Enhanced logging with emojis and details
        mode_emoji = "📝" if self.broker.mode == "PAPER" else "💰"
        logger.info(
            f"\n{'▶'*3} POSITION OPENED {'▶'*3}\n"
            f"   Symbol: {symbol}\n"
            f"   Type: P{position_type} | Qty: {fill_qty} | Entry: ₹{fill_price:.2f}\n"
            f"   Stop Loss: ₹{stop_loss:.2f} | Target: {'₹' + str(target.quantize(Decimal('0.01'))) if target else '🏃 Runner'}\n"
            f"   Capital Used: ₹{fill_price * fill_qty:,.2f}\n"
            f"   Mode: {mode_emoji} {self.broker.mode} | Rank_GM: {ranking.rank_gm:.2f}\n"
            f"   Total Positions: {self.get_position_count()}/{MAX_POSITIONS}"
        )
        
        # Place GTT orders for stop-loss and target
        self._place_gtt_orders(trade)
        
        return trade
    
    def _place_gtt_orders(self, trade: Trade):
        """
        Automatically place OCO GTT orders with stop-loss and target based on position type.
        
        OCO (One-Cancels-Other) GTT Strategy by Position Type:
        - P1: OCO with Fixed SL (-5%) and Fixed Target (+10%)
           * When either triggers, the other is automatically canceled
        - P2: OCO with Trailing SL (-5%, trails with price) and Fixed Target (+7.5%)
           * SL trails upward as price moves up
           * Target is fixed, doesn't trail
        - P3: Only SL (no OCO needed, runners have no target)
           * SL trails upward with highest price
           * No target order
        
        Each GTT order pair is placed with quantity = trade quantity.
        """
        # Check if broker supports GTT orders
        if not hasattr(self.broker, 'place_gtt'):
            logger.debug(f"Broker doesn't support GTT orders, skipping GTT placement for {trade.symbol}")
            return
        
        symbol = trade.symbol
        qty = trade.qty
        position_type = trade.position_number
        
        if position_type == 1:
            # P1: Fixed SL and Fixed Target in OCO
            self._place_gtt_oco(
                symbol=symbol,
                qty=qty,
                position_type=1,
                stop_price=float(trade.stop_loss),
                target_price=float(trade.target),
                is_trailing_sl=False,
                trade=trade
            )
        
        elif position_type == 2:
            # P2: Trailing SL and Fixed Target in OCO
            self._place_gtt_oco(
                symbol=symbol,
                qty=qty,
                position_type=2,
                stop_price=float(trade.stop_loss),
                target_price=float(trade.target),
                is_trailing_sl=True,
                trade=trade
            )
        
        elif position_type == 3:
            # P3: Only Trailing SL (no target)
            self._place_gtt_stop_only(
                symbol=symbol,
                qty=qty,
                position_type=3,
                stop_price=float(trade.stop_loss),
                is_trailing_sl=True,
                trade=trade
            )
    
    def _place_gtt_oco(
        self,
        symbol: str,
        qty: int,
        position_type: int,
        stop_price: float,
        target_price: float,
        is_trailing_sl: bool,
        trade: Trade
    ):
        """Place OCO GTT with stop-loss and target."""
        try:
            if self.broker.mode == "PAPER":
                logger.info(
                    f"   [PAPER] GTT OCO P{position_type} {symbol}: "
                    f"SL @ ₹{stop_price:.2f} (trailing={is_trailing_sl}), "
                    f"Target @ ₹{target_price:.2f}"
                )
                trade.gtt_id = f"GTT_OCO_PAPER_P{position_type}_{symbol}"
                return
            
            kite = self.broker._get_kite()
            
            # Place OCO (two-leg) GTT order
            gtt_id = kite.place_gtt(
                trigger_type=kite.GTT_TYPE_OCO,  # OCO = two legs
                tradingsymbol=symbol,
                exchange=kite.EXCHANGE_NSE,
                trigger_values=[stop_price, target_price],  # Two trigger prices
                last_price=float(self.broker.get_ltp(symbol) or stop_price),
                orders=[
                    {  # Leg 1: Stop-Loss order
                        "transaction_type": kite.TRANSACTION_TYPE_SELL,
                        "quantity": qty,
                        "product": kite.PRODUCT_CNC,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": stop_price
                    },
                    {  # Leg 2: Target order
                        "transaction_type": kite.TRANSACTION_TYPE_SELL,
                        "quantity": qty,
                        "product": kite.PRODUCT_CNC,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": target_price
                    }
                ]
            )
            
            trade.gtt_id = str(gtt_id)
            logger.info(
                f"   ✓ GTT OCO P{position_type} {symbol}: "
                f"SL @ ₹{stop_price:.2f} (trailing={is_trailing_sl}), "
                f"Target @ ₹{target_price:.2f} | GTT ID: {gtt_id}"
            )
        
        except Exception as e:
            logger.error(
                f"   ❌ FAILED to place OCO GTT for {symbol} P{position_type}: {type(e).__name__}: {e}",
                exc_info=True
            )
    
    def _place_gtt_stop_only(
        self,
        symbol: str,
        qty: int,
        position_type: int,
        stop_price: float,
        is_trailing_sl: bool,
        trade: Trade
    ):
        """Place GTT with only stop-loss (for P3 runners)."""
        try:
            if self.broker.mode == "PAPER":
                logger.info(
                    f"   [PAPER] GTT SL-ONLY P{position_type} {symbol}: "
                    f"SL @ ₹{stop_price:.2f} (trailing={is_trailing_sl})"
                )
                trade.gtt_id = f"GTT_SL_PAPER_P{position_type}_{symbol}"
                return
            
            kite = self.broker._get_kite()
            
            # Place single-leg GTT order (SL only, no target)
            gtt_id = kite.place_gtt(
                trigger_type=kite.GTT_TYPE_SINGLE,
                tradingsymbol=symbol,
                exchange=kite.EXCHANGE_NSE,
                trigger_values=[stop_price],
                last_price=float(self.broker.get_ltp(symbol) or stop_price),
                orders=[
                    {  # Single leg: Stop-Loss order
                        "transaction_type": kite.TRANSACTION_TYPE_SELL,
                        "quantity": qty,
                        "product": kite.PRODUCT_CNC,
                        "order_type": kite.ORDER_TYPE_LIMIT,
                        "price": stop_price
                    }
                ]
            )
            
            trade.gtt_id = str(gtt_id)
            logger.info(
                f"   ✓ GTT SL-ONLY P{position_type} {symbol}: "
                f"SL @ ₹{stop_price:.2f} (trailing={is_trailing_sl}) | GTT ID: {gtt_id}"
            )
        
        except Exception as e:
            logger.error(
                f"   ❌ FAILED to place SL-ONLY GTT for {symbol} P{position_type}: {type(e).__name__}: {e}",
                exc_info=True
            )
    def close_position(self, trade: Trade, reason: str) -> Decimal:
        """
        Close an existing position.
        
        Returns realized PnL.
        """
        # Get current price
        ltp = self.broker.get_ltp(trade.symbol)
        if ltp is None:
            # Use entry price as fallback for forced liquidation scenarios (e.g., reset)
            logger.warning(f"No LTP for {trade.symbol}, using entry price as fallback for close")
            ltp = trade.entry_price
        
        # Place exit order
        order_result = self.broker.exit_order(trade)
        
        if order_result['status'] != 'COMPLETE':
            logger.warning(f"Exit order failed for {trade.symbol}: {order_result.get('reason', 'Unknown')}")
            return Decimal("0")
        
        exit_price = order_result['fill_price']
        
        # Calculate PnL
        pnl = (exit_price - trade.entry_price) * Decimal(trade.qty)
        pnl_pct = trade.current_pnl_pct(exit_price)
        
        # Update trade
        trade.exit_time = datetime.now()
        trade.exit_price = exit_price
        trade.pnl = pnl
        trade.status = TradeStatus.CLOSED
        
        # Save to database
        logger.info(f"   Saving closed trade to DB: {trade.symbol} P{trade.position_number} - Exit: ₹{exit_price} PnL: ₹{pnl}")
        self.db.update_trade(trade)

        # Update central trade_journal (Postgres) with exit data so journal reflects exits for all traders
        updated_central = False
        try:
            from pgAdmin_database.db_connection import pg_cursor
            try:
                with pg_cursor() as (cur, conn):
                    # Attempt to update by order_id if available, otherwise try symbol + entry_date fallback
                    order_id_val = None
                    try:
                        # Try to read order id from trade attributes if present
                        order_id_val = getattr(trade, 'order_id', None)
                    except Exception:
                        order_id_val = None

                    timestamp = trade.exit_time.isoformat() if trade.exit_time else None
                    pnl_val = float(trade.pnl) if trade.pnl is not None else None

                    if order_id_val:
                        cur.execute("""
                            UPDATE trade_journal
                            SET exit_date = %s, exit_price = %s, pnl = %s, status = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE order_id = %s AND status = 'open'
                        """, (timestamp, str(exit_price), pnl_val, 'closed', str(order_id_val)))
                    else:
                        # Fallback: try to update most recent open journal entry for this symbol
                        cur.execute("""
                            UPDATE trade_journal
                            SET exit_date = %s, exit_price = %s, pnl = %s, status = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE id = (
                                SELECT id FROM trade_journal WHERE symbol = %s AND status = 'open' ORDER BY entry_date DESC LIMIT 1
                            )
                        """, (timestamp, str(exit_price), pnl_val, 'closed', trade.symbol))
                    conn.commit()
                    logger.info(f"Updated central trade_journal exit for {trade.symbol} (order_id={order_id_val})")
                    updated_central = True
            except Exception as e:
                logger.warning(f"Failed to update central trade_journal for exit via pg_cursor: {e}")
        except Exception:
            # pg_cursor not available - will try HTTP fallback
            pass

        # Fallback: use local Flask API to log exits if direct DB update didn't succeed
        if not updated_central:
            try:
                import requests
                url = os.environ.get('WEBAPP_BASE_URL', 'http://127.0.0.1:5050') + '/api/trade-journal/log-exit'
                payload = {
                    'order_id': getattr(trade, 'order_id', None) or None,
                    'symbol': trade.symbol,
                    'exit_price': float(exit_price),
                    'exit_qty': int(trade.qty),
                    'exit_type': 'completed'
                }
                # best-effort POST; ignore network errors
                try:
                    r = requests.post(url, json=payload, timeout=2.0)
                    if r.status_code == 200:
                        logger.info(f"Logged exit via HTTP fallback for {trade.symbol} order_id={payload.get('order_id')}")
                except Exception as he:
                    logger.debug(f"HTTP fallback to log exit failed: {he}")
            except Exception:
                # requests not available or other error - ignore
                pass
        
        # Remove from open trades
        with self._lock:
            self.open_trades = [t for t in self.open_trades if t.trade_id != trade.trade_id]

        # Record last exit time for cooldown enforcement (3 minutes cooldown)
        try:
            if trade.symbol:
                self._last_exit_time[trade.symbol] = datetime.now()
                logger.debug(f"Recorded last exit time for {trade.symbol}: {self._last_exit_time[trade.symbol].isoformat()}")
        except Exception:
            pass
        
        # Enhanced logging with color coding for PnL
        pnl_emoji = "📈 +" if pnl >= 0 else "📉 "
        logger.info(
            f"\n{'◀'*3} POSITION CLOSED {'◀'*3}\n"
            f"   Symbol: {trade.symbol}\n"
            f"   Type: P{trade.position_number} | Entry: ₹{trade.entry_price:.2f} | Exit: ₹{exit_price:.2f}\n"
            f"   PnL: {pnl_emoji}₹{pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            f"   Reason: {reason}\n"
            f"   Remaining Positions: {self.get_position_count()}/{MAX_POSITIONS}"
        )
        
        # Verify trade was saved
        todays_trades = self.db.get_trades_today()
        closed_trades = [t for t in todays_trades if t.status == TradeStatus.CLOSED]
        logger.info(f"   ✓ Verification: Total closed trades today: {len(closed_trades)}")
        
        return pnl
    
    def _check_exits(self):
        """Check all open positions for exit conditions."""
        trades_to_close = []
        
        with self._lock:
            for trade in self.open_trades:
                ltp = self.broker.get_ltp(trade.symbol)
                if ltp is None:
                    continue
                
                # Update trailing stop for P2 and P3
                if trade.position_number in [2, 3]:
                    # Update highest price first
                    if trade.highest_price_since_entry is None or ltp > trade.highest_price_since_entry:
                        trade.highest_price_since_entry = ltp
                        logger.debug(f"High water mark updated for {trade.symbol}: ₹{ltp:.2f}")

                    # Debounce / lock per-symbol to avoid multiple simultaneous trailing updates
                    try:
                        lock = self._gtt_locks.setdefault(trade.symbol, threading.Lock())
                        acquired = lock.acquire(blocking=False)
                        if not acquired:
                            # Another thread is already updating GTTs for this symbol; skip this update
                            logger.debug(f"Skipping trailing update for {trade.symbol} because another update is in progress")
                        else:
                            try:
                                # Short debounce: avoid updating again if we updated recently
                                last = self._last_gtt_update.get(trade.symbol)
                                DEBOUNCE_SECONDS = 5
                                if last and (datetime.now() - last).total_seconds() < DEBOUNCE_SECONDS:
                                    logger.debug(f"Debounced trailing update for {trade.symbol}; last update {(datetime.now()-last).total_seconds():.1f}s ago")
                                else:
                                    new_stop = trade.calculate_trailing_stop(ltp)
                                    if new_stop > trade.stop_loss:
                                        trade.stop_loss = new_stop
                                        self.db.update_trade(trade)
                                        self._last_gtt_update[trade.symbol] = datetime.now()
                                        logger.debug(f"Trailing SL updated for {trade.symbol}: ₹{new_stop:.2f}")
                            finally:
                                lock.release()
                    except Exception:
                        # Fail-open: if locking fails, just attempt the update
                        new_stop = trade.calculate_trailing_stop(ltp)
                        if new_stop > trade.stop_loss:
                            trade.stop_loss = new_stop
                            self.db.update_trade(trade)
                            logger.debug(f"Trailing SL updated for {trade.symbol}: ₹{new_stop:.2f}")
                
                # Check stop loss
                if ltp <= trade.stop_loss:
                    trades_to_close.append((trade, "Stop Loss Hit"))
                    continue
                
                # Check target (if defined)
                if trade.target and ltp >= trade.target:
                    trades_to_close.append((trade, "Target Hit"))
                    continue
        
        # Close positions outside lock
        for trade, reason in trades_to_close:
            self.close_position(trade, reason)
    
    def _scan_for_entries(self):
        """
        Scan for ONE new entry opportunity per scan cycle.
        
        Rules:
        1. ONE trade per scan (every 60 seconds)
        2. Search from RANK #1 downwards (descending)
        3. Priority: P3 > P2 > P1
           - If a stock qualifies for P3, take P3
           - Else if qualifies for P2, take P2
           - Else if new stock, take P1
        4. HARD FILTER: Rank_GM must be > MIN_RANK_GM_THRESHOLD (3)
        5. TIME FILTER: Only enter between 9:45 AM and 3:00 PM
        
        This ensures gradual position building, one trade at a time.
        """
        if self.get_position_count() >= MAX_POSITIONS:
            logger.info(f"All {MAX_POSITIONS} positions filled, no new entries")
            return
        
        # TIME FILTER: Only allow entries between 9:45 AM and 3:15 PM
        from datetime import time as dt_time
        now = datetime.now()
        market_open = dt_time(9, 45)   # 9:45 AM
        market_close = dt_time(15, 15)  # 3:15 PM
        current_time = now.time()
        
        if current_time < market_open:
            logger.debug(f"Entry blocked: Before market hours (current: {current_time.strftime('%H:%M')}, opens at 9:45)")
            return
        
        if current_time >= market_close:
            logger.debug(f"Entry blocked: After entry cutoff (current: {current_time.strftime('%H:%M')}, cutoff at 15:00)")
            return
        
        # Get fresh rankings (sorted by rank, best first)
        rankings = get_live_rankings()
        if not rankings:
            logger.debug("No rankings available")
            return
        
        logger.debug(f"Scanning for entry ({self.get_position_count()}/{MAX_POSITIONS} positions)...")
        
        # Search from Rank #1 downwards for the BEST eligible trade
        for i, ranking in enumerate(rankings):
            symbol = ranking.symbol
            rank_num = i + 1
            
            # HARD FILTER: Reject if Rank_GM <= MIN_RANK_GM_THRESHOLD
            if ranking.rank_gm <= MIN_RANK_GM_THRESHOLD:
                # Silent rejection - don't log rejected trades to reduce console noise
                continue
            
            # Check what position type this symbol qualifies for
            # Priority: P3 > P2 > P1
            
            # Get existing positions for this symbol
            symbol_positions = [t for t in self.open_trades if t.symbol == symbol]
            existing_types = {t.position_number for t in symbol_positions}
            
            eligible_type = 0  # 0 means not eligible
            
            # Check P3 eligibility (highest priority)
            if 1 in existing_types and 2 in existing_types and 3 not in existing_types:
                ltp = self.broker.get_ltp(symbol)
                if ltp:
                    p1_pnl = next(t for t in symbol_positions if t.position_number == 1).current_pnl_pct(ltp)
                    p2_pnl = next(t for t in symbol_positions if t.position_number == 2).current_pnl_pct(ltp)
                    avg_pnl = (p1_pnl + p2_pnl) / 2
                    if avg_pnl >= POSITION_3_ENTRY_CONDITION_AVG_PNL:
                        eligible_type = 3
                        logger.debug(f"Rank #{rank_num} {symbol}: P3 eligible (avg PnL={avg_pnl:.2f}%, Rank_GM={ranking.rank_gm:.2f})")
            
            # Check P2 eligibility (medium priority)
            elif 1 in existing_types and 2 not in existing_types:
                ltp = self.broker.get_ltp(symbol)
                if ltp:
                    p1_trade = next(t for t in symbol_positions if t.position_number == 1)
                    p1_pnl = p1_trade.current_pnl_pct(ltp)
                    if p1_pnl > POSITION_2_ENTRY_CONDITION_PNL:
                        eligible_type = 2
                        logger.debug(f"Rank #{rank_num} {symbol}: P2 eligible (P1 PnL={p1_pnl:.2f}%, Rank_GM={ranking.rank_gm:.2f})")
            
            # Check P1 eligibility (lowest priority - no open position in this stock)
            elif not existing_types:
                # Stock has no open positions - eligible for fresh P1 entry
                # (Can re-enter even if previously traded and closed today)
                eligible_type = 1
                logger.debug(f"Rank #{rank_num} {symbol}: P1 eligible (no open position, Rank_GM={ranking.rank_gm:.2f})")
            
            # If eligible, take this trade and return (ONE per scan)
            if eligible_type > 0:
                trade = self.open_position(ranking, position_type=eligible_type)
                if trade:
                    logger.info(f"✓ Opened P{eligible_type} in {symbol} (Rank #{rank_num})")
                    return  # ONE trade per scan
                else:
                    logger.debug(f"Failed to open P{eligible_type} in {symbol}, continuing search...")
        
        logger.debug(f"Scan complete: No eligible trades found. Total: {self.get_position_count()}/{MAX_POSITIONS}")
    
    def run_cycle(self):
        """Run one strategy cycle: check exits, then scan for entries."""
        try:
            # Record scan time
            self._last_scan_time = datetime.now()
            scan_time_str = self._last_scan_time.strftime('%H:%M:%S')
            
            # Log cycle start with detailed state
            logger.info(f"\n{'='*80}")
            logger.info(f"🔄 STRATEGY SCAN CYCLE @ {scan_time_str}")
            logger.info(f"{'='*80}")
            logger.info(f"Active Positions: {self.get_position_count()}/{MAX_POSITIONS} | "
                       f"Deployed Capital: ₹{self.get_allocated_capital():,.2f}")
            
            # First check exits (SL/Target hits)
            logger.debug(f"→ Checking exits (Stop Loss / Target hits)...")
            self._check_exits()
            
            # Then scan for new entries from top of rankings
            logger.debug(f"→ Scanning for new entries...")
            self._scan_for_entries()
            
            # Save state snapshot
            state = StrategyState(
                timestamp=datetime.now(),
                allocated_capital=self.get_allocated_capital(),
                active_positions=self.get_position_count(),
                total_pnl=self.db.get_total_pnl_today(),
                open_trades=self.open_trades.copy()
            )
            self.db.save_strategy_state(state)
            
            # Log cycle summary
            total_pnl = self.db.get_total_pnl_today()
            pnl_color = "📈" if total_pnl >= 0 else "📉"
            logger.info(f"✓ Cycle complete | Today's PnL: {pnl_color} ₹{total_pnl:+,.2f}")
            logger.info(f"{'='*80}\n")
            
        except Exception as e:
            logger.error(f"❌ Strategy cycle error: {e}", exc_info=True)
    
    def start(self):
        """Start the strategy in a background thread."""
        if self._running:
            logger.warning("Strategy already running")
            return
        
        self._running = True
        self._stop_event.clear()
        
        def run_loop():
            logger.info(f"\n{'🟢'*40}")
            logger.info(f"✅ MOMENTUM STRATEGY STARTED")
            logger.info(f"   Total Capital: ₹{TOTAL_STRATEGY_CAPITAL:,.0f}")
            logger.info(f"   Per Position: ₹{CAPITAL_PER_POSITION:,.0f}")
            logger.info(f"   Max Positions: {MAX_POSITIONS}")
            logger.info(f"   Scan Interval: {SCAN_INTERVAL_SECONDS}s")
            logger.info(f"{'🟢'*40}\n")
            
            while not self._stop_event.is_set():
                self.run_cycle()
                # Wait for interval or stop event
                self._stop_event.wait(timeout=SCAN_INTERVAL_SECONDS)
            
            logger.info(f"\n{'🔴'*40}")
            logger.info(f"⛔ MOMENTUM STRATEGY STOPPED")
            final_pnl = self.db.get_total_pnl_today()
            logger.info(f"   Final Today's PnL: ₹{final_pnl:+,.2f}")
            logger.info(f"{'🔴'*40}\n")
            
            self._running = False
        
        self._thread = threading.Thread(target=run_loop, name="momentum_strategy", daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the strategy."""
        if not self._running:
            logger.warning("Strategy not running")
            return
        
        logger.info("Stopping strategy...")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._running = False
    
    def is_running(self) -> bool:
        """Check if strategy is running."""
        return self._running
    
    def get_current_value(self) -> Decimal:
        """Calculate current market value of all open positions."""
        total = Decimal("0")
        for trade in self.open_trades:
            ltp = self.broker.get_ltp(trade.symbol)
            if ltp:
                total += ltp * Decimal(trade.qty)
            else:
                # If no LTP, use entry price as fallback
                total += trade.entry_price * Decimal(trade.qty)
        return total
    
    def get_unrealized_pnl(self) -> Decimal:
        """Calculate total unrealized PnL from open positions."""
        return self.get_current_value() - self.get_allocated_capital()
    
    def get_status(self) -> Dict[str, Any]:
        """Get current strategy status for API/UI."""
        open_trades_data = []
        for trade in self.open_trades:
            ltp = self.broker.get_ltp(trade.symbol)
            current_pnl = trade.current_pnl_pct(ltp) if ltp else Decimal("0")
            current_pnl_inr = (ltp - trade.entry_price) * Decimal(trade.qty) if ltp else Decimal("0")
            
            open_trades_data.append({
                "trade_id": trade.trade_id,
                "symbol": trade.symbol,
                "position_number": trade.position_number,
                "entry_time": trade.entry_time.isoformat(),
                "entry_price": float(trade.entry_price),
                "qty": trade.qty,
                "ltp": float(ltp) if ltp else None,
                "stop_loss": float(trade.stop_loss),
                "target": float(trade.target) if trade.target else None,
                "current_pnl_pct": float(current_pnl),
                "current_pnl_inr": float(current_pnl_inr),
                "status": trade.status.value
            })
        
        # Get today's closed trades
        todays_trades = self.db.get_trades_today()
        closed_trades_data = []
        for trade in todays_trades:
            if trade.status == TradeStatus.CLOSED:
                closed_trades_data.append({
                    "trade_id": trade.trade_id,
                    "symbol": trade.symbol,
                    "position_number": trade.position_number,
                    "entry_time": trade.entry_time.isoformat(),
                    "entry_price": float(trade.entry_price),
                    "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
                    "exit_price": float(trade.exit_price) if trade.exit_price else None,
                    "qty": trade.qty,
                    "pnl": float(trade.pnl) if trade.pnl else 0,
                    "status": trade.status.value
                })
        
        deployed_capital = self.get_allocated_capital()
        current_value = self.get_current_value()
        unrealized_pnl = current_value - deployed_capital
        
        # Calculate next scan countdown
        next_scan_in = SCAN_INTERVAL_SECONDS
        if self._last_scan_time and self._running:
            elapsed = (datetime.now() - self._last_scan_time).total_seconds()
            next_scan_in = max(0, SCAN_INTERVAL_SECONDS - int(elapsed))
        
        # Count positions by type
        p1_count = sum(1 for trade in self.open_trades if trade.position_number == 1)
        p2_count = sum(1 for trade in self.open_trades if trade.position_number == 2)
        p3_count = sum(1 for trade in self.open_trades if trade.position_number == 3)
        
        return {
            "running": self._running,
            "mode": self.broker.mode,  # Execution mode (PAPER/LIVE)
            "capital_per_position": float(CAPITAL_PER_POSITION),
            "total_capital": float(TOTAL_STRATEGY_CAPITAL),
            "deployed_capital": float(deployed_capital),
            "current_value": float(current_value),
            "unrealized_pnl": float(unrealized_pnl),
            "allocated_capital": float(deployed_capital),  # Backward compat
            "remaining_capital": float(self.get_remaining_capital()),
            "active_positions": self.get_position_count(),
            "max_positions": MAX_POSITIONS,
            "p1_count": p1_count,
            "p2_count": p2_count,
            "p3_count": p3_count,
            "realized_pnl_today": float(self.db.get_total_pnl_today()),
            "open_trades": open_trades_data,
            "closed_trades_today": closed_trades_data,
            "scan_interval": SCAN_INTERVAL_SECONDS,
            "next_scan_in": next_scan_in,
            "last_scan_time": self._last_scan_time.isoformat() if self._last_scan_time else None,
            "last_update": datetime.now().isoformat(),
            "min_rank_gm_threshold": MIN_RANK_GM_THRESHOLD,  # Filter threshold
            "db_path": self.db.db_path  # Database path for current mode
        }


# ============================================================================
# GLOBAL STRATEGY INSTANCE
# ============================================================================

_strategy_instance: Optional[MomentumStrategy] = None
_strategy_lock = threading.Lock()


def get_strategy() -> MomentumStrategy:
    """Get or create the global strategy instance."""
    global _strategy_instance
    with _strategy_lock:
        if _strategy_instance is None:
            _strategy_instance = MomentumStrategy()
        return _strategy_instance


def run_momentum_strategy():
    """
    Main entry point to run the momentum strategy.
    
    Can be called from:
    - Standalone script: python momentum_strategy.py
    - Imported module: from momentum_strategy import run_momentum_strategy
    """
    strategy = get_strategy()
    
    # Set up LTP callback to integrate with webapp
    try:
        from ltp_service import fetch_ltp
        
        def ltp_callback(symbol: str) -> Optional[Decimal]:
            try:
                data = fetch_ltp()
                ltp_info = data.get('data', {}).get(symbol, {})
                price = ltp_info.get('last_price')
                return Decimal(str(price)) if price else None
            except Exception:
                return None
        
        strategy.broker.set_ltp_callback(ltp_callback)
        logger.info("LTP callback connected to ltp_service")
    except ImportError:
        logger.warning("ltp_service not available, using cached LTP only")
    
    strategy.start()
    return strategy


def stop_strategy():
    """Stop the running strategy."""
    strategy = get_strategy()
    strategy.stop()


def get_strategy_status() -> Dict[str, Any]:
    """Get current strategy status for API endpoints."""
    strategy = get_strategy()
    return strategy.get_status()


# ============================================================================
# FLASK API ENDPOINTS (for integration with app.py)
# ============================================================================

def register_strategy_routes(app):
    """
    Register strategy API routes with the Flask app.
    
    Call this from app.py to add strategy endpoints.
    """
    from flask import jsonify, request
    
    @app.route('/api/strategy/momentum/status')
    def api_momentum_status():
        """Get momentum strategy status."""
        try:
            status = get_strategy_status()
            return jsonify(status)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route('/api/strategy/mode', methods=['POST'])
    def api_strategy_mode():
        """Change global execution mode (PAPER or LIVE) immediately.

        Body: { "mode": "PAPER" | "LIVE" }
        """
        try:
            data = request.get_json(silent=True) or {}
            mode = (data.get('mode') or '').upper()
            if mode not in ('PAPER', 'LIVE'):
                return jsonify({"error": f"Invalid mode: {mode}. Must be PAPER or LIVE"}), 400

            # Safety checks when switching to LIVE
            if mode == 'LIVE':
                import os
                base_dir = os.path.dirname(os.path.dirname(__file__))
                api_key = os.getenv('KITE_API_KEY')
                token_path = os.path.join(base_dir, 'Core_files', 'token.txt')
                if not api_key:
                    return jsonify({"error": "LIVE mode blocked: KITE_API_KEY not configured in environment"}), 400
                if not os.path.exists(token_path):
                    return jsonify({"error": "LIVE mode blocked: access token not found. Run auth to generate Core_files/token.txt"}), 400

            strategy = get_strategy()
            # Perform the mode switch (thread-safe inside strategy)
            strategy.switch_mode(mode)

            return jsonify({"status": "ok", "mode": strategy.broker.mode, "db": strategy.db.db_path})
        except Exception as e:
            logger.exception("/api/strategy/mode failed: %s", e)
            return jsonify({"error": str(e)}), 500
    
    @app.route('/api/strategy/momentum/parameters')
    def api_momentum_parameters():
        """Get momentum strategy position ladder parameters."""
        try:
            params = {
                "position_1": {
                    "entry": "No active positions",
                    "stop_loss": float(POSITION_1_STOP_LOSS_PCT),
                    "stop_loss_pct": f"{POSITION_1_STOP_LOSS_PCT}%",
                    "stop_loss_type": "Fixed",
                    "target": float(POSITION_1_TARGET_PCT),
                    "target_pct": f"+{POSITION_1_TARGET_PCT}%"
                },
                "position_2": {
                    "entry": f"P1 PnL > {float(POSITION_2_ENTRY_CONDITION_PNL)}%",
                    "stop_loss": float(POSITION_2_STOP_LOSS_PCT),
                    "stop_loss_pct": f"{POSITION_2_STOP_LOSS_PCT}%",
                    "stop_loss_type": "Trailing",
                    "target": float(POSITION_2_TARGET_PCT) if POSITION_2_TARGET_PCT else None,
                    "target_pct": f"+{POSITION_2_TARGET_PCT}%" if POSITION_2_TARGET_PCT else "Runner"
                },
                "position_3": {
                    "entry": f"Avg(P1,P2) ≥ +{float(POSITION_3_ENTRY_CONDITION_AVG_PNL)}%",
                    "stop_loss": float(POSITION_3_STOP_LOSS_PCT),
                    "stop_loss_pct": f"{POSITION_3_STOP_LOSS_PCT}%",
                    "stop_loss_type": "Trailing",
                    "target": float(POSITION_3_TARGET_PCT) if POSITION_3_TARGET_PCT else None,
                    "target_pct": f"+{POSITION_3_TARGET_PCT}%" if POSITION_3_TARGET_PCT else "Runner"
                },
                "general": {
                    "min_rank_threshold": float(MIN_RANK_GM_THRESHOLD)
                }
            }
            return jsonify(params)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    @app.route('/api/strategy/momentum/start', methods=['POST'])
    def api_momentum_start():
        """
        Start the momentum strategy.
        
        Accepts JSON body with optional 'mode' parameter:
        - mode: "PAPER" (default) or "LIVE"
        
        Safety enforcement for LIVE mode:
        - Rejects if MIN_RANK_GM_THRESHOLD is not configured
        """
        try:
            strategy = get_strategy()
            if strategy.is_running():
                return jsonify({"status": "already_running", "mode": strategy.broker.mode})
            
            # Get mode from request (default to PAPER)
            data = request.get_json(silent=True) or {}
            mode = data.get('mode', 'PAPER').upper()
            
            # Validate mode
            if mode not in ('PAPER', 'LIVE'):
                return jsonify({"error": f"Invalid mode: {mode}. Must be PAPER or LIVE"}), 400
            
            # Safety enforcement for LIVE mode
            if mode == 'LIVE':
                # Ensure Rank_GM threshold is configured
                if MIN_RANK_GM_THRESHOLD <= 0:
                    return jsonify({
                        "error": "LIVE mode blocked: MIN_RANK_GM_THRESHOLD must be > 0",
                        "safety": "Rank_GM_Threshold_Not_Set"
                    }), 400
                live_mode_msg = f"{Colors.BOLD}{Colors.RED}🔴 LIVE MODE ACTIVATED - Real orders will be placed!{Colors.RESET}"
                logger.warning(live_mode_msg)
            
            # Switch mode (this also switches database and reloads trades)
            strategy.switch_mode(mode)
            
            # Start strategy
            run_momentum_strategy()
            return jsonify({"status": "started", "mode": mode, "db": strategy.db.db_path})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    @app.route('/api/strategy/momentum/stop', methods=['POST'])
    def api_momentum_stop():
        """Stop the momentum strategy."""
        try:
            stop_strategy()
            return jsonify({"status": "stopped"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    @app.route('/api/strategy/momentum/close/<int:trade_id>', methods=['POST'])
    def api_momentum_close_trade(trade_id: int):
        """Manually close a trade."""
        try:
            strategy = get_strategy()
            trade = next((t for t in strategy.open_trades if t.trade_id == trade_id), None)
            if trade is None:
                return jsonify({"error": "Trade not found"}), 404
            pnl = strategy.close_position(trade, "Manual Close")
            return jsonify({"status": "closed", "pnl": float(pnl)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    @app.route('/api/strategy/momentum/book/<int:trade_id>', methods=['POST'])
    def api_momentum_book_trade(trade_id: int):
        """Book a position by setting current LTP as target."""
        try:
            from flask import request
            data = request.get_json() or {}
            target_price = data.get('target_price')
            
            if target_price is None:
                return jsonify({"error": "target_price is required"}), 400
            
            strategy = get_strategy()
            trade = next((t for t in strategy.open_trades if t.trade_id == trade_id), None)
            if trade is None:
                return jsonify({"error": "Trade not found"}), 404
            
            # Update trade target to current LTP
            old_target = trade.target
            trade.target = Decimal(str(target_price))
            strategy.db.update_trade(trade)
            
            logger.info(f"Booked position {trade.symbol} P{trade.position_number}: Target updated from ₹{old_target:.2f} to ₹{target_price:.2f}")
            
            return jsonify({
                "status": "booked",
                "symbol": trade.symbol,
                "old_target": float(old_target) if old_target else None,
                "new_target": float(target_price)
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    @app.route('/api/strategy/momentum/clear/<int:trade_id>', methods=['POST'])
    def api_momentum_clear_trade(trade_id: int):
        """Clear a position - close it immediately and mark as booked."""
        try:
            from flask import request
            data = request.get_json() or {}
            exit_price = data.get('exit_price')
            
            strategy = get_strategy()
            trade = next((t for t in strategy.open_trades if t.trade_id == trade_id), None)
            if trade is None:
                return jsonify({"error": "Trade not found"}), 404
            
            # Use provided exit price or fetch LTP
            if exit_price is None:
                exit_price = strategy.broker.get_ltp(trade.symbol)
            if exit_price is None:
                exit_price = float(trade.entry_price)  # Fallback to entry price
            
            exit_price = Decimal(str(exit_price))
            
            # Calculate PnL
            pnl = (exit_price - trade.entry_price) * Decimal(trade.qty)
            pnl_pct = ((exit_price - trade.entry_price) / trade.entry_price * 100)
            
            # Update trade as closed
            trade.exit_time = datetime.now()
            trade.exit_price = exit_price
            trade.pnl = pnl
            trade.status = TradeStatus.CLOSED
            
            # Save to database
            strategy.db.update_trade(trade)

            # Update central trade_journal (Postgres) so journal reflects this booked/cleared exit
            try:
                from pgAdmin_database.db_connection import pg_cursor
                try:
                    with pg_cursor() as (cur, conn):
                        order_id_val = None
                        try:
                            order_id_val = getattr(trade, 'order_id', None)
                        except Exception:
                            order_id_val = None

                        timestamp = trade.exit_time.isoformat() if trade.exit_time else None
                        pnl_val = float(trade.pnl) if trade.pnl is not None else None

                        if order_id_val:
                            cur.execute("""
                                UPDATE trade_journal
                                SET exit_date = %s, exit_price = %s, pnl = %s, status = %s, updated_at = CURRENT_TIMESTAMP
                                WHERE order_id = %s AND status = 'open'
                            """, (timestamp, str(trade.exit_price), pnl_val, 'closed', str(order_id_val)))
                        else:
                            cur.execute("""
                                UPDATE trade_journal
                                SET exit_date = %s, exit_price = %s, pnl = %s, status = %s, updated_at = CURRENT_TIMESTAMP
                                WHERE id = (
                                    SELECT id FROM trade_journal WHERE symbol = %s AND status = 'open' ORDER BY entry_date DESC LIMIT 1
                                )
                            """, (timestamp, str(trade.exit_price), pnl_val, 'closed', trade.symbol))
                        conn.commit()
                        logger.info(f"Updated central trade_journal exit (cleared) for {trade.symbol} (order_id={order_id_val})")
                except Exception as e:
                    logger.warning(f"Failed to update central trade_journal for clear via pg_cursor: {e}")
            except Exception:
                # Fallback: use local Flask API to log exits if direct DB update didn't succeed
                try:
                    import requests
                    url = os.environ.get('WEBAPP_BASE_URL', 'http://127.0.0.1:5050') + '/api/trade-journal/log-exit'
                    payload = {
                        'order_id': getattr(trade, 'order_id', None) or None,
                        'symbol': trade.symbol,
                        'exit_price': float(trade.exit_price) if trade.exit_price is not None else None,
                        'exit_qty': int(trade.qty),
                        'exit_type': 'booked'
                    }
                    try:
                        r = requests.post(url, json=payload, timeout=2.0)
                        if r.status_code == 200:
                            logger.info(f"Logged cleared exit via HTTP fallback for {trade.symbol} order_id={payload.get('order_id')}")
                    except Exception as he:
                        logger.debug(f"HTTP fallback to log cleared exit failed: {he}")
                except Exception:
                    # requests not available or other error - ignore
                    pass

            # Remove from open trades
            with strategy._lock:
                strategy.open_trades = [t for t in strategy.open_trades if t.trade_id != trade.trade_id]
            
            pnl_emoji = "📈 +" if pnl >= 0 else "📉 "
            logger.info(
                f"\n{'◀'*3} POSITION CLEARED (BOOKED) {'◀'*3}\n"
                f"   Symbol: {trade.symbol} P{trade.position_number}\n"
                f"   Entry: ₹{trade.entry_price:.2f} | Exit: ₹{exit_price:.2f}\n"
                f"   PnL: {pnl_emoji}₹{pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
                f"   Remaining Positions: {strategy.get_position_count()}/{MAX_POSITIONS}"
            )
            
            return jsonify({
                "status": "cleared",
                "symbol": trade.symbol,
                "exit_price": float(exit_price),
                "pnl": float(pnl),
                "pnl_pct": float(pnl_pct)
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    @app.route('/api/strategy/momentum/reset', methods=['POST'])
    def api_momentum_reset_all():
        """
        Close all open positions and reset strategy (PAPER MODE ONLY).
        This clears the order book and starts with zero positions.
        """
        try:
            strategy = get_strategy()
            
            # Only allow in PAPER mode
            if strategy.broker.mode != 'PAPER':
                return jsonify({
                    "error": "Reset is only allowed in PAPER trading mode for safety"
                }), 403
            
            # Get all open trades
            open_trades_copy = list(strategy.open_trades)
            closed_count = 0
            failed_count = 0
            
            logger.info(f"\n{'='*60}")
            logger.info(f"RESETTING STRATEGY - Closing {len(open_trades_copy)} open position(s)")
            logger.info(f"{'='*60}")
            
            # Close each position
            for trade in open_trades_copy:
                try:
                    strategy.close_position(trade, "Strategy Reset - Close All")
                    closed_count += 1
                except Exception as e:
                    logger.error(f"Failed to close {trade.symbol} P{trade.position_number}: {str(e)}")
                    failed_count += 1
            
            # Clear the open trades list (ensure it's empty)
            with strategy._lock:
                strategy.open_trades = []
            
            # Reset any pending order state (if these attributes exist)
            if hasattr(strategy, 'pending_positions'):
                strategy.pending_positions = {}
            if hasattr(strategy, 'symbol_entry_state'):
                strategy.symbol_entry_state = {}
            if hasattr(strategy, 'traded_today_symbols'):
                strategy.traded_today_symbols = set()
            
            # Reset timing
            strategy._last_scan_time = None
            
            logger.info(f"Strategy Reset Complete:")
            logger.info(f"  - Positions Closed: {closed_count}")
            logger.info(f"  - Failed to Close: {failed_count}")
            logger.info(f"  - Open Trades Remaining: {len(strategy.open_trades)}")
            logger.info(f"{'='*60}\n")
            
            # DEBUG: Verify status after reset
            status = strategy.get_status()
            logger.info(f"DEBUG: After reset, open_trades in status: {len(status.get('open_trades', []))} items")
            
            return jsonify({
                "status": "reset",
                "closed": closed_count,
                "failed": failed_count,
                "message": f"Strategy reset complete. Closed {closed_count} position(s)."
            })
        
        except Exception as e:
            logger.error(f"Error during reset: {str(e)}")
            return jsonify({"error": str(e)}), 500
    
    logger.info("Strategy routes registered with Flask app")


# ============================================================================
# STANDALONE EXECUTION
# ============================================================================

if __name__ == '__main__':
    import signal
    
    print("=" * 60)
    print("MOMENTUM STRATEGY - Paper Trading Mode")
    print("=" * 60)
    print(f"Capital: ₹{TOTAL_STRATEGY_CAPITAL}")
    print(f"Max Positions: {MAX_POSITIONS}")
    print(f"Scan Interval: {SCAN_INTERVAL_SECONDS}s")
    print("=" * 60)
    print("Position Ladder Rules:")
    print(f"  P1: Entry if no positions | SL: {POSITION_1_STOP_LOSS_PCT}% | Target: +{POSITION_1_TARGET_PCT}%")
    print(f"  P2: Entry if P1 PnL > 0 | SL: {POSITION_2_STOP_LOSS_PCT}% trailing | Target: +{POSITION_2_TARGET_PCT}%")
    print(f"  P3: Entry if avg(P1,P2) >= +{POSITION_3_ENTRY_CONDITION_AVG_PNL}% | SL: {POSITION_3_STOP_LOSS_PCT}% trailing | Target: Runner")
    print("=" * 60)
    print("Press Ctrl+C to stop\n")
    
    strategy = run_momentum_strategy()
    
    # Handle graceful shutdown
    def signal_handler(sig, frame):
        print("\nShutting down...")
        stop_strategy()
        print("Strategy stopped.")
        exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Keep main thread alive
    try:
        while strategy.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        stop_strategy()
