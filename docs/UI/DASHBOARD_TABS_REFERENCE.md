# Quick Reference - Strategy Tabs & Components

## Dashboard Tabs (UI/Frontend)

### 1. **Strategy Control Tab**
- **Start/Stop Button**: Toggle momentum strategy on/off
- **Mode Selector**: Switch between PAPER and LIVE
- **Parameters Display**: Show current configuration
  - Total Capital: ₹240,000
  - Per Position: ₹3,000
  - Max Positions: 90
  - Scan Interval: 60s
  - Min Rank_GM: 2.5
- **Status Indicator**: Green (running) / Red (stopped)

**API Calls**:
```
POST /api/strategy/momentum/start
POST /api/strategy/momentum/stop
POST /api/strategy/mode  (with mode: LIVE/PAPER)
GET  /api/strategy/momentum/status
GET  /api/strategy/momentum/parameters
```

---

### 2. **Open Positions Tab**
- **Table with columns**:
  - Symbol
  - Position Number (P1, P2, P3)
  - Entry Price
  - Current Price
  - Quantity
  - PnL (absolute)
  - PnL %
  - Entry Time
  - Stop Loss
  - Target (if P1)
  - Highest Price Since Entry
  - Action Buttons (Close, Book Profit, etc.)

**Data Source**:
```
GET /api/strategy/momentum/status
  → response.open_trades[]
```

**Key Metrics Calculated**:
```python
current_pnl = (current_price - entry_price) * qty
pnl_pct = (pnl / (entry_price * qty)) * 100
position_status = "P1" | "P2" | "P3"
```

---

### 3. **Closed Trades Tab**
- **Table with columns**:
  - Symbol
  - Entry Price
  - Entry Time
  - Exit Price
  - Exit Time
  - Quantity
  - PnL (absolute)
  - PnL %
  - Duration
  - Status (CLOSED, STOPPED)
  - Entry Reason (Rank_GM score)

**Data Source**:
```
GET /api/trades
  → response.trades[] (filtered by status=CLOSED)
```

---

### 4. **Market Scanner Tab**

#### **Sub-tab 4.1: Candlestick (CK) Patterns**
- **Patterns Detected**:
  - Bullish Engulfing
  - Bearish Engulfing
  - Hammer
  - Shooting Star
  - Doji
  - Morning Star
  - Evening Star

**Table Columns**:
- Symbol
- Pattern Name
- Pattern Strength (0-1 scale)
- Suggested Entry Price
- Suggested Stop Loss
- Entry Confidence %

**Data Source**:
```
GET /api/ck
  → response.patterns[]
```

---

#### **Sub-tab 4.2: VCP (Volatility Contraction Pattern)**
- **Pattern Details**:
  - Contraction Phase (narrow range bars)
  - Expansion Phase (breakout)
  - Entry Setup

**Table Columns**:
- Symbol
- Contraction Bars Count
- Contraction Range (High - Low)
- Breakout Direction (UP/DOWN)
- Breakout Level
- Confidence Score

**Data Source**:
```
GET /api/vcp
  → response.patterns[]
```

---

#### **Sub-tab 4.3: EMA History**
- **Chart**: 3-line chart showing price + two EMAs
  - Price Line (blue)
  - EMA-20 (orange)
  - EMA-50 (red)

**Interaction**:
- Symbol selector dropdown
- Date range picker
- Trend analysis display

**Data Source**:
```
GET /api/ema-history?symbol=RELIANCE&days=60
  → response.dates[], response.ema_20[], response.ema_50[]
```

---

### 5. **Support/Resistance Tab**

#### **Sub-tab 5.1: User Support Levels**
- **Input**: Symbol dropdown or search
- **Display**: Support & Resistance levels
  - Support 1, 2, 3 (levels below current price)
  - Resistance 1, 2 (levels above current price)
  - Bounce History (how many times touched)
  - Strength Indicator

**Data Source**:
```
POST /api/user-support
  body: {"symbol": "RELIANCE"}
  → response.support_1, support_2, support_3, resistance_1, resistance_2
```

---

#### **Sub-tab 5.2: Major Support Levels**
- **Table**: All significant support/resistance across all symbols
  - Support Level (price)
  - Number of Touches
  - Strength (0-1 scale)
  - Symbols touching this level

**Data Source**:
```
GET /api/major-support
  → response.major_levels[]
```

---

#### **Sub-tab 5.3: Order Book Analysis**
- **Table**: Recent order history
  - Symbol
  - Order Type (BUY/SELL)
  - Price
  - Quantity
  - Status (COMPLETE, CANCELLED, PENDING)
  - Timestamp
  - Duration

**Data Source**:
```
GET /api/orderbook
  → response.orders[]
```

---

### 6. **Broker Status Tab**

#### **Sub-tab 6.1: Positions**
- **Live Holdings** from broker
- **Table Columns**:
  - Symbol
  - Quantity
  - Buy Price
  - Current Price
  - PnL (absolute & %)
  - Margin Used

**Data Source**:
```
GET /api/positions
  → response.positions[]
```

---

#### **Sub-tab 6.2: Holdings**
- **Lifetime Equity Holdings**
- **Table Columns**:
  - Symbol
  - Quantity
  - Buy Price
  - Last Price
  - PnL
  - Percentage Gain

**Data Source**:
```
GET /api/holdings
  → response.holdings[]
```

---

#### **Sub-tab 6.3: GTT Orders**
- **Good-Till-Triggered Orders** (stop-losses)
- **Table Columns**:
  - GTT ID
  - Symbol
  - Trigger Price
  - Status (ACTIVE, TRIGGERED, CANCELLED)
  - Created Time
  - Action Buttons (Modify, Cancel)

**Data Source**:
```
GET /api/gtt
  → response.gtts[]
```

---

#### **Sub-tab 6.4: Recent Orders**
- **Order History** from broker
- **Table Columns**:
  - Order ID
  - Symbol
  - Type (BUY/SELL)
  - Price
  - Quantity
  - Status
  - Filled Quantity
  - Time

**Data Source**:
```
GET /api/trades
  → response.trades[] (with order_id)
```

---

### 7. **Statistics Tab**

#### **Sub-tab 7.1: Daily Summary**
- **Metrics**:
  - Today's PnL (absolute & %)
  - Win Rate (%)
  - Profit Factor (gross_profit / gross_loss)
  - Trades Today (count)
  - Avg Trade Duration
  - Max Profit Trade
  - Max Loss Trade

**Calculation**:
```python
todays_pnl = sum(trade.pnl for trade in closed_trades if trade.exit_date.date() == today)
win_count = count(trades where pnl > 0)
win_rate = win_count / total_trades * 100
```

---

#### **Sub-tab 7.2: Cumulative Performance**
- **All-Time Stats**:
  - Total PnL
  - Win/Loss Count
  - Win Rate %
  - Profit Factor
  - Risk-Reward Ratio
  - Best Trade
  - Worst Trade
  - Average Trade Duration

**Charts**:
- Equity Curve (cumulative PnL over time)
- Daily PnL Bar Chart
- Trade Distribution Histogram

---

#### **Sub-tab 7.3: Symbol Performance**
- **Per-Symbol Stats**:
  - Symbol
  - Trades Count
  - Win Rate
  - Avg Profit
  - Total PnL
  - Best Trade
  - Worst Trade

**Sorting**: By PnL, Win Rate, or Trade Count

---

### 8. **Risk Management Tab**

#### **Sub-tab 8.1: Capital Allocation**
- **Pie Chart**:
  - Deployed Capital (P1 + P2 + P3 in open positions)
  - Available Capital (free to deploy)
  - Max Available

**Display**:
```
Deployed: ₹109,949.60 (45.8%)
Available: ₹130,050.40 (54.2%)
Max: ₹240,000
```

---

#### **Sub-tab 8.2: Position Ladder**
- **Current Position Distribution**:
  - P1 Count (fixed SL, fixed target)
  - P2 Count (floating SL, no target)
  - P3 Count (floating SL with larger trail, no target)
  - Empty Slots (max 90 - current)

**Display**:
```
Position 1: 23 / 30 slots
Position 2: 15 / 30 slots
Position 3: 5 / 30 slots
Total: 43 / 90 positions
```

---

#### **Sub-tab 8.3: Trailing Stop Status**
- **For each P2 & P3**:
  - Symbol
  - Current Price
  - Highest Price Since Entry
  - Current SL
  - Trail Percentage
  - Last Update Time
  - GTT Status (ACTIVE/TRIGGERED)

**Key Concept**:
```
For P2: SL = highest_price * (1 - 2.5%)
For P3: SL = highest_price * (1 - 5%)
Updated: Every LTP tick if new high
```

---

### 9. **Manual Controls Tab**

#### **Sub-tab 9.1: Trade Management**
- **For Each Open Trade**:
  - Close Trade Button (exit at market)
  - Book Profit Button (manual exit with price input)
  - Modify SL Button (manual SL adjustment)
  - Cancel GTT Button (remove auto SL)

**API Calls**:
```
POST /api/strategy/momentum/close/{trade_id}
POST /api/strategy/momentum/book/{trade_id}
  body: {"exit_price": 2850.75}
```

---

#### **Sub-tab 9.2: GTT Management**
- **Modify GTT**:
  - Enter new trigger price
  - Confirm modification
  - Shows GTT history

**API Calls**:
```
POST /api/gtt/recreate
  body: {"gtt_id": "...", "new_trigger_price": 2810.00}
```

---

#### **Sub-tab 9.3: Emergency Controls**
- **Reset Strategy**: Clear all trades (dangerous!)
- **Pause Trading**: Stop accepting new entries (continue monitoring)
- **Enable Paper Mode**: Switch to paper trading

**API Calls**:
```
POST /api/strategy/momentum/reset (⚠️ dangerous)
POST /api/strategy/momentum/stop
POST /api/strategy/mode body: {"mode": "PAPER"}
```

---

### 10. **Live Events Tab**

#### **Sub-tab 10.1: Order Events Stream**
- **WebSocket Stream**: Real-time order updates
  - New Order Placed
  - Order Executed
  - Order Cancelled
  - GTT Triggered
  - Trade Closed

**Event Format**:
```json
{
  "timestamp": "2026-02-02T14:25:30Z",
  "type": "ORDER_UPDATE",
  "symbol": "RELIANCE",
  "order_id": "ORD-1234",
  "status": "COMPLETE",
  "price": 2840.50,
  "qty": 1
}
```

**Data Source**:
```
WebSocket: /events/orders
GET /api/ltp (for live prices)
```

---

#### **Sub-tab 10.2: Strategy Activity Log**
- **Scrollable log** of strategy decisions:
  - New entry placed
  - P2 entry condition met
  - P3 entry condition met
  - Trailing SL updated
  - Position closed (profit/loss)
  - Cycle complete summary

**Severity Levels**: INFO, WARNING, ERROR

---

### 11. **Settings Tab**

#### **Sub-tab 11.1: Strategy Parameters**
- **Editable Fields** (reload required):
  - Total Capital
  - Per Position Amount
  - Max Positions
  - Scan Interval (seconds)
  - Min Rank_GM Threshold
  - Position-specific SL/Target percentages

**Changes Saved To**: `param.yaml` or API config

---

#### **Sub-tab 11.2: API Configuration**
- **Broker Selection**: WEBSOCKET | KITE
- **WebSocket URL**: wss://...
- **API Keys**: (masked display)
- **Connection Status**: Connected / Disconnected

---

#### **Sub-tab 11.3: Database Configuration**
- **SQLite Path**: Display current DB file
- **PostgreSQL**: Host, Port, Database name
- **Connection Test**: Button to test connection

---

## Key Data Flows

### Entry Signal Flow
```
1. Scanner runs every 60s
2. Calculates Rank_GM for each symbol
3. Filters: Rank_GM > 2.5 & not already open
4. Place_order(symbol, qty)
   ↓
5. Record trade in SQLite
6. Create GTT for stop-loss
7. Notify UI → Trade appears in "Open Positions"
```

### Exit Signal Flow
```
1. GTT triggered at stop-loss price
   ↓
2. Kite webhook → /events/orders
   ↓
3. Strategy receives GTT_TRIGGERED
4. Match to open trade in DB
5. Calculate PnL
6. Update trade status = CLOSED
7. Archive to closed_trades
8. UI refreshes → Trade moves to "Closed"
```

### Trailing Stop Update Flow
```
Every LTP tick:
  1. For each P2, P3 trade
  2. current_price > highest_price?
     YES:
        - highest_price = current_price
        - new_sl = highest × (1 - stop_loss_pct)
        - new_sl > old_sl?
           YES:
             - Modify GTT with new SL
             - Log trailing update
```

---

## API Quick Ref

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/strategy/momentum/start` | POST | Start strategy |
| `/api/strategy/momentum/stop` | POST | Stop strategy |
| `/api/strategy/momentum/status` | GET | Get open trades & status |
| `/api/strategy/momentum/parameters` | GET | Get config |
| `/api/strategy/mode` | POST | Switch PAPER/LIVE |
| `/api/strategy/momentum/close/{id}` | POST | Close trade |
| `/api/ltp` | GET | Live prices |
| `/api/ck` | GET | Candlestick patterns |
| `/api/vcp` | GET | Volatility patterns |
| `/api/ema-history` | GET | EMA chart data |
| `/api/user-support` | GET/POST | Support levels |
| `/api/major-support` | GET/POST | Major levels |
| `/api/trades` | GET | All trades |
| `/api/positions` | GET | Holdings |
| `/api/holdings` | GET | Lifetime holdings |
| `/api/orderbook` | GET | Order history |
| `/api/gtt` | GET | GTT orders |
| `/events/orders` | WS | Order event stream |

---

## Common User Interactions

### "I want to close a trade manually"
```
1. Click trade in "Open Positions"
2. Click "Close Trade" button
3. API: POST /api/strategy/momentum/close/{trade_id}
4. Trade immediately exits at market price
5. UI updates: Trade moves to "Closed Trades"
```

### "I want to modify a stop-loss"
```
1. Find GTT in "GTT Orders" tab
2. Click "Modify" button
3. Enter new trigger price
4. API: POST /api/gtt/recreate
5. GTT updated with new price
```

### "I want to view today's P&L"
```
1. Go to "Statistics" → "Daily Summary"
2. Shows:
   - Today's PnL: ₹-428.45 (📉)
   - Win Rate: 65%
   - Trades: 12
   - Avg Duration: 2h15m
```

### "I want to check trailing stops"
```
1. Go to "Risk Management" → "Trailing Stop Status"
2. For each P2/P3:
   - Current Price vs Highest Price
   - Updated trailing SL
   - Last update time
```

### "I want to see market patterns"
```
1. Go to "Market Scanner"
2. Check "CK Patterns" & "VCP Patterns"
3. See which symbols have setups
4. Can use as entry hints (manual or system)
```

---

## Summary Metrics at a Glance

**Top Dashboard Widget**:
```
┌─────────────────────────────────────────────────┐
│ Strategy Status: ✅ RUNNING (LIVE MODE)         │
│                                                 │
│ Open Positions: 43/90                           │
│ Today's PnL: ₹-428.45 (📉 -0.18%)              │
│ Deployed Capital: ₹109,949.60 (45.8%)           │
│ Available Capital: ₹130,050.40 (54.2%)          │
│                                                 │
│ Win Rate: 65% | Avg Trade: 2h15m                │
└─────────────────────────────────────────────────┘
```

