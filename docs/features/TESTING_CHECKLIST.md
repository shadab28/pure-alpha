# Testing Checklist - EMA History Feature

## Pre-Testing Setup

### Step 1: Verify Database Setup
```bash
# Connect to PostgreSQL
psql -U your_user -d your_database

# Check if ohlcv_data table exists
\dt ohlcv_data

# Check table structure
\d ohlcv_data

# Verify 15-minute data exists
SELECT COUNT(*) FROM ohlcv_data WHERE timeframe='15m';
SELECT DISTINCT symbol FROM ohlcv_data WHERE timeframe='15m' LIMIT 5;

# Check for EMA columns with data
SELECT symbol, COUNT(*) 
FROM ohlcv_data 
WHERE timeframe='15m' AND ema_20 IS NOT NULL 
GROUP BY symbol;
```

### Step 2: Start Flask Server
```bash
cd /Users/shadab/Work\ and\ Codiing/pure-alpha
python3 -m flask --app Webapp/app.py run --host 0.0.0.0 --port 5000

# Or use the existing task:
# Run Full Webapp (Ticker + Flask + Scanners)
```

### Step 3: Open Browser
```
Navigate to: http://localhost:5000
```

---

## UI Tests

### Test 1: Tab Button Visibility
- [ ] Tab button "EMA History (15m)" appears in tab navigation
- [ ] Button is between "VCP" and "Orders" tabs
- [ ] Button has correct styling (secondary button style)
- [ ] Button text is correct: "EMA History (15m)"

### Test 2: Tab Click Navigation
- [ ] Clicking tab shows EMA History view
- [ ] View is visible and not hidden
- [ ] Tab is highlighted as "active" (loses "secondary" class)
- [ ] Other tabs become inactive
- [ ] Other views are hidden

### Test 3: Card Layout
- [ ] Card has blue header background
- [ ] Card header text: "EMA History (15-Minute Candles)"
- [ ] Filter input box visible (with placeholder "Filter by symbol...")
- [ ] Refresh button visible and clickable
- [ ] Table visible with headers and body

### Test 4: Table Headers
Verify all 7 columns are present and visible:
- [ ] Timestamp
- [ ] Symbol
- [ ] EMA 20
- [ ] EMA 50
- [ ] EMA 100
- [ ] EMA 200
- [ ] Last Traded Price

### Test 5: Table Styling
- [ ] Headers have blue background (#eaf2ff)
- [ ] Headers have dark text
- [ ] Table has borders
- [ ] Alternating row colors (white and light blue)
- [ ] Rows highlight on hover
- [ ] Numbers are bold in numeric columns

---

## Data Loading Tests

### Test 6: Initial Data Load
- [ ] Table populates with data after clicking tab
- [ ] Shows message "No EMA history data available" if no data
- [ ] Data loads within 2 seconds
- [ ] No console errors in browser DevTools

### Test 7: Data Format
Check if data is displayed correctly:
- [ ] Timestamps show as "YYYY-MM-DD HH:MM:SS" format
- [ ] Symbol names display (e.g., "INFY", "TCS")
- [ ] EMA values show 2 decimal places (e.g., "1234.56")
- [ ] LTP values show 2 decimal places
- [ ] Null values show as "-" (muted gray)

### Test 8: Data Accuracy (if database has data)
- [ ] Timestamps are in correct order
- [ ] Symbols match database entries
- [ ] EMA values are reasonable (within expected range)
- [ ] LTP matches latest close price

---

## Sorting Tests

### Test 9: Sort by Timestamp (Default)
- [ ] Default sort shows newest timestamps first (DESC)
- [ ] Click Timestamp header → sorts oldest first (ASC)
- [ ] Click again → sorts newest first (DESC)
- [ ] Numbers change order visibly

### Test 10: Sort by Symbol
- [ ] Click Symbol header → sorts A-Z
- [ ] Click again → sorts Z-A
- [ ] Multiple symbols arrange alphabetically
- [ ] All data stays properly aligned

### Test 11: Sort by EMA Columns
For each EMA column (20, 50, 100, 200):
- [ ] Click header → sorts ascending (lowest first)
- [ ] Click again → sorts descending (highest first)
- [ ] Numeric ordering is correct
- [ ] Null values appear at end

### Test 12: Sort by LTP
- [ ] Click header → sorts ascending
- [ ] Click again → sorts descending
- [ ] Numeric values order correctly
- [ ] Works with mixed data and nulls

---

## Filtering Tests

### Test 13: Filter by Symbol
- [ ] Type "INFY" → shows only INFY rows
- [ ] Type "infy" → shows INFY (case-insensitive)
- [ ] Type "IN" → shows INFY (partial match)
- [ ] Type "xyz" → shows no results with message
- [ ] Clear filter → all rows return

### Test 14: Real-time Filtering
- [ ] Typing updates table instantly
- [ ] No lag when typing each character
- [ ] Delete character → updates instantly
- [ ] Row count changes as you type

### Test 15: Multiple Symbols Filter
- [ ] Type "T" → might show TCS, TSM, etc.
- [ ] Filter is case-insensitive
- [ ] Substring matching works
- [ ] Correct rows highlighted

---

## Refresh Tests

### Test 16: Manual Refresh
- [ ] Click "Refresh" button
- [ ] Button shows "Refreshing..." state (if implemented)
- [ ] Data updates within 2 seconds
- [ ] Same table structure maintained
- [ ] No page reload occurs

### Test 17: Auto-Refresh While Active
- [ ] Wait on EMA History tab for 10+ seconds
- [ ] Data updates automatically
- [ ] Row order may change (if newest first)
- [ ] Values update to latest

### Test 18: Auto-Refresh Stops on Tab Switch
- [ ] Click EMA History tab → auto-refresh starts
- [ ] Switch to different tab → auto-refresh stops
- [ ] Click EMA History tab again → auto-refresh resumes
- [ ] No excess data fetches when hidden

### Test 19: Refresh After Filter
- [ ] Apply filter (e.g., "INFY")
- [ ] Click Refresh button
- [ ] Filter is maintained
- [ ] Table updates with refreshed data
- [ ] Still shows only INFY rows

---

## Error Handling Tests

### Test 20: Database Unavailable
Simulate by stopping PostgreSQL:
```bash
# Stop PostgreSQL
sudo systemctl stop postgresql

# Refresh page
# Check tab
```
Expected: "No EMA history data available" or error message

### Test 21: API Error Handling
- [ ] Shows error message if API fails
- [ ] Message is readable
- [ ] Can retry with Refresh button
- [ ] No white screen of death

### Test 22: Network Error
Disable network and try:
- [ ] Shows error message
- [ ] Error is descriptive
- [ ] Can retry when network restored

### Test 23: Empty Result Set
If database has no 15-minute data:
- [ ] Shows "No EMA history data available"
- [ ] Message is clear and centered
- [ ] No console errors
- [ ] Refresh button still works

---

## Integration Tests

### Test 24: Tab Navigation Consistency
- [ ] All tabs work (Main, Filtered, CK, VCP, EMA History, Orders, Active, Trades)
- [ ] Only one tab active at a time
- [ ] EMA History tab properly toggles
- [ ] No visual conflicts with other tabs

### Test 25: Style Consistency
- [ ] Matches other table styles (CK, VCP, Orders)
- [ ] Same color scheme used
- [ ] Same fonts and sizes
- [ ] Same button styles

### Test 26: Data Display Consistency
- [ ] Null handling matches other tables
- [ ] Number formatting matches
- [ ] Timestamp format is consistent
- [ ] Sorting behavior same as other tables

### Test 27: Browser Storage/Cache
- [ ] Filter input clears on page refresh
- [ ] Sort state resets to default
- [ ] Data reloads from API
- [ ] No stale data shown

---

## Performance Tests

### Test 28: Load Time
- [ ] Click EMA History tab
- [ ] Data appears within 2 seconds
- [ ] No page lag
- [ ] Smooth scrolling if table is large

### Test 29: Rendering Performance
- [ ] 100 rows render smoothly
- [ ] No page stutter
- [ ] Sorting is instant
- [ ] Filtering is instant

### Test 30: Memory Usage
- [ ] Browser tab doesn't consume excessive RAM
- [ ] No memory leaks over time
- [ ] Closing tab frees memory
- [ ] Can leave tab open without issues

### Test 31: Network Efficiency
- [ ] API response is reasonable size
- [ ] No unnecessary re-fetches
- [ ] Auto-refresh only when needed
- [ ] Refresh button doesn't spam requests

---

## Browser Compatibility Tests

### Test 32: Chrome/Chromium
- [ ] All features work
- [ ] Layout correct
- [ ] No console errors
- [ ] Sorting works

### Test 33: Firefox
- [ ] All features work
- [ ] Layout correct
- [ ] No console errors
- [ ] Filtering works

### Test 34: Safari
- [ ] All features work
- [ ] Layout correct
- [ ] Numbers format correctly

### Test 35: Edge
- [ ] All features work
- [ ] No rendering issues

### Test 36: Mobile Chrome
- [ ] Tab visible on mobile
- [ ] Can scroll table horizontally
- [ ] Filter input works on touch
- [ ] Buttons clickable with touch

---

## Edge Cases

### Test 37: Very Long Symbol Names
If data has long symbols:
- [ ] Table doesn't break layout
- [ ] Text wraps or truncates gracefully
- [ ] Still sortable

### Test 38: Very Large Numbers
If data has large EMA values:
- [ ] Numbers display correctly
- [ ] Doesn't overflow column
- [ ] Sorting works with large numbers

### Test 39: Very Small Numbers
If data has small decimal EMA values:
- [ ] Shows 2 decimal places
- [ ] Numbers are readable
- [ ] Sorting works

### Test 40: Missing EMA Values
If some rows lack EMA data:
- [ ] Shows "-" (muted) instead of error
- [ ] Row is still usable
- [ ] Sorting handles nulls gracefully
- [ ] Filter still works

### Test 41: Special Characters in Filter
- [ ] Filter handles spaces
- [ ] Filter handles numbers
- [ ] Filter handles symbols
- [ ] Doesn't crash with special chars

### Test 42: Rapid Tab Switching
- [ ] Clicking tabs rapidly doesn't break
- [ ] Data loads correctly
- [ ] No fetch conflicts
- [ ] UI stays responsive

---

## Accessibility Tests

### Test 43: Keyboard Navigation
- [ ] Tab key moves through elements
- [ ] Can focus filter input
- [ ] Can click buttons with keyboard
- [ ] Sort headers focusable

### Test 44: Color Contrast
- [ ] Headers readable
- [ ] Table text readable
- [ ] Muted text visible but distinct

### Test 45: Screen Reader
- [ ] Column headers announced
- [ ] Table data readable
- [ ] Buttons labeled clearly
- [ ] Filter input labeled

---

## API Tests (Backend)

### Test 46: API Endpoint Exists
```bash
curl http://localhost:5000/api/ema-history
```
- [ ] Returns 200 status
- [ ] Returns JSON response
- [ ] Has "data" key
- [ ] Data is array

### Test 47: API Response Format
```json
{
  "data": [
    {
      "timestamp": "...",
      "symbol": "...",
      "ema_20": 1234.56,
      "ema_50": null,
      "ema_100": 1234.00,
      "ema_200": 1233.50,
      "ltp": 1235.00
    }
  ]
}
```
- [ ] All required keys present
- [ ] Correct data types
- [ ] Timestamps are strings
- [ ] Numeric values are numbers or null

### Test 48: API Error Response
If API fails:
```json
{
  "error": "Database not available"
}
```
- [ ] Returns 500 status
- [ ] Has "error" key
- [ ] Error message is descriptive

### Test 49: API Performance
- [ ] Response time < 2 seconds
- [ ] Works with 100+ candles
- [ ] Works with 50+ symbols
- [ ] Doesn't timeout

---

## Final Verification Checklist

- [ ] All 49 tests passed
- [ ] No console errors
- [ ] No network errors
- [ ] Data accurate
- [ ] UI responsive
- [ ] Performance acceptable
- [ ] Feature is production-ready

---

## Bug Report Template

If issues found during testing:

```
Title: [Brief description]

Steps to Reproduce:
1. [Step 1]
2. [Step 2]
3. [Step 3]

Expected Behavior:
[What should happen]

Actual Behavior:
[What actually happens]

Environment:
- Browser: [Chrome/Firefox/Safari/Edge]
- OS: [Windows/Mac/Linux]
- Database: [PostgreSQL version]
- Python: [Version]
- Data: [Sample data if available]

Screenshots/Console Errors:
[Paste error messages or attach screenshots]
```

---

## Sign-Off

- [ ] All tests completed
- [ ] No blockers found
- [ ] Feature ready for production
- [ ] Documentation complete
- [ ] Code changes approved

**Tested By**: ________________________
**Date**: ________________________
**Notes**: ________________________
