# 🎨 Terminal Color Output Reference

## Startup Sequence with Colors

When you run the Pure Alpha Trading Webapp, here's what you'll see with the new color enhancements:

```
═══════════════════════════════════════════════════════════════════════════════

2026-01-14 00:26:54 [MOMENTUM] INFO: Strategy routes registered with Flask app
2026-01-14 00:26:54 [MOMENTUM] INFO: Momentum strategy routes registered
2026-01-14 00:26:54 [INFO] ════════════════════════════════════════════════════════════════════════════════════
2026-01-14 00:26:54 [INFO] Pure Alpha Trading Webapp - Starting...
2026-01-14 00:26:54 [INFO] ════════════════════════════════════════════════════════════════════════════════════
2026-01-14 00:26:54 [INFO] Using universe: stocks2026

   🟢 2026-01-14 00:26:54 [INFO] Loaded 62 symbols from stocks2026
      └─ Shows symbol universe has been loaded into memory
      └─ Color: BOLD GREEN
      └─ Action: Ready to map tokens

   🟢 2026-01-14 00:26:54 [INFO] Kite API initialized
      └─ Confirms connection to Zerodha KiteConnect API
      └─ Color: BOLD GREEN
      └─ Action: Ready for trading

   🟢 2026-01-14 00:26:54 [INFO] Resolved 62 tokens for subscription
      └─ All symbols mapped to WebSocket instrument tokens
      └─ Color: BOLD GREEN
      └─ Action: Ready for real-time data subscription

2026-01-14 00:26:54 [INFO] ohlcv_data table ensured.

   🟢 2026-01-14 00:26:54 [INFO] Flask server started on http://127.0.0.1:5050
      └─ **CRITICAL**: This is where you access the dashboard
      └─ Color: BOLD GREEN
      └─ Action: Open browser and visit this URL

   🟢 2026-01-14 00:26:54 [INFO] Scanner worker started (interval=300s)
      └─ Momentum/Reversal scanners refresh every 5 minutes
      └─ Color: BOLD GREEN
      └─ Action: Scanner data available via API

   🟢 2026-01-14 00:26:54 [INFO] KiteTicker connected in threaded mode
      └─ Real-time data handler is active
      └─ Color: BOLD GREEN
      └─ Action: Ready to receive market ticks

 * Serving Flask app 'app'
 * Debug mode: off

   🟢 2026-01-14 00:26:54 [INFO] Ticker connected. Subscribing to 62 tokens in ltp mode...
      └─ WebSocket connected to Zerodha's real-time feed
      └─ Color: BOLD GREEN
      └─ Action: Receiving LTP updates for all symbols

2026-01-14 00:26:59 [INFO] Status: 62 symbols tracked, 62 in aggregation
   └─ Regular status update every minute
   └─ Color: DEFAULT (no color)
   └─ Action: Monitoring continues...

═══════════════════════════════════════════════════════════════════════════════
```

## Color Key

### Green (✅)
- Used for: Successful operations, ready states
- ANSI Code: `\033[92m` (Bright Green)
- Combined with: `\033[1m` (Bold) for emphasis
- Meaning: "System is ready and working"

**Examples**:
- ✅ Symbols loaded
- ✅ API initialized
- ✅ Tokens resolved
- ✅ Server started
- ✅ Connection established

### Red (🔴)
- Used for: Critical warnings, emergency states
- ANSI Code: `\033[91m` (Bright Red)
- Combined with: `\033[1m` (Bold) for maximum visibility
- Meaning: "DANGER - Pay attention immediately"

**Example**:
- 🔴 LIVE MODE ACTIVATED - Real orders will be placed!

## What Each Message Means

### 1️⃣ "Loaded 62 symbols from stocks2026"
**What happened**: The system loaded the list of 62 stocks to track
**Why it's important**: Confirms the symbol universe is correct
**Next step**: These will be mapped to token IDs

### 2️⃣ "Kite API initialized"
**What happened**: Successfully connected to Zerodha's trading API
**Why it's important**: Without this, no orders can be placed
**Next step**: Orders and GTT placement will now work

### 3️⃣ "Resolved 62 tokens for subscription"
**What happened**: Each symbol has been mapped to its instrument token
**Why it's important**: These tokens are needed for real-time data
**Next step**: The system will subscribe to these tokens on the WebSocket

### 4️⃣ "Flask server started on http://127.0.0.1:5050"
**What happened**: Web dashboard is now accessible
**Why it's important**: This is where you monitor trades and settings
**Next step**: Open this URL in your browser to see the dashboard
**Security**: Only accessible from your local machine (127.0.0.1)

### 5️⃣ "Scanner worker started (interval=300s)"
**What happened**: Momentum and reversal scanners are running
**Why it's important**: These find trading opportunities
**Next step**: Scanner data will refresh every 5 minutes

### 6️⃣ "KiteTicker connected in threaded mode"
**What happened**: Real-time data handler is active
**Why it's important**: Market ticks will be processed in real-time
**Next step**: Live price updates begin flowing in

### 7️⃣ "Ticker connected. Subscribing to 62 tokens in ltp mode..."
**What happened**: WebSocket connection established to Zerodha
**Why it's important**: You're now receiving live market prices
**Next step**: Dashboard will show real-time LTP updates

## How to Read Terminal Output

### ✅ Healthy Startup
If you see **all green messages** in this sequence, the system is ready:
1. Symbols loaded (green)
2. API initialized (green)
3. Tokens resolved (green)
4. Flask server started (green)
5. Scanner started (green)
6. KiteTicker connected (green)
7. Ticker connected (green)
✅ **Status**: Ready to trade

### ⚠️ Partial Startup
If a message is **missing or red**:
- Check the error message below it
- Review logs in `logs/` directory
- Restart the application

### 🔴 Critical Alert
If you see **"LIVE MODE ACTIVATED"** in bold red:
- Real orders will be placed
- Verify you're ready for live trading
- Check Rank_GM threshold is configured correctly

## Color Compatibility

✅ **macOS Terminal**: Full support
✅ **macOS iTerm2**: Full support
✅ **Linux bash/zsh**: Full support
✅ **Windows PowerShell**: Full support
✅ **Windows Terminal**: Full support
✅ **VS Code Terminal**: Full support

## Color Codes Reference

```python
# ANSI Escape Sequences Used
BOLD = '\033[1m'        # Make text bold
GREEN = '\033[92m'      # Bright green text
RED = '\033[91m'        # Bright red text
RESET = '\033[0m'       # Reset all formatting

# Combined Example
bold_green = f"{BOLD}{GREEN}text{RESET}"
```

## Customization

If you want to customize the colors, edit the `Colors` class in `Webapp/main.py`:

```python
class Colors:
    # Edit these values to change colors
    GREEN = '\033[92m'      # Try '\033[32m' for darker green
    RED = '\033[91m'        # Try '\033[31m' for darker red
    BOLD = '\033[1m'        # Keep this for emphasis
    RESET = '\033[0m'       # Always keep this
```

**Note**: Standard color codes are 30-37, bright versions are 90-97.

---

**Last Updated**: 2026-01-14
**Version**: 1.0
**Status**: ✅ All color enhancements active and verified
