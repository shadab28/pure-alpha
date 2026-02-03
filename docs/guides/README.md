# Strategy Guides

Complete guides for understanding and implementing the trading strategy.

## Contents

### 1. **STRATEGY_EXECUTION_COMPLETE_GUIDE.md**
The most comprehensive documentation file covering:
- Complete system architecture and components
- 9-phase startup sequence explained in detail
- 20+ API endpoints with request/response examples
- Strategy execution cycle (60-second scan intervals)
- Database schema and persistence model
- WebSocket implementation approach
- 90+ code examples and snippets

**Use this when**: Understanding the complete system, implementing a new feature, or troubleshooting the strategy engine.

### 2. **STRATEGY_SUMMARY.md**
Executive summary of the trading strategy:
- High-level strategy overview
- Key entry and exit rules
- Position management logic
- Risk parameters and capital allocation

**Use this when**: Quick reference or explaining strategy to others.

### 3. **STRATEGY_SUMMARY.txt**
Text version of strategy summary for easy reference.

## Quick Links

- 📊 [Complete Strategy Guide](./STRATEGY_EXECUTION_COMPLETE_GUIDE.md)
- 🔄 [Startup & Execution Flow](./STRATEGY_EXECUTION_COMPLETE_GUIDE.md#startup-sequence)
- 🌐 [API Endpoints Reference](./STRATEGY_EXECUTION_COMPLETE_GUIDE.md#api-endpoints)
- 💾 [Database Schema](./STRATEGY_EXECUTION_COMPLETE_GUIDE.md#database)
- ⚙️ [Configuration Reference](./STRATEGY_EXECUTION_COMPLETE_GUIDE.md#configuration)

## Key Concepts

### 3-Position Ladder
Progressive position entry with conditional rules:
- **P1**: Initial entry when Rank_GM > 2.5
- **P2**: Add position when P1 PnL > 0.25%
- **P3**: Add position when avg(P1, P2) PnL > 1%

### Trailing Stops
Dynamic stop-loss management:
- SL only moves up (profit protection)
- Based on highest price achieved since entry
- Implemented via GTT (Good Till Triggered) orders
- Prevents manual monitoring

### Capital Management
- Total Capital: ₹240,000
- Per Position: ₹3,000
- Max Positions: 90
- Scan Interval: 60 seconds

## For Developers

Start with **STRATEGY_EXECUTION_COMPLETE_GUIDE.md** for the complete implementation details including:
- How to modify entry/exit rules
- How to add new indicators
- How to integrate with different brokers
- Performance optimization tips

## Related Documentation

- 🌐 [WebSocket Implementation](../websocket/WEBSOCKET_ORDER_IMPLEMENTATION.md)
- 📱 [Dashboard Reference](../ui/DASHBOARD_TABS_REFERENCE.md)
- ⚙️ [Architecture Documentation](../architecture/)
- ✨ [Features Documentation](../features/)
