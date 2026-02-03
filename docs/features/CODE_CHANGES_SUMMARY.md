# Code Changes Summary - EMA History Feature

## File 1: Webapp/templates/index.html

### Change 1: Add Tab Button (Line 66)

**Location**: Tab navigation buttons section

**Added Code**:
```html
<button id="tab-ema-history" class="btn secondary">EMA History (15m)</button>
```

**Context**:
```html
<div class="btn-row" style="margin-top:-4px;">
  <button id="tab-main" class="btn secondary">All</button>
  <button id="tab-filtered" class="btn secondary">Filtered</button>
  <button id="tab-ck" class="btn secondary">CK</button>
  <button id="tab-vcp" class="btn secondary">VCP</button>
  <button id="tab-ema-history" class="btn secondary">EMA History (15m)</button>  <!-- NEW -->
  <button id="tab-orders" class="btn secondary">Orders</button>
  <button id="tab-active" class="btn secondary">Active Orderbook</button>
  <button id="tab-trades" class="btn secondary">Trade Journal</button>
</div>
```

---

### Change 2: Add EMA History Card View (Lines 468-491)

**Location**: Between Active Orderbook view and Orders view

**Added Code**:
```html
<!-- EMA History (15m) view -->
<div id="view-ema-history" class="card" style="display:none; margin-top:14px;">
  <div class="card-header" style="display:flex;align-items:center;justify-content:space-between;">
    <span>EMA History (15-Minute Candles)</span>
    <div style="display:flex; gap:10px; align-items:center;">
      <input id="ema-filter-symbol" type="text" placeholder="Filter by symbol..." style="padding:6px; width:150px;" />
      <button class="btn secondary" id="btn-refresh-ema-history">Refresh</button>
    </div>
  </div>
  <div class="card-body">
    <table id="ema-history-table">
      <thead>
        <tr>
          <th data-key="timestamp">Timestamp</th>
          <th data-key="symbol">Symbol</th>
          <th data-key="ema_20">EMA 20</th>
          <th data-key="ema_50">EMA 50</th>
          <th data-key="ema_100">EMA 100</th>
          <th data-key="ema_200">EMA 200</th>
          <th data-key="ltp">Last Traded Price</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>
</div>
```

---

### Change 3: Update Tab References (Line 938)

**Location**: Tab management JavaScript

**Added Code**:
```javascript
const tabEmaHistory = document.getElementById('tab-ema-history');
```

**Context**:
```javascript
const tabMain = document.getElementById('tab-main');
const tabFiltered = document.getElementById('tab-filtered');
const tabOrders = document.getElementById('tab-orders');
const tabActive = document.getElementById('tab-active');
const tabCK = document.getElementById('tab-ck');
const tabVCP = document.getElementById('tab-vcp');
const tabEmaHistory = document.getElementById('tab-ema-history');  // NEW
const tabTrades = document.getElementById('tab-trades');
```

---

### Change 4: Update setSelectedTab Function (Line 947)

**Location**: View hiding loop

**Original**:
```javascript
['view-main','view-filtered','view-orders','view-active','view-ck','view-vcp','view-trades'].forEach(id=>{
```

**Updated**:
```javascript
['view-main','view-filtered','view-orders','view-active','view-ck','view-vcp','view-ema-history','view-trades'].forEach(id=>{
```

---

### Change 5: Update Tab Array (Line 947)

**Location**: Tab class management

**Original**:
```javascript
[tabMain, tabFiltered, tabOrders, tabActive, tabCK, tabVCP, tabTrades].forEach(t=>{
```

**Updated**:
```javascript
[tabMain, tabFiltered, tabOrders, tabActive, tabCK, tabVCP, tabEmaHistory, tabTrades].forEach(t=>{
```

---

### Change 6: Add Event Listener (Line ~955)

**Location**: Tab click handlers

**Added Code**:
```javascript
if (tabEmaHistory) tabEmaHistory.addEventListener('click', ()=> { setSelectedTab(tabEmaHistory, 'view-ema-history'); fetchEmaHistory(); });
```

**Context**:
```javascript
tabMain.addEventListener('click', ()=> setSelectedTab(tabMain, 'view-main'));
tabFiltered.addEventListener('click', ()=> setSelectedTab(tabFiltered, 'view-filtered'));
tabOrders.addEventListener('click', ()=> { setSelectedTab(tabOrders, 'view-orders'); fetchOrders(); });
if (tabActive) tabActive.addEventListener('click', ()=> { setSelectedTab(tabActive, 'view-active'); fetchActiveOrderbook(); });
if (tabCK) tabCK.addEventListener('click', ()=> { setSelectedTab(tabCK, 'view-ck'); fetchCK(); });
if (tabVCP) tabVCP.addEventListener('click', ()=> { setSelectedTab(tabVCP, 'view-vcp'); fetchVCP(); });
if (tabEmaHistory) tabEmaHistory.addEventListener('click', ()=> { setSelectedTab(tabEmaHistory, 'view-ema-history'); fetchEmaHistory(); });  // NEW
if (tabTrades) tabTrades.addEventListener('click', ()=> { setSelectedTab(tabTrades, 'view-trades'); fetchTrades(); });
```

---

### Change 7: Add EMA History Logic (Lines 1295-1413)

**Location**: After VCP logic

**Added Code**:
```javascript
// ========== EMA HISTORY (15-MINUTE) LOGIC ==========
let latestEmaHistoryRows = [];
let sortStateEmaHistory = { key: 'timestamp', dir: 'desc' };
let emaHistoryInterval = null;
let emaHistoryFilterSymbol = '';

function renderEmaHistoryTable() {
  applySort(latestEmaHistoryRows, sortStateEmaHistory);
  const tbody = document.querySelector('#ema-history-table tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  
  // Apply symbol filter if set
  const filterSymbol = document.getElementById('ema-filter-symbol')?.value?.trim().toUpperCase() || '';
  let filtered = latestEmaHistoryRows;
  if (filterSymbol) {
    filtered = latestEmaHistoryRows.filter(r => r.symbol && r.symbol.toUpperCase().includes(filterSymbol));
  }
  
  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="muted">No EMA history data available</td></tr>';
    return;
  }
  
  for (const r of filtered) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${r.timestamp || ''}</td>
      <td><strong>${r.symbol || ''}</strong></td>
      <td>${r.ema_20 != null ? Number(r.ema_20).toFixed(2) : '<span class="muted">-</span>'}</td>
      <td>${r.ema_50 != null ? Number(r.ema_50).toFixed(2) : '<span class="muted">-</span>'}</td>
      <td>${r.ema_100 != null ? Number(r.ema_100).toFixed(2) : '<span class="muted">-</span>'}</td>
      <td>${r.ema_200 != null ? Number(r.ema_200).toFixed(2) : '<span class="muted">-</span>'}</td>
      <td class="num"><strong>${r.ltp != null ? Number(r.ltp).toFixed(2) : '<span class="muted">-</span>'}</strong></td>
    `;
    tbody.appendChild(tr);
  }
}

async function fetchEmaHistory() {
  try {
    const res = await fetch('/api/ema-history');
    const j = await res.json();
    if (!res.ok) throw new Error(j.error || 'Failed to fetch EMA history');
    
    const data = j.data || {};
    const rows = [];
    
    // Transform data into rows format
    if (Array.isArray(data)) {
      // If it's an array of candle records
      for (const candle of data) {
        rows.push({
          timestamp: candle.timestamp || '',
          symbol: candle.symbol || '',
          ema_20: (typeof candle.ema_20 === 'number') ? candle.ema_20 : (candle.ema_20 ? Number(candle.ema_20) : null),
          ema_50: (typeof candle.ema_50 === 'number') ? candle.ema_50 : (candle.ema_50 ? Number(candle.ema_50) : null),
          ema_100: (typeof candle.ema_100 === 'number') ? candle.ema_100 : (candle.ema_100 ? Number(candle.ema_100) : null),
          ema_200: (typeof candle.ema_200 === 'number') ? candle.ema_200 : (candle.ema_200 ? Number(candle.ema_200) : null),
          ltp: (typeof candle.ltp === 'number') ? candle.ltp : (candle.ltp ? Number(candle.ltp) : null)
        });
      }
    } else {
      // If it's an object with symbols as keys
      for (const symbol of Object.keys(data).sort()) {
        const symbolData = data[symbol];
        if (symbolData && Array.isArray(symbolData.candles)) {
          for (const candle of symbolData.candles) {
            rows.push({
              timestamp: candle.timestamp || '',
              symbol: symbol,
              ema_20: (typeof candle.ema_20 === 'number') ? candle.ema_20 : (candle.ema_20 ? Number(candle.ema_20) : null),
              ema_50: (typeof candle.ema_50 === 'number') ? candle.ema_50 : (candle.ema_50 ? Number(candle.ema_50) : null),
              ema_100: (typeof candle.ema_100 === 'number') ? candle.ema_100 : (candle.ema_100 ? Number(candle.ema_100) : null),
              ema_200: (typeof candle.ema_200 === 'number') ? candle.ema_200 : (candle.ema_200 ? Number(candle.ema_200) : null),
              ltp: (typeof candle.ltp === 'number') ? candle.ltp : (candle.ltp ? Number(candle.ltp) : null)
            });
          }
        }
      }
    }
    
    latestEmaHistoryRows = rows;
    renderEmaHistoryTable();
  } catch (e) {
    const tbody = document.querySelector('#ema-history-table tbody');
    if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="muted">${escapeHtml(e.message)}</td></tr>`;
  }
  
  // Polling management: if EMA History view visible, ensure interval running
  try { clearInterval(emaHistoryInterval); } catch (err) {}
  if (document.getElementById('view-ema-history').style.display === 'block') {
    emaHistoryInterval = setInterval(fetchEmaHistory, 10000);
  }
}

// Attach sorting to EMA History table
attachSortingFor('ema-history-table', sortStateEmaHistory, renderEmaHistoryTable);

// Filter event listener
const emaFilterInput = document.getElementById('ema-filter-symbol');
if (emaFilterInput) {
  emaFilterInput.addEventListener('keyup', renderEmaHistoryTable);
}

// Refresh button
const btnRefreshEmaHistory = document.getElementById('btn-refresh-ema-history');
if (btnRefreshEmaHistory) {
  btnRefreshEmaHistory.addEventListener('click', fetchEmaHistory);
}
```

---

## File 2: Webapp/app.py

### Change: Add EMA History API Endpoint (Lines 529-558)

**Location**: After `/api/vcp` endpoint

**Added Code**:
```python
@app.route('/api/ema-history')
def api_ema_history():
	"""Return historical EMA values for 15-minute candles.
	
	Returns recent 15-minute candles with EMA 20, 50, 100, 200 values.
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
		from ltp_service import get_ema_history  # type: ignore
		res = get_ema_history()
		if 'error' in res:
			return jsonify({"error": res.get('error')}), 500
		return jsonify(res)
	except Exception as e:
		error_logger.exception("/api/ema-history failed: %s", e)
		return jsonify({"error": str(e)}), 500
```

---

## File 3: Webapp/ltp_service.py

### Change: Add get_ema_history Function (Lines 1656-1747)

**Location**: At end of file (after `manual_refresh_all_averages()`)

**Added Code**:
```python
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
```

---

## Summary of Changes

| File | Change Type | Lines | Description |
|------|------------|-------|-------------|
| `index.html` | Added | 66 | Tab button for EMA History |
| `index.html` | Added | 468-491 | Card view with table |
| `index.html` | Added | 938 | Element reference |
| `index.html` | Modified | 947 | Added to view array |
| `index.html` | Modified | 947 | Added to tab array |
| `index.html` | Added | ~955 | Event listener |
| `index.html` | Added | 1295-1413 | JavaScript logic |
| `app.py` | Added | 529-558 | Flask API endpoint |
| `ltp_service.py` | Added | 1656-1747 | Backend data function |

**Total Lines Added**: ~450 lines
**Total Files Modified**: 3
**Breaking Changes**: None
**Dependencies Added**: None
