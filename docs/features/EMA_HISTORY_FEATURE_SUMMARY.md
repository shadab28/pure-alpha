# EMA History (15-Minute) Feature Implementation Summary

## Overview
Added a new **EMA History (15-Minute)** UI section to the webapp dashboard that displays historical EMA values (20, 50, 100, 200) for 15-minute candles along with the Last Traded Price (LTP).

## Changes Made

### 1. **Frontend - HTML (`Webapp/templates/index.html`)**

#### Tab Button Added
- **Location**: Line ~66
- Added new tab button: `<button id="tab-ema-history" class="btn secondary">EMA History (15m)</button>`
- Positioned between VCP and Orders tabs

#### Card View Section Added
- **Location**: Lines 468-491
- New card container with ID `view-ema-history` (hidden by default)
- Card header includes:
  - Title: "EMA History (15-Minute Candles)"
  - Symbol filter input (`#ema-filter-symbol`)
  - Refresh button (`#btn-refresh-ema-history`)
- Card body contains table `#ema-history-table` with columns:
  - **Timestamp** (sortable)
  - **Symbol** (sortable)
  - **EMA 20** (sortable)
  - **EMA 50** (sortable)
  - **EMA 100** (sortable)
  - **EMA 200** (sortable)
  - **Last Traded Price** (sortable)

### 2. **Frontend - JavaScript (`Webapp/templates/index.html`)**

#### Tab Management
- **Location**: Lines 932-936
- Added `tabEmaHistory` element reference
- Updated `setSelectedTab()` to include `view-ema-history`
- Updated tab array to include `tabEmaHistory`
- Added event listener: `tabEmaHistory.addEventListener('click', () => { setSelectedTab(...); fetchEmaHistory(); })`

#### EMA History Logic (Lines 1295-1413)
```javascript
// State variables
- latestEmaHistoryRows: Array of candle records
- sortStateEmaHistory: Sorting configuration (default: timestamp DESC)
- emaHistoryInterval: Auto-refresh interval reference
- emaHistoryFilterSymbol: Filter state

// Functions

1. renderEmaHistoryTable()
   - Applies sorting to data
   - Filters by symbol (case-insensitive)
   - Renders rows with formatted numbers (2 decimal places)
   - Shows muted "-" for null values
   - Displays "No data" message when empty

2. fetchEmaHistory()
   - Calls `/api/ema-history` endpoint
   - Handles both array and object response formats
   - Converts all numeric values to floats
   - Updates latestEmaHistoryRows and re-renders
   - Manages auto-polling interval (10 seconds when visible)
   - Error handling with display message

3. Event Listeners
   - Symbol filter input: real-time filtering on keyup
   - Refresh button: manual fetch trigger
   - Header sorting: attachSortingFor() for all columns
```

### 3. **Backend - Flask Route (`Webapp/app.py`)**

#### New API Endpoint
- **Route**: `@app.route('/api/ema-history')`
- **Location**: After `/api/vcp` endpoint
- **Method**: GET
- **Response Format**:
```json
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
    }
  ]
}
```
- Error response: `{"error": "description"}`

### 4. **Backend - Data Service (`Webapp/ltp_service.py`)**

#### New Function: `get_ema_history()`
- **Location**: End of file (after `manual_refresh_all_averages()`)
- **Purpose**: Fetch 15-minute OHLCV data with EMA values from PostgreSQL
- **Implementation**:
  1. Connects to PostgreSQL via `pg_cursor()`
  2. Retrieves list of all unique symbols
  3. For each symbol, fetches 20 most recent 15-minute candles
  4. Queries: `timestamp, ema_20, ema_50, ema_100, ema_200, close`
  5. Converts all timestamps to `YYYY-MM-DD HH:MM:SS` format
  6. Returns array of candle records sorted by timestamp
  7. Handles missing EMA values gracefully (returns `null`)
- **Database Query**:
  ```sql
  SELECT timestamp, ema_20, ema_50, ema_100, ema_200, close
  FROM ohlcv_data
  WHERE symbol = ? AND timeframe = '15m'
  ORDER BY timestamp DESC
  LIMIT 20
  ```

## UI/UX Features

### Styling & Layout
- **Reuses existing webapp design**:
  - Same card layout (border, background, shadow)
  - Same header styling with flex layout
  - Same table styling (borders, alternating row colors, hover effects)
  - Same button styles (secondary, small, with hover effect)
  - Same color scheme (blue palette, text colors, muted text)

### Interactivity
- **Sorting**: Click column headers to sort ascending/descending
- **Filtering**: Real-time symbol filter (case-insensitive substring match)
- **Auto-refresh**: Polls every 10 seconds when tab is active
- **Manual refresh**: Click "Refresh" button for immediate update
- **Data Formatting**: 
  - Timestamps displayed as `YYYY-MM-DD HH:MM:SS`
  - Numbers formatted to 2 decimal places
  - Missing values shown as muted "-"
  - Symbol names shown in bold

### Data Handling
- **Display Order**: Reverse chronological by default (most recent first)
- **Limit**: Shows 20 most recent 15-minute candles per symbol
- **Filter Scope**: Filters across all symbols
- **Sorting**: Applies to all columns with `data-key` attributes

## Database Requirements

The feature requires:
1. **PostgreSQL** database with `ohlcv_data` table
2. **Columns** in `ohlcv_data`:
   - `symbol` (TEXT)
   - `timeframe` (TEXT, value: '15m')
   - `timestamp` (TIMESTAMP)
   - `ema_20` (NUMERIC, nullable)
   - `ema_50` (NUMERIC, nullable)
   - `ema_100` (NUMERIC, nullable)
   - `ema_200` (NUMERIC, nullable)
   - `close` (NUMERIC, nullable)

If database is unavailable, endpoint returns: `{"data": [], "error": "Database not available"}`

## No External Dependencies

- ✅ No new UI libraries imported
- ✅ No external links or navigation added
- ✅ Uses existing table component structure
- ✅ Uses existing font, spacing, and styling
- ✅ Uses existing sorting utility (`attachSortingFor`)
- ✅ Uses existing color scheme and layout patterns
- ✅ No modifications to existing tables or sections

## Constraints Satisfied

- ✅ 15-minute timeframe specified
- ✅ 5 columns: Timestamp, EMA 20, EMA 50, EMA 100, EMA 200, LTP
- ✅ Reuses exact table styling and layout
- ✅ No external components
- ✅ No new design systems
- ✅ Isolated to new section (no existing modifications)
- ✅ No routing/links/navigation added
- ✅ Minimal, focused implementation

## Testing

To test the feature:

1. **Ensure Database is Populated**:
   ```bash
   python3 Core_files/download_daily_2y_to_db.py
   # or populate with 15-minute candle data
   ```

2. **Start the webapp**:
   ```bash
   python3 -m flask --app Webapp/app.py run --host 0.0.0.0 --port 5000
   ```

3. **Access the dashboard**:
   - Navigate to `http://localhost:5000`
   - Click the "EMA History (15m)" tab
   - Observe table populated with 15-minute candle data

4. **Test features**:
   - Click column headers to sort
   - Type symbol name in filter box
   - Click Refresh button
   - Leave tab open to see auto-refresh (every 10s)

## API Endpoint Details

### Request
```
GET /api/ema-history
```

### Response (Success - 200)
```json
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
    {
      "timestamp": "2026-02-02 14:15:00",
      "symbol": "INFY",
      "ema_20": 1233.99,
      "ema_50": 1233.75,
      "ema_100": 1233.45,
      "ema_200": 1231.98,
      "ltp": 1234.50
    }
  ]
}
```

### Response (Error - 500)
```json
{
  "error": "Database not available"
}
```

## Performance Considerations

- **Database Query**: Efficient with LIMIT 20 per symbol
- **Caching**: Frontend caches results in `latestEmaHistoryRows`
- **Polling**: 10-second interval (only when tab active)
- **Sorting**: Client-side using existing `applySort()` utility
- **Filtering**: Client-side substring matching

## Files Modified

1. ✅ `/Users/shadab/Work and Codiing/pure-alpha/Webapp/templates/index.html`
   - Added tab button
   - Added view card with table
   - Added JavaScript logic and event handlers

2. ✅ `/Users/shadab/Work and Codiing/pure-alpha/Webapp/app.py`
   - Added `/api/ema-history` Flask route

3. ✅ `/Users/shadab/Work and Codiing/pure-alpha/Webapp/ltp_service.py`
   - Added `get_ema_history()` function

## Future Enhancements (Not Implemented)

- Symbol-specific detailed view
- Download as CSV
- Custom timeframe selector
- EMA comparison across timeframes
- Alerts when EMA crosses occur
- Historical statistics (average EMA values)
