# Pure Alpha Trading Strategy - Complete Execution Guide

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Startup Flow](#startup-flow)
3. [Core Components](#core-components)
4. [API Endpoints](#api-endpoints)
5. [Strategy Execution Cycle](#strategy-execution-cycle)
6. [Database Schema](#database-schema)
7. [WebSocket Implementation Guide](#websocket-implementation-guide)

---

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                    Pure Alpha Trading System                 │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼──────────────────────┐
        │                     │                      │
    ┌───▼──────┐         ┌───▼──────┐         ┌───▼──────┐
    │   Flask  │         │   Kite   │         │ Momentum │
    │   Webapp │         │  Ticker  │         │ Strategy │
    │  (5050)  │         │ (Real-   │         │  Engine  │
    │          │         │  time    │         │          │
    └──────────┘         └──────────┘         └──────────┘
        │                     │                      │
        └─────────────────────┼──────────────────────┘
                    ┌─────────▼─────────┐
                    │   LTP Service     │
                    │  (Live Prices)    │
                    └───────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   PostgreSQL DB   │
                    │  & SQLite         │
                    └───────────────────┘
```

### Key Features
- **Real-time Data**: KiteTicker subscribes to live LTP updates for 85+ stocks
- **15-min Candles**: Tick data aggregated into 15-minute OHLC candles
- **Momentum Strategy**: Automated entry/exit with 3-position ladder system
- **GTT Management**: Good-Till-Triggered orders for trailing stop-losses
- **REST + WebSocket**: APIs for dashboard + WebSocket order event streaming
- **Paper & Live Mode**: Switch between paper trading and real execution

---

## Startup Flow

### 1. Entry Point: `main.py`

```python
# python3 Webapp/main.py --port 5050
```

#### Initialization Steps:

**Step 1.1: Configuration Loading**
```python
# Load environment variables from .env
load_dotenv(os.path.join(REPO_ROOT, '.env'))

# Load param.yaml for strategy settings
params = load_params()  # Returns dict with strategy config

# Load stock universe (e.g., stocks2026)
symbols = load_symbol_list('stocks2026')  # e.g., 87 stocks
```

**Step 1.2: Logging Setup**
```python
from logging_config import setup_logging
setup_logging(logging.INFO)

# Sets up:
# - Console handler (INFO level)
# - Date-wise file logs under ./logs/YYYY-MM-DD/
# - Handlers for: root, access, orders, errors, trail, momentum
```

**Step 1.3: Kite API Initialization**
```python
from kiteconnect import KiteConnect, KiteTicker

# Read token from Core_files/token.txt
kite = KiteConnect(api_key=API_KEY, access_token=TOKEN)

# Verify connection
test_connection()

# Download/load instruments.csv (symbol -> token mapping)
ensure_instruments_csv(kite, INSTRUMENTS_CSV)
sym_to_token = load_instrument_tokens(INSTRUMENTS_CSV)

# Resolve symbols to tokens
tokens, token_to_sym = resolve_universe(symbols, sym_to_token)
# tokens: [int, int, ...] - 85 tokens
# token_to_sym: {token: 'SYMBOL', ...}
```

**Step 1.4: Flask App Startup**
```python
from app import app
import ltp_service

# Bind canonical fetch_ltp and get_kite into app globals
# (ensures consistent function resolution across imports)
app_mod = sys.modules.get('app')
setattr(app_mod, 'fetch_ltp', _canonical_fetch_ltp)
setattr(app_mod, 'get_kite', _canonical_get_kite)

# Start Flask in background thread
flask_thread = threading.Thread(target=lambda: app.run(...))
flask_thread.daemon = True
flask_thread.start()
```

**Step 1.5: KiteTicker Connection**
```python
from kiteconnect import KiteTicker

ticker = KiteTicker(api_key=API_KEY, access_token=TOKEN)

# Callbacks for real-time data
@ticker.on_tick
def on_tick(ticks):
    """Called when new price tick arrives."""
    for tick in ticks:
        token = tick['instrument_token']
        ltp = tick['last_price']
        symbol = token_to_sym.get(token)
        
        # Update global LTP store
        ltp_store.update_tick(token, ltp, token_to_sym)
        
        # Aggregate into 15-min candles
        candle_agg.update(symbol, ltp)
        
        # Push to ltp_service cache for webapp
        update_ltp_service_cache({symbol: ltp})

# Subscribe in LTP mode (minimal bandwidth)
ticker.subscribe(tokens, 'ltp')
ticker.connect(threaded=True)

logging.info(f"KiteTicker connected. Subscribing to {len(tokens)} tokens...")
```

**Step 1.6: Momentum Strategy Initialization**
```python
from momentum_strategy import run_momentum_strategy

# Strategy starts in PAPER mode initially
# Database: Webapp/momentum_strategy_paper.db
strategy_thread = threading.Thread(
    target=run_momentum_strategy,
    kwargs={
        'kite': kite,
        'symbols': symbols,
        'ltp_callback': ltp_store.get_ltp
    },
    daemon=False
)
strategy_thread.start()
```

**Step 1.7: Database Setup**
```python
# PostgreSQL connection
from pgAdmin_database.db_connection import test_connection, pg_cursor

if test_connection():
    ensure_ohlcv_table()  # Create ohlcv_data table
    logging.info("PostgreSQL ready")

# SQLite for strategy persistence
# - momentum_strategy_paper.db (paper trades)
# - momentum_strategy_live.db (live trades)
```

**Step 1.8: Scanner Worker (Background)**
```python
def scanner_worker():
    """Periodically compute/update scanner data (CK, VCP patterns)."""
    while True:
        try:
            # Fetch latest daily data
            # Compute EMA, identify CK/VCP patterns
            # Store in app cache for API serving
            
            # CK Pattern: Candlestick pattern detection
            # VCP Pattern: Volatility Contraction Pattern
            
            time.sleep(300)  # Every 5 minutes
        except Exception as e:
            logging.error(f"Scanner error: {e}")

scanner_thread = threading.Thread(target=scanner_worker, daemon=True)
scanner_thread.start()
logging.info("Scanner worker started (interval=300s)")
```

**Step 1.9: 15-min Candle Boundary Handler (Main Loop)**
```python
# Main loop watches for 15-minute boundaries
while True:
    now = datetime.now(tz=IST)
    bar_end = floor_to_15m(now)
    next_bar_end = bar_end + timedelta(minutes=15)
    
    sleep_seconds = (next_bar_end - now).total_seconds()
    time.sleep(max(0, sleep_seconds))
    
    # At boundary: save candles to DB
    if is_trading_bar(bar_end):
        rows = candle_agg.snapshot_rows(bar_end, '15m')
        
        try:
            with pg_cursor() as (cur, conn):
                for row in rows:
                    cur.execute(
                        """INSERT INTO ohlcv_data 
                        (timeframe, stockname, candle_stock, open, high, low, close, volume)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        row
                    )
                conn.commit()
                logging.info(f"Persisted {len(rows)} candles for 15m bar {bar_end}")
        except Exception as e:
            logging.error(f"Candle persistence error: {e}")
        
        candle_agg.reset()  # Clear for next period
```

---

## Core Components

### 1. LTP Service (`ltp_service.py`)

**Purpose**: Manages live price cache and serves prices without hitting Kite API

```python
# Global cache
_ltp_cache = {}
_ltp_cache_lock = threading.RLock()

def fetch_ltp(symbol: str) -> Optional[float]:
    """Get last traded price for a symbol from cache."""
    with _ltp_cache_lock:
        return _ltp_cache.get(symbol)

def get_kite() -> KiteConnect:
    """Get authenticated Kite API client."""
    # Initialized in main.py
    # Provides API access for orders, holdings, positions
    pass
```

**Update Flow**:
```
KiteTicker.on_tick()
    ↓
candle_agg.update(symbol, ltp)
    ↓
update_ltp_service_cache({symbol: ltp})
    ↓
ltp_service._ltp_cache[symbol] = ltp
    ↓
/api/ltp returns fresh prices
```

### 2. Momentum Strategy (`momentum_strategy.py`)

**Core Configuration**:
```python
TOTAL_STRATEGY_CAPITAL = Decimal("240000")  # ₹240,000 total
CAPITAL_PER_POSITION = Decimal("3000")      # ₹3,000 per trade
MAX_POSITIONS = 90                           # Max 90 open positions
SCAN_INTERVAL_SECONDS = 60                  # Check list every 60s

# Entry filter
MIN_RANK_GM_THRESHOLD = 2.5  # Only trade if Rank_GM > 2.5

# Position-specific rules
POSITION_1_STOP_LOSS_PCT = Decimal("-2.5")   # Fixed -2.5%
POSITION_1_TARGET_PCT = Decimal("5.0")       # Fixed +5%

POSITION_2_STOP_LOSS_PCT = Decimal("-2.5")   # Floating -2.5%
POSITION_2_TARGET_PCT = None                 # No target (runner)
POSITION_2_ENTRY_CONDITION_PNL = Decimal("0.25")  # P1 PnL > 0.25%

POSITION_3_STOP_LOSS_PCT = Decimal("-5")    # Floating -5%
POSITION_3_TARGET_PCT = None                 # No target (runner)
POSITION_3_ENTRY_CONDITION_AVG_PNL = Decimal("1.0")  # Avg PnL >= 1%
```

**Trade Lifecycle**:

```
1. SCAN (every 60s)
   ├─ Fetch daily data for all symbols
   ├─ Compute EMAs (20, 50, 200)
   ├─ Calculate Rank_GM (momentum score)
   └─ Sort by Rank_GM

2. RANKING & FILTERING
   ├─ Keep only symbols with Rank_GM > 2.5
   ├─ Remove already-open symbols
   └─ Select top N for new entries

3. ENTRY
   ├─ Check position count < 90
   ├─ Check capital available > ₹3,000
   ├─ Place BUY order at LTP
   │  ├─ Quantity = ₹3,000 / LTP (rounded)
   │  └─ Order ID recorded
   ├─ Create Position 1 entry
   │  ├─ Entry price = execution price
   │  ├─ SL = Entry price × (1 + POSITION_1_STOP_LOSS_PCT)
   │  ├─ Target = Entry price × (1 + POSITION_1_TARGET_PCT)
   │  └─ Create GTT order for SL (exit entire position)
   └─ Log to SQLite trade database

4. MONITORING (continuous)
   ├─ Update highest_price_since_entry
   ├─ Check P1 PnL > 0.25% for P2 entry
   ├─ Check (P1 + P2) avg PnL > 1% for P3 entry
   ├─ Update trailing stops (P2, P3)
   └─ Check hit targets

5. P2 ENTRY (when P1 PnL > 0.25%)
   ├─ Place new BUY order (same symbol, new position)
   ├─ Create Position 2 entry
   │  ├─ SL = Entry × (1 - 2.5%)  [floating, trails]
   │  └─ No fixed target
   └─ Create GTT for SL

6. P3 ENTRY (when avg(P1, P2) PnL > 1%)
   ├─ Place new BUY order
   ├─ Create Position 3 entry
   │  ├─ SL = Entry × (1 - 5%)  [floating, trails]
   │  └─ No fixed target
   └─ Create GTT for SL

7. TRAILING STOP UPDATE
   For P2 & P3:
   ├─ On each LTP tick
   ├─ If LTP > highest_price:
   │  ├─ Update highest_price
   │  ├─ Recalculate SL = highest × (1 - 2.5% or 5%)
   │  ├─ Modify GTT order with new SL
   │  └─ Log trailing update
   └─ Update DB with new SL

8. EXIT (Hit Target or SL)
   On GTT trigger (Kite webhook):
   ├─ Receive order_fill event
   ├─ Match to open trade in DB
   ├─ Update trade status = CLOSED
   ├─ Calculate PnL = (exit_price - entry_price) × qty
   ├─ Archive to closed_trades
   └─ Free up capital for new entry

9. END OF DAY
   ├─ Any open P1 with profitable close
   ├─ Close P2, P3 at market
   └─ Log daily PnL
```

**Trade Data Model**:
```python
@dataclass
class Trade:
    trade_id: int
    symbol: str
    entry_time: datetime
    entry_price: Decimal
    qty: int
    stop_loss: Decimal
    target: Optional[Decimal]
    position_number: int  # 1, 2, or 3
    status: TradeStatus  # OPEN or CLOSED
    
    # Optional fields
    exit_time: Optional[datetime]
    exit_price: Optional[Decimal]
    pnl: Optional[Decimal]
    highest_price_since_entry: Optional[Decimal]
    gtt_id: Optional[str]  # Kite GTT order ID
    execution_mode: str  # PAPER or LIVE
    rank_gm_at_entry: Optional[float]
    order_id: Optional[str]
    central_trade_id: Optional[str]  # PostgreSQL trade_journal ID
```

### 3. Flask App (`app.py`)

**Session & Security**:
```python
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = False  # HTTPS only in prod
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret')

# CSRF protection
from src.security.csrf_protection import csrf_protect

# Auth system
from src.security.auth import require_login, UserRole
```

**Access Logging**:
```python
# Skip logging high-frequency requests to reduce noise
skip_paths = {
    '/api/ltp',
    '/api/user-support',
    '/api/major-support',
    '/api/strategy/momentum/status',
}

@app.after_request
def _log_request(response):
    if request.path not in skip_paths:
        access_logger.info(...)  # Log method, path, status, latency
```

---

## API Endpoints

### Market Data APIs

#### **GET /api/ltp**
Get live last traded prices for all symbols.

```json
{
  "RELIANCE": 2850.50,
  "TCS": 3680.25,
  "HDFCBANK": 1890.75,
  ...
}
```

**Implementation** (served from cache):
```python
@app.route("/api/ltp")
def get_ltp():
    by_token, by_symbol = ltp_store.snapshot()
    return jsonify(by_symbol)
```

#### **GET /api/ck**
Candlestick pattern detection (Chernikov-type patterns).

```json
{
  "patterns": [
    {
      "symbol": "RELIANCE",
      "pattern": "bullish_engulfing",
      "strength": 0.85,
      "entry_price": 2840,
      "stop_loss": 2800
    },
    ...
  ]
}
```

#### **GET /api/vcp**
Volatility Contraction Pattern detection.

```json
{
  "patterns": [
    {
      "symbol": "TCS",
      "pattern": "vcp_breakout",
      "contraction_bars": 5,
      "breakout_level": 3700,
      "confidence": 0.78
    },
    ...
  ]
}
```

#### **GET /api/ema-history**
Historical EMA values for a symbol.

```json
{
  "symbol": "RELIANCE",
  "dates": ["2026-02-01", "2026-02-02", ...],
  "ema_20": [2840, 2845, 2850, ...],
  "ema_50": [2820, 2822, 2825, ...],
  "ema_200": [2810, 2812, 2815, ...]
}
```

#### **GET /api/user-support**
Support levels for user-input symbol.

```json
{
  "symbol": "RELIANCE",
  "support_1": 2800,
  "support_2": 2750,
  "support_3": 2700,
  "resistance_1": 2900,
  "resistance_2": 2950
}
```

#### **GET /api/major-support**
Major support/resistance across all symbols.

```json
{
  "major_levels": [
    {"level": 2850, "touches": 5, "strength": 0.9},
    {"level": 2800, "touches": 3, "strength": 0.7},
    ...
  ]
}
```

### Order Management APIs

#### **GET /api/positions**
Current holdings from Kite broker.

```json
{
  "positions": [
    {
      "instrument_token": 232961,
      "tradingsymbol": "RELIANCE",
      "quantity": 1,
      "buy_price": 2840.50,
      "current_price": 2850.75,
      "pnl": 10.25,
      "pnl_pct": 0.36
    },
    ...
  ]
}
```

#### **GET /api/holdings**
Lifetime holdings (equity, mutual funds, etc).

```json
{
  "holdings": [
    {
      "tradingsymbol": "RELIANCE",
      "quantity": 5,
      "price": 2840.50,
      "last_price": 2850.75,
      "pnl": 51.25,
      "pnl_pct": 0.36
    },
    ...
  ]
}
```

#### **GET /api/trades**
All trades (open & closed) from trade_journal.

```json
{
  "trades": [
    {
      "trade_id": 1001,
      "symbol": "RELIANCE",
      "entry_date": "2026-02-02T09:30:00Z",
      "entry_price": 2840.50,
      "entry_qty": 1,
      "exit_date": "2026-02-02T14:00:00Z",
      "exit_price": 2850.75,
      "pnl": 10.25,
      "pnl_pct": 0.36,
      "duration": "4h30m",
      "strategy": "MOMENTUM",
      "status": "CLOSED"
    },
    ...
  ]
}
```

#### **GET /api/orderbook**
Recent order history with status.

```json
{
  "orders": [
    {
      "order_id": "12345",
      "tradingsymbol": "RELIANCE",
      "transaction_type": "BUY",
      "price": 2840.50,
      "quantity": 1,
      "status": "COMPLETE",
      "filled_quantity": 1,
      "timestamp": "2026-02-02T09:30:00Z"
    },
    ...
  ]
}
```

#### **GET /api/gtt**
Good-Till-Triggered (GTT) orders for trailing stops.

```json
{
  "gtts": [
    {
      "gtt_id": "304680214",
      "symbol": "RELIANCE",
      "price": 2800.00,
      "status": "ACTIVE",
      "created_at": "2026-02-02T09:35:00Z"
    },
    ...
  ]
}
```

### Strategy APIs

#### **POST /api/strategy/momentum/start**
Start the momentum strategy.

```bash
curl -X POST http://localhost:5050/api/strategy/momentum/start \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "LIVE",
    "capital": 240000,
    "per_position": 3000,
    "max_positions": 90
  }'
```

**Response**:
```json
{
  "status": "started",
  "mode": "LIVE",
  "database": "/path/to/momentum_strategy_live.db",
  "trades_loaded": 43
}
```

#### **POST /api/strategy/momentum/stop**
Stop the momentum strategy.

```bash
curl -X POST http://localhost:5050/api/strategy/momentum/stop
```

**Response**:
```json
{
  "status": "stopped"
}
```

#### **GET /api/strategy/momentum/status**
Get current strategy status and open trades.

```json
{
  "mode": "LIVE",
  "is_running": true,
  "open_positions": 43,
  "max_positions": 90,
  "deployed_capital": 109949.60,
  "available_capital": 130050.40,
  "todays_pnl": -428.45,
  "open_trades": [
    {
      "trade_id": 1001,
      "symbol": "RELIANCE",
      "position_number": 1,
      "entry_price": 2840.50,
      "current_price": 2850.75,
      "qty": 1,
      "pnl": 10.25,
      "pnl_pct": 0.36,
      "stop_loss": 2768.48,
      "target": 2982.52,
      "entry_time": "2026-02-02T09:30:00Z"
    },
    ...
  ]
}
```

#### **GET /api/strategy/momentum/parameters**
Get strategy configuration.

```json
{
  "total_capital": 240000,
  "per_position": 3000,
  "max_positions": 90,
  "scan_interval": 60,
  "min_rank_gm_threshold": 2.5,
  "position_1": {
    "stop_loss_pct": -2.5,
    "target_pct": 5.0
  },
  "position_2": {
    "stop_loss_pct": -2.5,
    "entry_condition_pnl": 0.25
  },
  "position_3": {
    "stop_loss_pct": -5.0,
    "entry_condition_avg_pnl": 1.0
  }
}
```

#### **POST /api/strategy/mode**
Switch between PAPER and LIVE mode.

```bash
curl -X POST http://localhost:5050/api/strategy/mode \
  -H "Content-Type: application/json" \
  -d '{"mode": "LIVE"}'
```

**Response**:
```json
{
  "mode_changed": "PAPER → LIVE",
  "database": "/path/to/momentum_strategy_live.db",
  "open_trades_loaded": 43
}
```

#### **POST /api/strategy/momentum/close/{trade_id}**
Manually close a specific trade.

```bash
curl -X POST http://localhost:5050/api/strategy/momentum/close/1001
```

#### **POST /api/strategy/momentum/book/{trade_id}**
Book profit on a trade (partial or full).

```bash
curl -X POST http://localhost:5050/api/strategy/momentum/book/1001 \
  -H "Content-Type: application/json" \
  -d '{"exit_price": 2850.75}'
```

#### **POST /api/strategy/momentum/clear/{trade_id}**
Clear (remove) a trade from database without executing order.

#### **POST /api/strategy/momentum/reset**
Reset strategy to initial state (dangerous - clears all trades).

---

## Strategy Execution Cycle

### Scan Cycle Flow (Every 60 seconds)

```
┌─────────────────────────────────────────────────────────┐
│           MOMENTUM STRATEGY SCAN CYCLE (60s)            │
└─────────────────────────────────────────────────────────┘
                        │
        ┌───────────────▼──────────────┐
        │  1. FETCH DAILY DATA         │
        │  - Kite.historical(...)      │
        │  - For each symbol: OHLC     │
        └───────────────▼──────────────┘
                        │
        ┌───────────────▼──────────────┐
        │  2. COMPUTE EMAs             │
        │  - EMA(20)                   │
        │  - EMA(50)                   │
        │  - EMA(200)                  │
        └───────────────▼──────────────┘
                        │
        ┌───────────────▼──────────────┐
        │  3. CALCULATE RANK_GM        │
        │  - Momentum score            │
        │  - Strength indicator        │
        └───────────────▼──────────────┘
                        │
        ┌───────────────▼──────────────┐
        │  4. FILTER SYMBOLS           │
        │  - Rank_GM > 2.5             │
        │  - Not already open          │
        │  - Sort descending           │
        └───────────────▼──────────────┘
                        │
        ┌───────────────▼──────────────┐
        │  5. CHECK CAPACITY           │
        │  - Open positions < 90?      │
        │  - Available capital > 3k?   │
        └───────────────▼──────────────┘
                        │
                ┌───────┴────────┐
                │                │
           YES  │                │  NO
                ▼                ▼
        ┌────────────────┐  (Skip entry)
        │  6. NEW ENTRY  │
        └────────────────┘
                │
        ┌───────▼───────────────────┐
        │ Place BUY order at LTP     │
        │ kite.place_order(...)      │
        └───────┬───────────────────┘
                │
        ┌───────▼───────────────────┐
        │ Record Trade in SQLite:    │
        │ - symbol                   │
        │ - entry_price              │
        │ - qty                      │
        │ - position_number = 1      │
        │ - SL, Target               │
        │ - order_id                 │
        └───────┬───────────────────┘
                │
        ┌───────▼───────────────────┐
        │ Create GTT for SL          │
        │ kite.place_gtt(...)        │
        │ Trigger: price <= SL       │
        └───────┬───────────────────┘
                │
        ┌───────▼───────────────────┐
        │  7. UPDATE EXISTING        │
        │                            │
        │ For each open trade:       │
        │ - Get latest LTP           │
        │ - Update highest_price     │
        │ - Recalculate trailing SL  │
        │ - Check target hit         │
        │ - Modify GTT if needed     │
        └───────┬───────────────────┘
                │
        ┌───────▼───────────────────┐
        │  8. CHECK POSITION 2       │
        │                            │
        │ If P1 PnL > 0.25%:         │
        │ - Place new BUY (same sym) │
        │ - Create P2 entry          │
        │ - Create GTT for SL        │
        └───────┬───────────────────┘
                │
        ┌───────▼───────────────────┐
        │  9. CHECK POSITION 3       │
        │                            │
        │ If avg(P1,P2) PnL > 1%:    │
        │ - Place new BUY (same sym) │
        │ - Create P3 entry          │
        │ - Create GTT for SL        │
        └───────┬───────────────────┘
                │
        ┌───────▼───────────────────┐
        │  10. CYCLE COMPLETE        │
        │  Wait 60 seconds           │
        │  → Goto Step 1             │
        └───────────────────────────┘
```

### Real-time Events

**KiteTicker Order Events**:
```
KiteTicker.on_order_update(data)
├─ Order filled (BUY/SELL)
├─ GTT triggered
├─ Order cancelled
└─ Kite webhook → /events/orders endpoint
    ├─ Match order_id to open trade
    ├─ Calculate exit price & PnL
    ├─ Update trade status = CLOSED
    ├─ Archive to trade_journal
    └─ Free capital for new entry
```

**GTT Trailing Stop Update**:
```
Every tick (on_ltp):
├─ For each P2, P3 trade
├─ If current_ltp > highest_price_since_entry
│  ├─ Update highest_price
│  ├─ New SL = highest × (1 - stop_loss_pct)
│  └─ If new SL > old SL (profit-protecting):
│      ├─ Modify GTT order
│      ├─ Update DB trade record
│      └─ Log trailing update
└─ Skip if SL unchanged or not profitable
```

---

## Database Schema

### SQLite: momentum_strategy_[PAPER|LIVE].db

#### Table: `trades`
```sql
CREATE TABLE trades (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    entry_price REAL NOT NULL,
    qty INTEGER NOT NULL,
    stop_loss REAL NOT NULL,
    target REAL,
    position_number INTEGER NOT NULL,
    status TEXT NOT NULL,  -- OPEN or CLOSED
    exit_time TIMESTAMP,
    exit_price REAL,
    pnl REAL,
    highest_price_since_entry REAL,
    gtt_id TEXT,
    execution_mode TEXT,
    rank_gm_at_entry REAL,
    order_id TEXT,
    central_trade_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indices for fast lookup
CREATE INDEX idx_symbol_status ON trades(symbol, status);
CREATE INDEX idx_entry_time ON trades(entry_time);
```

### PostgreSQL: ohlcv_data

```sql
CREATE TABLE ohlcv_data (
    timeframe VARCHAR(10) NOT NULL,          -- '15m', '1h', '1d'
    stockname VARCHAR(50) NOT NULL,          -- 'RELIANCE'
    candle_stock TIMESTAMP NOT NULL,         -- Candle timestamp (IST)
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume BIGINT NOT NULL DEFAULT 0
);

-- Index for fast queries
CREATE INDEX idx_stock_time ON ohlcv_data(stockname, candle_stock DESC);
```

### PostgreSQL: trade_journal (Central Trade Log)

```sql
CREATE TABLE trade_journal (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol VARCHAR(20) NOT NULL,
    entry_date TIMESTAMP NOT NULL,
    entry_price NUMERIC NOT NULL,
    entry_qty INTEGER NOT NULL,
    exit_date TIMESTAMP,
    exit_price NUMERIC,
    pnl NUMERIC,
    pnl_pct NUMERIC,
    duration VARCHAR(20),
    strategy VARCHAR(20),
    status VARCHAR(10),              -- OPEN, CLOSED
    notes TEXT,
    order_id VARCHAR(50),
    gtt_id TEXT,
    rank_gm_at_entry REAL,
    position_number INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## WebSocket Implementation Guide

### For Replacing Kite API with WebSocket Orders

This section provides the architecture and key interfaces you need to adapt for WebSocket-based order placement.

### 1. Broker Abstraction Layer

Create a `BrokerInterface` to abstract away Kite-specific calls:

```python
# broker_interface.py
from abc import ABC, abstractmethod
from typing import Dict, Optional, List
from decimal import Decimal
from datetime import datetime

class BrokerInterface(ABC):
    """Abstract base for broker implementations."""
    
    @abstractmethod
    def place_order(self, symbol: str, quantity: int, price: float, 
                   order_type: str = 'LIMIT') -> Dict:
        """
        Place a BUY or SELL order.
        
        Returns: {
            'order_id': str,
            'status': 'PENDING' | 'COMPLETE' | 'FAILED',
            'filled_qty': int,
            'filled_price': float
        }
        """
        pass
    
    @abstractmethod
    def place_gtt(self, symbol: str, trigger_price: float, 
                 quantity: int) -> Dict:
        """
        Place a Good-Till-Triggered stop-loss order.
        
        Returns: {
            'gtt_id': str,
            'status': 'ACTIVE' | 'FAILED'
        }
        """
        pass
    
    @abstractmethod
    def modify_gtt(self, gtt_id: str, new_trigger_price: float) -> Dict:
        """Modify GTT trigger price."""
        pass
    
    @abstractmethod
    def cancel_gtt(self, gtt_id: str) -> Dict:
        """Cancel a GTT order."""
        pass
    
    @abstractmethod
    def get_positions(self) -> List[Dict]:
        """Get current holdings."""
        pass
    
    @abstractmethod
    def get_orders(self) -> List[Dict]:
        """Get order history."""
        pass
    
    @abstractmethod
    def on_order_event(self, callback: Callable):
        """Register callback for order fill/update events."""
        pass
```

### 2. WebSocket Broker Implementation

```python
# websocket_broker.py
import asyncio
import websockets
import json
from typing import Dict, Callable, Optional
from broker_interface import BrokerInterface
from queue import Queue
import threading

class WebSocketBroker(BrokerInterface):
    """WebSocket-based broker for orders via WebSocket instead of REST."""
    
    def __init__(self, ws_url: str, api_key: str):
        self.ws_url = ws_url
        self.api_key = api_key
        self.ws = None
        self.order_callbacks = []
        self.pending_orders: Dict[str, Dict] = {}
        self.event_queue = Queue()
        
        # Start connection in background
        self.connection_thread = threading.Thread(
            target=self._connect_loop,
            daemon=True
        )
        self.connection_thread.start()
    
    def _connect_loop(self):
        """Run WebSocket connection loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._websocket_loop())
    
    async def _websocket_loop(self):
        """Main WebSocket loop - connects and maintains connection."""
        while True:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    
                    # Authenticate
                    auth_msg = {
                        'type': 'AUTH',
                        'api_key': self.api_key
                    }
                    await ws.send(json.dumps(auth_msg))
                    
                    # Listen for messages
                    async for message in ws:
                        data = json.loads(message)
                        await self._handle_message(data)
            except Exception as e:
                logging.error(f"WebSocket error: {e}")
                await asyncio.sleep(5)  # Reconnect after 5s
    
    async def _handle_message(self, data: Dict):
        """Handle incoming WebSocket messages."""
        msg_type = data.get('type')
        
        if msg_type == 'ORDER_UPDATE':
            order_id = data.get('order_id')
            if order_id in self.pending_orders:
                self.pending_orders[order_id].update(data)
            
            # Trigger callbacks
            for callback in self.order_callbacks:
                callback(data)
        
        elif msg_type == 'GTT_TRIGGER':
            for callback in self.order_callbacks:
                callback({
                    'type': 'GTT_TRIGGERED',
                    'gtt_id': data.get('gtt_id'),
                    'symbol': data.get('symbol'),
                    'trigger_price': data.get('trigger_price')
                })
    
    def place_order(self, symbol: str, quantity: int, price: float,
                   order_type: str = 'LIMIT') -> Dict:
        """
        Send BUY order via WebSocket.
        """
        order_id = f"ORD_{datetime.now().timestamp()}"
        
        message = {
            'type': 'PLACE_ORDER',
            'order_id': order_id,
            'symbol': symbol,
            'side': 'BUY',
            'quantity': quantity,
            'price': price,
            'order_type': order_type
        }
        
        # Send via WebSocket (async)
        asyncio.run_coroutine_threadsafe(
            self.ws.send(json.dumps(message)),
            asyncio.get_event_loop()
        )
        
        # Record pending order
        self.pending_orders[order_id] = {
            'status': 'PENDING',
            'symbol': symbol,
            'qty': quantity
        }
        
        return {
            'order_id': order_id,
            'status': 'PENDING'
        }
    
    def place_gtt(self, symbol: str, trigger_price: float,
                 quantity: int) -> Dict:
        """
        Place GTT (stop-loss) order via WebSocket.
        """
        gtt_id = f"GTT_{datetime.now().timestamp()}"
        
        message = {
            'type': 'PLACE_GTT',
            'gtt_id': gtt_id,
            'symbol': symbol,
            'trigger_price': trigger_price,
            'quantity': quantity
        }
        
        asyncio.run_coroutine_threadsafe(
            self.ws.send(json.dumps(message)),
            asyncio.get_event_loop()
        )
        
        return {
            'gtt_id': gtt_id,
            'status': 'ACTIVE'
        }
    
    def modify_gtt(self, gtt_id: str, new_trigger_price: float) -> Dict:
        """Modify GTT trigger via WebSocket."""
        message = {
            'type': 'MODIFY_GTT',
            'gtt_id': gtt_id,
            'new_trigger_price': new_trigger_price
        }
        
        asyncio.run_coroutine_threadsafe(
            self.ws.send(json.dumps(message)),
            asyncio.get_event_loop()
        )
        
        return {'status': 'PENDING'}
    
    def cancel_gtt(self, gtt_id: str) -> Dict:
        """Cancel GTT via WebSocket."""
        message = {
            'type': 'CANCEL_GTT',
            'gtt_id': gtt_id
        }
        
        asyncio.run_coroutine_threadsafe(
            self.ws.send(json.dumps(message)),
            asyncio.get_event_loop()
        )
        
        return {'status': 'PENDING'}
    
    def get_positions(self) -> List[Dict]:
        """Get positions via WebSocket REST fallback."""
        # Can fall back to REST or request via WebSocket
        pass
    
    def get_orders(self) -> List[Dict]:
        """Get order history via WebSocket REST fallback."""
        pass
    
    def on_order_event(self, callback: Callable):
        """Register callback for order events."""
        self.order_callbacks.append(callback)
```

### 3. Integration with Momentum Strategy

```python
# momentum_strategy.py (modified)

class MomentumStrategy:
    def __init__(self, broker: BrokerInterface, symbols: List[str], ltp_callback):
        self.broker = broker  # Can be KiteConnect or WebSocketBroker
        self.symbols = symbols
        self.ltp_callback = ltp_callback
        
        # Register for order events
        self.broker.on_order_event(self._on_order_event)
    
    def place_entry_order(self, symbol: str, qty: int, ltp: float) -> str:
        """Place entry order (works with any broker)."""
        result = self.broker.place_order(
            symbol=symbol,
            quantity=qty,
            price=ltp,
            order_type='MARKET'
        )
        return result['order_id']
    
    def place_gtt(self, symbol: str, stop_loss_price: float, qty: int) -> str:
        """Place stop-loss GTT (works with any broker)."""
        result = self.broker.place_gtt(
            symbol=symbol,
            trigger_price=stop_loss_price,
            quantity=qty
        )
        return result['gtt_id']
    
    def _on_order_event(self, event: Dict):
        """Handle order updates from broker (WebSocket or REST)."""
        if event.get('type') == 'ORDER_UPDATE':
            order_id = event.get('order_id')
            status = event.get('status')
            
            if status == 'COMPLETE':
                self._record_trade_execution(order_id, event)
        
        elif event.get('type') == 'GTT_TRIGGERED':
            gtt_id = event.get('gtt_id')
            self._record_exit(gtt_id, event)
```

### 4. API Integration Points

**Key places where Kite API is called** (replace with broker interface):

```python
# In app.py or strategy

# BEFORE (Kite-specific):
kite = KiteConnect(api_key=..., access_token=...)
kite.place_order(symbol=..., quantity=..., price=..., order_type='MARKET')

# AFTER (Broker-agnostic):
broker = get_broker()  # Returns KiteConnect or WebSocketBroker
broker.place_order(symbol=..., quantity=..., price=..., order_type='MARKET')


# BEFORE (Kite GTT):
kite.place_gtt(symbol=..., trigger_price=..., quantity=...)

# AFTER:
broker.place_gtt(symbol=..., trigger_price=..., quantity=...)


# BEFORE (Kite order events):
@ticker.on_order_update
def on_order_update(data):
    handle_order(data)

# AFTER:
broker.on_order_event(handle_order)
```

### 5. WebSocket Message Protocol (Example)

Define your broker's WebSocket message format:

```json
// Place Order
{
  "type": "PLACE_ORDER",
  "order_id": "ORD_1643702400.123",
  "symbol": "RELIANCE",
  "side": "BUY",
  "quantity": 1,
  "price": 2840.50,
  "order_type": "LIMIT"
}

// Order Update (from server)
{
  "type": "ORDER_UPDATE",
  "order_id": "ORD_1643702400.123",
  "status": "COMPLETE",
  "filled_qty": 1,
  "filled_price": 2840.50,
  "timestamp": "2026-02-02T09:30:15Z"
}

// Place GTT
{
  "type": "PLACE_GTT",
  "gtt_id": "GTT_1643702450.456",
  "symbol": "RELIANCE",
  "trigger_price": 2800.00,
  "quantity": 1
}

// GTT Triggered (from server)
{
  "type": "GTT_TRIGGERED",
  "gtt_id": "GTT_1643702450.456",
  "symbol": "RELIANCE",
  "trigger_price": 2800.00,
  "timestamp": "2026-02-02T14:25:30Z"
}

// Modify GTT
{
  "type": "MODIFY_GTT",
  "gtt_id": "GTT_1643702450.456",
  "new_trigger_price": 2810.00
}
```

### 6. Configuration for New Project

```yaml
# config.yaml for new project
broker:
  type: "websocket"  # or "kite"
  url: "wss://broker.example.com/ws"
  api_key: "${BROKER_API_KEY}"
  
strategy:
  capital: 240000
  per_position: 3000
  max_positions: 90
  scan_interval: 60
  
database:
  sqlite: "./trading_strategy.db"
  postgres: "postgresql://user:pass@localhost:5432/trading"
```

### 7. Migration Checklist

- [ ] Create `BrokerInterface` abstract class
- [ ] Implement `WebSocketBroker` extending `BrokerInterface`
- [ ] Create WebSocket connection manager with auto-reconnect
- [ ] Define WebSocket message protocol (JSON schema)
- [ ] Update `MomentumStrategy` to use `self.broker` instead of `self.kite`
- [ ] Replace `app.py` Kite REST calls with `broker.` calls
- [ ] Update order event handlers to use broker callbacks
- [ ] Test order placement & event streaming
- [ ] Add order queue & retry logic for failed orders
- [ ] Implement order state machine (PENDING → COMPLETE → CLOSED)
- [ ] Add logging for all broker interactions

---

## Files Reference

### Key Files Location

```
pure-alpha/
├── Webapp/
│   ├── main.py                      # Entry point
│   ├── app.py                       # Flask app (3500+ lines)
│   ├── momentum_strategy.py         # Strategy engine (2800+ lines)
│   ├── ltp_service.py              # LTP cache service
│   ├── logging_config.py           # Logging setup
│   ├── momentum_strategy_paper.db   # Paper trades DB
│   ├── momentum_strategy_live.db    # Live trades DB
│   └── templates/                  # HTML templates
│
├── pgAdmin_database/
│   ├── db_connection.py            # PostgreSQL connection
│   └── config.yaml                 # DB config
│
├── Support Files/
│   ├── param.yaml                  # Strategy parameters
│   └── NiftySymbol.py             # Stock universes
│
├── Core_files/
│   └── token.txt                   # Kite API token
│
├── Csvs/
│   └── instruments.csv             # Token mappings
│
└── logs/
    └── YYYY-MM-DD/                 # Date-wise logs
        ├── strategy.log
        ├── access.log
        ├── orders.log
        ├── trailing.log
        └── errors.log
```

### Import Hierarchy

```
main.py
├── app.py
│   ├── ltp_service.py
│   ├── logging_config.py
│   ├── cooldown.py
│   ├── momentum_strategy.py (routes)
│   └── Security modules
│       ├── src/security/auth.py
│       ├── src/security/csrf_protection.py
│       └── src/security/security_headers.py
│
├── ltp_service.py
│   ├── kiteconnect.py
│   └── logging_config.py
│
├── kiteconnect/
│   ├── connect.py        # Kite API client
│   ├── ticker.py         # KiteTicker for real-time
│   └── exceptions.py
│
├── pgAdmin_database/
│   └── db_connection.py  # PostgreSQL
│
└── Support Files/
    ├── param.yaml
    └── NiftySymbol.py
```

---

## Summary

This document covers the complete momentum trading strategy implementation:

1. **Startup**: Configuration loading, Kite API init, Flask start, KiteTicker connection
2. **Architecture**: Modular design with Flask API, strategy engine, LTP service
3. **Strategy**: Rank-based stock selection, 3-position ladder, GTT trailing stops
4. **APIs**: 20+ endpoints for market data, orders, positions, strategy control
5. **Execution**: 60s scan cycle, real-time event handling, GTT updates
6. **WebSocket**: Guide for replacing Kite API with WebSocket orders

**For WebSocket adaptation**: Follow the broker abstraction pattern to swap Kite calls with WebSocket messages while keeping strategy logic intact.

