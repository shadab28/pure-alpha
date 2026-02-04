# Futures Tab Implementation Guide

## Overview
A new **Futures** tab has been added to the CK (Crypto/Stocks) dashboard. This tab mirrors the CK tab's functionality but displays **front futures contracts** instead of stocks, with automatic expiry filtering (excluding contracts that expire tomorrow).

## What Was Added

### 1. Backend API Endpoint (`Webapp/app.py`)
**Route:** `/api/futures`

```python
@app.route('/api/futures')
def api_futures():
    """Return Futures dashboard data (front futures contracts till one day before expiry)."""
```

- Returns front futures contract data with technical indicators
- Uses the same infrastructure as CK tab
- Filters for contracts expiring after tomorrow

### 2. Service Function (`Webapp/ltp_service.py`)
**Function:** `get_futures_data()`

Features:
- Fetches all NFO (National Futures Options) instruments from Kite
- Filters for FUT (Futures) segment
- Groups by base symbol (e.g., NIFTY-26FEB, BANKNIFTY-26FEB → NIFTY, BANKNIFTY)
- Keeps only front contracts (closest to expiry, but not expiring tomorrow)
- Returns LTP, RSI (15m), SMA50/200, and technical signals
- Mirrors CK tab's signal logic (Bullish/Bearish with Buy/Hold actions)

### 3. Frontend Components (`Webapp/templates/index.html`)

#### Tab Button
```html
<button id="tab-futures" class="btn secondary">Futures</button>
```

#### Main View with Sub-tabs
```html
<div id="view-futures" class="card" style="display:none;">
  <button id="futures-subtab-momentum" class="btn small active">Momentum</button>
  <button id="futures-subtab-reversals" class="btn small secondary">Reversals at Levels</button>
  <button id="futures-subtab-strategy" class="btn small secondary">Strategy</button>
</div>
```

#### Sub-views
1. **Momentum** - Bullish futures contracts with RSI and moving average alignment
2. **Reversals at Levels** - Contracts near support/resistance (RSI < 30 or > 70)
3. **Strategy** - Note explaining shared strategy with main CK tab

#### Data Tables
- `futures-table-momentum` - Shows bullish setups
- `futures-table-reversals` - Shows reversal opportunities

### 4. JavaScript Logic

#### Fetch Function
```javascript
async function fetchFutures()
```
- Calls `/api/futures` endpoint
- Parses response and populates `latestFuturesRows`
- Auto-refreshes every 10 seconds when tab is active

#### Render Function
```javascript
function renderFuturesTables()
```
- Filters rows based on active sub-tab
- Applies sorting state
- Updates HTML tables with formatted data

#### Sub-tab Switching
```javascript
function setFuturesSubtab(subtab)
```
- Similar to CK tab's sub-tab logic
- Supports: 'momentum', 'reversals', 'strategy'

## How It Works

### Data Flow
```
Kite API (instruments)
    ↓
get_futures_data() (filter for front contracts)
    ↓
/api/futures endpoint
    ↓
fetchFutures() (JavaScript)
    ↓
renderFuturesTables()
    ↓
Display in HTML tables
```

### Front Contract Selection
- Gets all NFO FUT instruments
- Parses expiry date from contract name
- Filters for contracts expiring **tomorrow or later** (excludes same-day expiry)
- Groups by base symbol (NIFTY, BANKNIFTY, etc.)
- Keeps only the closest expiry for each base symbol

### Technical Signals
Same logic as CK tab:
- **Bullish** - 15m SMA50 > SMA200
- **Bearish** - 15m SMA50 < SMA200
- **Neutral** - Unable to determine

### Actions
- **Trade / Add on strength** - Bullish + price above SMA50
- **Accumulate on pullback** - Bullish + price below SMA50
- **Wait for setup** - Bearish

## Configuration

### Timeframe
- Uses **15-minute** candlestick data (same as CK tab)
- Refreshes every **10 seconds** when tab is active

### Default Sort Order
- **Momentum table** - Sorted by last_price (descending)
- **Reversals table** - Sorted by last_price (ascending)

## Usage

1. Click the **Futures** tab in the dashboard
2. View **Momentum** sub-tab to see bullish front contracts
3. Switch to **Reversals at Levels** to find reversal opportunities
4. Check **Strategy** for notes on shared strategy logic
5. Click "Buy" button to place trades (if integrated)

## Example Output

### Momentum Table
| Symbol | Expiry | Last Price | RSI (15m) | SMA50 (15m) | SMA200 (15m) | % vs SMA50 | Signal | Action |
|--------|--------|------------|-----------|-------------|-------------|-----------|--------|--------|
| NIFTY26FEB | 2026-02-26 | 24,500 | 65 | 24,300 | 24,000 | 0.82% | Bullish | Trade / Add on strength |
| BANKNIFTY26FEB | 2026-02-26 | 48,200 | 52 | 48,100 | 47,900 | 0.21% | Bullish | Accumulate on pullback |

## Integration Points

### API Endpoints
- `/api/futures` - Main data endpoint

### Database
- Uses same LTP cache as main tab (`_rsi15m_cache`)
- Optional: Can store historical futures data in PostgreSQL (similar to stocks)

### Real-time Updates
- Tickers subscribed via Kite API
- LTP updated via WebSocket
- Technical indicators recalculated every 10 seconds

## Future Enhancements

1. **Options Tab** - Similar to Futures but for options chains
2. **Spread Analysis** - Calendar spreads, diagonal spreads
3. **Greeks Display** - Delta, Gamma, Vega, Theta for options
4. **Expiry Calendar** - Show all available contract months
5. **Historical Backtest** - Test strategies on historical futures data
6. **Automated Alerts** - Notify when conditions are met

## Troubleshooting

### No Futures Contracts Found
- Check if Kite connection is active
- Verify instruments are being fetched
- Check logs: `logs/YYYY-MM-DD/strategy.log`

### Empty Table
- Ensure LTP service is running
- Check WebSocket ticker subscription
- Verify expiry filtering is not too restrictive

### Stale Data
- Refresh manually with browser F5
- Check auto-refresh interval (10 seconds)
- Verify `/api/futures` endpoint is responding

## Files Modified

1. `/Webapp/app.py` - Added `/api/futures` route
2. `/Webapp/ltp_service.py` - Added `get_futures_data()` function
3. `/Webapp/templates/index.html` - Added Futures tab UI and JavaScript logic

## Code Changes Summary

### app.py (16 lines added)
```python
@app.route('/api/futures')
def api_futures():
    """Return Futures dashboard data..."""
    try:
        from ltp_service import get_futures_data
        res = get_futures_data()
        if 'error' in res:
            return jsonify({"error": res.get('error')}), 500
        return jsonify(res)
    except Exception as e:
        error_logger.exception("/api/futures failed: %s", e)
        return jsonify({"error": str(e)}), 500
```

### ltp_service.py (~120 lines added)
New function implementing futures data fetching with contract filtering logic.

### index.html (~300 lines added)
- Tab button
- View container with sub-tabs
- Two data tables
- JavaScript fetch, render, and sub-tab logic
