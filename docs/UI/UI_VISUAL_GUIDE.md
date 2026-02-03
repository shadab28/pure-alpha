# EMA History (15m) - UI Visual Guide

## Tab Navigation

```
┌─────────────────────────────────────────────────────────────────┐
│  LTP Dashboard                                    [Download CSV] [Refresh Now] │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ [All] [Filtered] [CK] [VCP] [EMA History (15m)] [Orders] [Active Orderbook] [Trade Journal] │
└─────────────────────────────────────────────────────────────────┘
```

## EMA History View Layout

```
┌────────────────────────────────────────────────────────────────────────────┐
│ EMA History (15-Minute Candles)     [Filter by symbol...] [Refresh]      │
├────────────────────────────────────────────────────────────────────────────┤
│  Timestamp          │ Symbol │ EMA 20  │ EMA 50  │ EMA 100 │ EMA 200 │ LTP │
├────────────────────────────────────────────────────────────────────────────┤
│  2026-02-02 14:45   │ INFY   │ 1234.56 │ 1234.00 │ 1233.50 │ 1232.00 │ 1235.50 │
│  2026-02-02 14:30   │ INFY   │ 1233.99 │ 1233.75 │ 1233.45 │ 1231.98 │ 1234.50 │
│  2026-02-02 14:15   │ INFY   │ 1233.42 │ 1233.50 │ 1233.40 │ 1231.95 │ 1233.99 │
│  2026-02-02 14:00   │ INFY   │ 1232.85 │ 1233.25 │ 1233.35 │ 1231.92 │ 1233.42 │
│  2026-02-02 13:45   │ INFY   │ 1232.28 │ 1233.00 │ 1233.30 │ 1231.90 │ 1232.85 │
│  2026-02-02 14:45   │ TCS    │ 4123.45 │ 4122.00 │ 4120.50 │ 4119.00 │ 4124.50 │
│  2026-02-02 14:30   │ TCS    │ 4122.99 │ 4121.75 │ 4120.45 │ 4118.98 │ 4123.50 │
│  2026-02-02 14:15   │ TCS    │ 4122.42 │ 4121.50 │ 4120.40 │ 4118.95 │ 4122.99 │
│  2026-02-02 14:00   │ TCS    │ 4121.85 │ 4121.25 │ 4120.35 │ 4118.92 │ 4122.42 │
│  2026-02-02 13:45   │ TCS    │ 4121.28 │ 4121.00 │ 4120.30 │ 4118.90 │ 4121.85 │
└────────────────────────────────────────────────────────────────────────────┘
```

## Color Scheme & Styling

```
Colors Used:
- Header Background: var(--blue-100) = #eaf2ff
- Header Text: var(--blue-900) = #0a2540
- Table Border: var(--border) = #d9e2f1
- Row Even Background: var(--blue-050) = #f5f8ff
- Row Odd Background: var(--white) = #ffffff
- Row Hover: #eef4ff
- Text: var(--text) = #0f172a
- Muted Text: var(--muted) = #5b6b86
- Numbers (bold): font-weight: 600

Font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif

Spacing:
- Card Padding: 12px 16px
- Cell Padding: 8px
- Table Font Size: 14px
- Row Height: ~32px (standard)

Borders:
- Card Border: 1px solid #d9e2f1
- Table Cell Borders: 1px solid #d9e2f1
- Border Radius: 10px (card)
```

## Data Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│                         User Interaction                              │
│  1. Click "EMA History (15m)" Tab                                    │
│  2. Click Column Headers to Sort                                     │
│  3. Type Symbol in Filter Box                                        │
│  4. Click "Refresh" Button                                           │
└────────────────────┬─────────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Frontend JavaScript                               │
│  - fetchEmaHistory()                                                 │
│  - renderEmaHistoryTable()                                           │
│  - Filter & Sort Logic                                              │
│  - Auto-polling (10 seconds)                                        │
└────────────────────┬─────────────────────────────────────────────────┘
                     │
                     ▼ HTTP GET /api/ema-history
┌──────────────────────────────────────────────────────────────────────┐
│                     Flask API Route                                   │
│  @app.route('/api/ema-history')                                     │
│  → get_ema_history() from ltp_service                               │
└────────────────────┬─────────────────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  Backend Data Service                                │
│  get_ema_history() in ltp_service.py                                │
│  - Connect to PostgreSQL                                            │
│  - Query ohlcv_data table                                           │
│  - Fetch 20 recent 15m candles per symbol                          │
│  - Extract: timestamp, ema_20, ema_50, ema_100, ema_200, close    │
│  - Return JSON array                                                │
└────────────────────┬─────────────────────────────────────────────────┘
                     │
                     ▼ PostgreSQL ohlcv_data table
┌──────────────────────────────────────────────────────────────────────┐
│                       Database                                       │
│  ohlcv_data:                                                        │
│  - symbol: 'INFY', 'TCS', 'WIPRO', etc.                            │
│  - timeframe: '15m'                                                 │
│  - timestamp: 2026-02-02 14:45:00                                  │
│  - ema_20, ema_50, ema_100, ema_200: numeric values               │
│  - close: last traded price for candle                             │
└──────────────────────────────────────────────────────────────────────┘
```

## Sorting Behavior

```
Default Sort: Timestamp (DESC - most recent first)

User Actions:
┌─────────────────────────────────────────────┐
│ Click Timestamp Header                      │
├─────────────────────────────────────────────┤
│ First Click:   Sort ASC (oldest first)      │
│ Second Click:  Sort DESC (newest first)     │
│ Third Click:   Return to default DESC       │
└─────────────────────────────────────────────┘

Applies to all columns:
- Timestamp (date/time string)
- Symbol (alphabetical)
- EMA 20, 50, 100, 200 (numeric)
- LTP (numeric)
```

## Filtering Behavior

```
Filter Input: Case-Insensitive Substring Match

Examples:
┌────────────────────────────────────────────────────┐
│ User Input: "INF"                                  │
├────────────────────────────────────────────────────┤
│ Shows: INFY (includes "INF")                       │
│ Hides: TCS, WIPRO, etc.                           │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ User Input: "tc"                                   │
├────────────────────────────────────────────────────┤
│ Shows: TCS (includes "tc" - case insensitive)    │
│ Hides: INFY, WIPRO, etc.                          │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ User Input: "" (empty)                             │
├────────────────────────────────────────────────────┤
│ Shows: All rows (no filter applied)               │
└────────────────────────────────────────────────────┘
```

## Number Formatting

```
All numeric values display with 2 decimal places:

EMA Values:      1234.56
LTP Value:       1235.00
Null Values:     -  (muted gray)

Example Row:
┌──────────────────────────────────────────────────────┐
│ Timestamp     │ Symbol │ EMA 20  │ EMA 50  │ LTP     │
├──────────────────────────────────────────────────────┤
│ 2026-02-02... │ INFY   │ 1234.56 │ 1234.00 │ 1235.50 │
│ 2026-02-02... │ TCS    │ -       │ 4122.00 │ 4124.50 │
└──────────────────────────────────────────────────────┘
```

## Empty States

```
No Data Available:
┌────────────────────────────────────────────────────┐
│                  EMA History Table                  │
├────────────────────────────────────────────────────┤
│  No EMA history data available                     │
└────────────────────────────────────────────────────┘

Filter No Results:
┌────────────────────────────────────────────────────┐
│  Filter: "XYZ"                                     │
├────────────────────────────────────────────────────┤
│  No EMA history data available                     │
│  (Zero rows match "XYZ")                           │
└────────────────────────────────────────────────────┘

API Error:
┌────────────────────────────────────────────────────┐
│                  EMA History Table                  │
├────────────────────────────────────────────────────┤
│  Failed to fetch EMA history: Database error      │
└────────────────────────────────────────────────────┘
```

## Button States

```
Normal State:
  [Refresh] - secondary button style
  Filter Input: active, ready for typing

Disabled States:
  [Refresh] - disabled briefly after click during fetch
  
Active/Hover States:
  [Refresh] - brightness increase on hover (filter: brightness(1.05))
  Filter Input: outline or focus border
```

## Responsive Behavior

```
Desktop (Full Width):
  Filter Input: 150px
  Button Gap: 10px
  Full table visible

Narrow Screen (if resized):
  Table scrolls horizontally
  Filter input and button stay visible
  No breaking layout
```

## Auto-Refresh Indicator

```
When Tab is ACTIVE:
  ✓ Polling every 10 seconds
  ✓ Data updates silently
  ✓ User sees latest data

When Tab is INACTIVE:
  ✗ Polling stops
  ✗ Manual refresh button still works
  
User returns to tab:
  ✓ Polling resumes automatically
  ✓ Fetches fresh data on click
```
