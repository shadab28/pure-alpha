# EMA History (15m) Feature - Quick Start Guide

## What Was Added

A new **EMA History (15-Minute)** UI tab to the LTP Dashboard that displays historical 15-minute candlestick data with exponential moving averages (EMA 20, 50, 100, 200) and the last traded price (LTP).

## Features

✅ **Real-time Data Fetching**
- Automatically fetches latest 15-minute EMA data from PostgreSQL
- Auto-refreshes every 10 seconds when tab is active
- Manual refresh button for immediate updates

✅ **Interactive Table**
- Click column headers to sort (ascending/descending)
- Filter by symbol name (case-insensitive)
- Displays all data with 2 decimal place precision
- Shows "-" for missing values

✅ **Responsive UI**
- Matches existing webapp styling perfectly
- No external dependencies
- Same colors, fonts, spacing as other tables
- Follows existing UI patterns

## How to Use

### 1. Navigate to EMA History Tab

```
Click: [EMA History (15m)] tab
        ↓
View displays with empty table or data (if database has records)
```

### 2. Sort Data

```
Click any column header (Timestamp, Symbol, EMA 20, etc.)
        ↓
First click:  Sort ascending (A→Z, oldest→newest)
Second click: Sort descending (Z→A, newest→oldest)
Third click:  Reset to default
```

### 3. Filter by Symbol

```
Type in "Filter by symbol..." box
        ↓
Table updates in real-time
        ↓
Examples:
  "INFY"  → Shows only INFY rows
  "in"    → Shows INFY (case-insensitive)
  "tc"    → Shows TCS
  Clear   → Shows all rows
```

### 4. Refresh Data

```
Click [Refresh] button
        ↓
Fetches latest data from database
        ↓
Table updates with new records
```

### 5. Auto-Refresh

```
While on EMA History tab:
  ✓ Data updates automatically every 10 seconds
  ✓ No action needed
  
When you switch to another tab:
  ✗ Auto-refresh stops (saves bandwidth)
  
When you return to EMA History tab:
  ✓ Auto-refresh resumes
```

## Data Interpretation

### Columns

| Column | Description | Format |
|--------|-------------|--------|
| **Timestamp** | Time of the 15-minute candle open | YYYY-MM-DD HH:MM:SS |
| **Symbol** | Stock ticker symbol | Text (e.g., INFY) |
| **EMA 20** | 20-period exponential moving average | Price (2 decimals) |
| **EMA 50** | 50-period exponential moving average | Price (2 decimals) |
| **EMA 100** | 100-period exponential moving average | Price (2 decimals) |
| **EMA 200** | 200-period exponential moving average | Price (2 decimals) |
| **LTP** | Last Traded Price (candle close) | Price (2 decimals) |

### Example Data

```
Timestamp           Symbol  EMA 20   EMA 50   EMA 100  EMA 200  LTP
2026-02-02 14:45    INFY    1234.56  1234.00  1233.50  1232.00  1235.50
2026-02-02 14:30    INFY    1233.99  1233.75  1233.45  1231.98  1234.50
2026-02-02 14:45    TCS     4123.45  4122.00  4120.50  4119.00  4124.50
```

### Reading EMAs

**EMA Trend Analysis:**
- When EMA 20 > EMA 50 > EMA 100 > EMA 200 → Uptrend
- When EMA 20 < EMA 50 < EMA 100 < EMA 200 → Downtrend
- Price above all EMAs → Strong bullish
- Price below all EMAs → Strong bearish

**Price vs EMA:**
- LTP > EMA 20 → Above short-term moving average (bullish)
- LTP < EMA 200 → Below long-term moving average (bearish)

## System Requirements

### For Full Functionality

1. **PostgreSQL Database**
   - Must have `ohlcv_data` table
   - Must have columns: symbol, timeframe, timestamp, ema_20, ema_50, ema_100, ema_200, close

2. **Data Population**
   ```bash
   # Populate historical data (optional)
   python3 Core_files/download_daily_2y_to_db.py
   ```

3. **Flask Running**
   ```bash
   python3 -m flask --app Webapp/app.py run --host 0.0.0.0 --port 5000
   ```

### If Database Not Available

Feature will display:
```
No EMA history data available
```

This is normal and means either:
- Database connection is not configured
- Database doesn't have the ohlcv_data table
- Table is empty (no records yet)

## Troubleshooting

### "No EMA history data available"

**Possible Causes:**
1. Database not connected
2. Database has no ohlcv_data table
3. Table is empty
4. Timeframe is not '15m' in database

**Solution:**
```bash
# Check database connection
psql -U your_user -d your_db -c "SELECT COUNT(*) FROM ohlcv_data WHERE timeframe='15m';"

# If count is 0, populate data
python3 Core_files/download_daily_2y_to_db.py

# Then refresh the webapp and try again
```

### Table Shows Data But No EMAs

**Cause:** Database records might not have EMA values computed

**Solution:**
1. Run EMA calculation script
2. Ensure database has EMA columns with values

### Filter Not Working

**Solution:**
- Check if symbol name matches exactly (case-insensitive)
- Try partial match (e.g., "INF" for "INFY")
- Clear filter by deleting text

### Sorting Not Working

**Solution:**
- Reload page (F5)
- Make sure column header is clickable (should change color on hover)
- Click column header again

## Files Modified

Three files were updated to add this feature:

1. **`Webapp/templates/index.html`**
   - Added tab button for "EMA History (15m)"
   - Added card view with table
   - Added JavaScript logic for fetching, sorting, filtering

2. **`Webapp/app.py`**
   - Added `/api/ema-history` endpoint
   - Calls ltp_service function

3. **`Webapp/ltp_service.py`**
   - Added `get_ema_history()` function
   - Queries PostgreSQL for 15-minute OHLCV data with EMAs

## API Endpoint Reference

### Endpoint
```
GET /api/ema-history
```

### Response Format (Success)
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

### Response Format (Error)
```json
{
  "error": "Database not available"
}
```

### Example Usage
```javascript
// Fetch EMA history
const response = await fetch('/api/ema-history');
const data = await response.json();

if (data.data) {
  console.log(`Fetched ${data.data.length} candles`);
}
```

## Performance

- **Query Limit**: 20 candles per symbol (most recent)
- **Refresh Rate**: 10 seconds (when tab active)
- **Data Transfer**: ~5-10 KB per request (typical)
- **Processing**: Client-side sorting and filtering (instant)

## Browser Compatibility

✅ Chrome/Chromium
✅ Firefox
✅ Safari
✅ Edge
✅ Mobile browsers (iOS Safari, Chrome Mobile)

Requires ES6+ JavaScript support

## Keyboard Shortcuts

```
Filter Input:
  Type:  Filter symbols in real-time
  Clear: Delete to remove filter

Column Headers:
  Click: Sort by that column
  Click again: Reverse sort direction

Refresh Button:
  Click: Fetch latest data

Tab Navigation:
  Click: Switch to EMA History view
```

## Tips & Tricks

### 1. Monitor Trend Changes
Watch EMA alignment to identify trend changes:
```
✓ EMA 20 crosses above EMA 50 → Potential buy signal
✓ EMA 50 crosses above EMA 200 → Golden cross (bullish)
✗ EMA 20 crosses below EMA 50 → Potential sell signal
```

### 2. Quick Symbol Lookup
```
Type first 2 letters of symbol:
  "IN" → Filter to INFY
  "TC" → Filter to TCS
  "WI" → Filter to WIPRO
```

### 3. Find Latest Data
```
Click "Timestamp" header to sort
→ Most recent candle at top
→ Newest 15-minute data first
```

### 4. Track Multiple Symbols
```
No filter → See all symbols at once
With filter → Focus on single symbol
Combine with sorting → Find patterns quickly
```

## Support & Questions

If you encounter issues:

1. **Check Database**: Ensure ohlcv_data table exists and has data
2. **Check Logs**: Look at Flask server console for errors
3. **Check Connection**: Verify PostgreSQL is running and accessible
4. **Try Refresh**: Click [Refresh] button to retry
5. **Reload Page**: Press F5 to reload entire webapp

## Roadmap

**Potential Future Enhancements:**
- CSV export for selected rows
- Custom timeframe selector
- EMA comparison across symbols
- Historical EMA statistics
- Alerts on EMA crossovers
- Auto-generated trading signals
