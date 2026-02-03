# Dashboard UI Reference

Complete documentation of the web dashboard interface and user interaction flows.

## Contents

### **DASHBOARD_TABS_REFERENCE.md**
Comprehensive reference for all dashboard tabs:
- 11 main dashboard tabs with detailed documentation
- Each tab: Purpose, Data Sources, Display Columns, Calculations, User Workflows
- API calls required for each tab
- Live data refresh behavior
- User interaction patterns

### **UI_VISUAL_GUIDE.md**
Visual guide with:
- Dashboard layout and component structure
- Color schemes and typography
- Interactive elements and their behaviors
- Responsive design considerations
- Screenshot references and wireframes

**Use these when**: Building the dashboard, adding new features, or understanding user workflows.

## Dashboard Tabs Overview

### 1. **Portfolio Tab**
Current portfolio status and performance
- **Key Metrics**: Total invested, Deployed capital, Current value, Today's PnL
- **Data Refresh**: Real-time (5-second intervals)
- **User Actions**: View holdings, close positions, export data

### 2. **Positions Tab**
Open trading positions with entry/exit details
- **Columns**: Symbol, Entry Price, Qty, Average Price, Current Price, P&L, SL, Target, Status
- **Sorting**: By P&L, entry time, symbol
- **User Actions**: Close position, modify SL/target, set manual alert

### 3. **Orders Tab**
Order history and current order status
- **Columns**: Order ID, Symbol, Side (BUY/SELL), Quantity, Price, Status, Fill Price, Time
- **Filtering**: By symbol, status, date range
- **User Actions**: Cancel pending orders, view execution details

### 4. **GTT Orders Tab**
Good Till Triggered stop-loss orders
- **Columns**: GTT ID, Symbol, Trigger Price, Quantity, Status, Created Time, Triggered Time
- **Auto-Management**: Shows system-managed GTT orders for trailing stops
- **User Actions**: View trigger logic, modify trigger price (for manual GTTs)

### 5. **Trades Tab**
Historical trade journal with complete analysis
- **Columns**: Trade ID, Symbol, Entry Time, Exit Time, Entry Price, Exit Price, Quantity, P&L, P&L %, Trade Duration
- **Filtering**: By symbol, date range, P&L threshold
- **User Actions**: Filter by profit/loss, export trade data, view trade analysis

### 6. **Market Data Tab**
Real-time market data and technical indicators
- **Columns**: Symbol, LTP, Change %, Volume, Bid/Ask, 52W High/Low, EMA Status
- **Refresh Rate**: Real-time (tick-by-tick)
- **User Actions**: Add to watchlist, view chart, view indicator details

### 7. **Scanner Tab**
Rank_GM scanner and stock selection criteria
- **Columns**: Symbol, Rank_GM, Trend, 3 EMA Status, RSI, MACD, Entry Signal
- **Refresh Rate**: Every 60 seconds (scan cycle)
- **User Actions**: View stock details, add custom alerts, see historical rank_gm

### 8. **Strategy Control Tab**
Strategy execution and parameter management
- **Controls**: Start/Stop strategy, Switch mode (LIVE/PAPER/BACKTEST)
- **Parameters**: Max positions, per-position capital, scan interval, Rank_GM threshold
- **Display**: Strategy status, current cycle count, last scan time, active mode

### 9. **Performance Analytics Tab**
Historical performance metrics and statistics
- **Charts**: Daily P&L, Monthly P&L, Win Rate, Avg Trade Duration
- **Metrics**: Total Trades, Winning Trades, Losing Trades, Profit Factor, Sharpe Ratio
- **Analysis**: Performance by symbol, by strategy parameter combination
- **Export**: Download performance data, generate reports

### 10. **Alerts Tab**
System alerts and notifications
- **Types**: Price alerts, SL hit alerts, GTT triggered, API errors, Connection status
- **Management**: View/dismiss alerts, set alert rules, notification preferences
- **History**: Searchable alert history with timestamps

### 11. **Settings Tab**
User preferences and system configuration
- **Preferences**: Theme (light/dark), Refresh rates, Display precision
- **Notifications**: Email alerts, SMS alerts, In-app only
- **API Settings**: Broker API keys, WebSocket connection settings (when available)
- **Backup/Restore**: Export trading data, import previous settings

## UI Components

### Real-Time Updates
```
Component Updates:
├── Tick-by-Tick: LTP, Market Data, Portfolio Value
├── 5-Second Intervals: Positions PnL, Order Status
├── 60-Second Intervals: Scanner Results, Strategy Status
└── On-Demand: Trades Tab, Analytics, Alerts
```

### Data Refresh Architecture
```
┌─────────────────────────────────────┐
│      Browser Frontend (React/Vue)   │
└──────────────────┬──────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
    REST API              WebSocket
    (HTTPS)          (Real-time Push)
        │                     │
┌───────▼────────────────────▼────────┐
│     Flask App (Webapp/app.py)       │
└───────┬────────────────────┬────────┘
        │                    │
   ┌────▼─────────┐   ┌──────▼────────┐
   │ Strategy     │   │ LTP Service   │
   │ Engine       │   │ (Price Cache) │
   └────┬─────────┘   └──────┬────────┘
        │                    │
   ┌────▼────────────────────▼────────┐
│  KiteTicker / Market Data Feed     │
└────────────────────────────────────┘
```

## User Workflows

### Workflow 1: Monitor Open Positions
1. User opens "Positions" tab
2. System displays current open positions with real-time PnL
3. User sees SL and Target levels
4. PnL updates tick-by-tick (color-coded green/red)
5. Optional: User closes position or modifies SL

### Workflow 2: Review Trading Performance
1. User navigates to "Performance Analytics" tab
2. System displays historical performance charts
3. User selects date range or symbol filter
4. System calculates and displays metrics (Sharpe, Win Rate, etc.)
5. User exports data or generates report

### Workflow 3: Control Strategy Execution
1. User opens "Strategy Control" tab
2. Displays current strategy status (LIVE/PAPER/PAUSED)
3. User adjusts parameters if needed
4. User starts/stops strategy
5. System validates parameters and applies changes
6. Confirmation message shows new status

### Workflow 4: Set Alerts
1. User opens "Alerts" tab
2. Creates new alert (price level, indicator value, etc.)
3. System monitors condition
4. When triggered, shows notification (in-app, email, SMS)
5. User reviews triggered alert and takes action if needed

## Technical Architecture

### Frontend Technologies (Recommended)
- **Framework**: React or Vue.js for component-based UI
- **State Management**: Redux/Vuex for global state
- **Real-time**: Socket.IO or native WebSocket for live updates
- **Charts**: Chart.js or Plotly for performance analytics
- **Styling**: Tailwind CSS or Material-UI for responsive design

### API Endpoints Used
- Market Data: `/api/ltp`, `/api/ck`, `/api/vcp`, `/api/ema-history`
- Positions: `/api/positions`, `/api/holdings`
- Orders: `/api/orderbook`, `/api/gtt`, `/api/trades`
- Strategy: `/api/strategy/momentum/start`, `/stop`, `/status`, `/parameters`
- Scanner: `/api/scanner/results`
- Performance: `/api/performance/analytics`

### Data Flow Example: Real-Time PnL Update
```
1. KiteTicker receives new LTP tick
2. LTP Service updates price cache
3. Strategy Engine recalculates PnL
4. Flask API serves updated data via /api/positions
5. Frontend polls every 5 seconds (or WebSocket push)
6. UI updates position rows with new PnL
7. User sees live color-coded changes
```

## Performance Considerations

### Optimization Tips
1. **Pagination**: Trades tab with 1000+ trades should paginate (50 per page)
2. **Lazy Loading**: Load analytics charts only when tab is opened
3. **Caching**: Cache static reference data (symbols, indicators)
4. **Debouncing**: Debounce filter/search inputs (300ms)
5. **Virtual Scrolling**: For tables with 1000+ rows

### Typical Page Load Times
- Positions Tab: < 500ms (real-time data)
- Trades Tab: < 1s (historical data with pagination)
- Analytics Tab: < 2s (heavy calculations)
- Scanner Tab: < 1s (60-second cache)

## Responsive Design

### Breakpoints
- **Desktop**: 1920px and above (full dashboard)
- **Laptop**: 1280px - 1919px (optimized layout)
- **Tablet**: 768px - 1279px (single-column, stacked tabs)
- **Mobile**: < 768px (mobile-specific views, limited features)

### Mobile Considerations
- Simplified Positions tab (key metrics only)
- Swipeable tabs instead of tab bar
- Larger touch targets (44px minimum)
- Collapsible charts to save space

## Accessibility

### WCAG 2.1 Level AA Compliance
- ✅ Color not sole means of conveying information (also use text)
- ✅ Keyboard navigation for all interactive elements
- ✅ Alt text for charts and images
- ✅ Screen reader compatibility (semantic HTML)
- ✅ Focus indicators for keyboard navigation

## Related Documentation

- 📊 [Strategy Guides](../guides/README.md)
- 🌐 [WebSocket Implementation](../websocket/README.md)
- ⚙️ [Architecture Documentation](../architecture/)
- 📱 [API Reference](../../Webapp/app.py)

## Implementation Checklist

- [ ] Design mockups created for all 11 tabs
- [ ] Frontend framework selected (React/Vue)
- [ ] Authentication implemented
- [ ] Basic layout and routing complete
- [ ] Portfolio tab fully functional
- [ ] Positions tab with real-time updates
- [ ] Orders tab with filtering
- [ ] GTT Orders tab
- [ ] Trades tab with pagination and export
- [ ] Market Data tab with live data
- [ ] Scanner tab with 60s refresh
- [ ] Strategy Control tab
- [ ] Performance Analytics with charts
- [ ] Alerts system
- [ ] Settings/Preferences
- [ ] Mobile responsive testing
- [ ] Accessibility audit
- [ ] Performance optimization
- [ ] Production deployment

## Next Steps

1. Start with `DASHBOARD_TABS_REFERENCE.md` for detailed tab specifications
2. Review `UI_VISUAL_GUIDE.md` for design guidelines
3. Create React/Vue components for each tab
4. Implement API integration with Flask backend
5. Add real-time updates using WebSocket
6. Test responsive design across devices
7. Conduct accessibility audit
