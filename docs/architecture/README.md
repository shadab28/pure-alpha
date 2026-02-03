# Architecture & Design Documentation

System architecture, design patterns, and technical specifications.

## Contents

This folder contains architectural documentation covering:
- System design and component architecture
- Data flow diagrams and sequence diagrams
- Technology stack and dependencies
- Design patterns used
- Performance and scalability considerations
- Security architecture

## Key Documentation Files

### System Architecture
- **Components**: Flask API, Strategy Engine, Market Data Streaming, Database Layer
- **Communication**: REST APIs, WebSocket (real-time), Database queries
- **Deployment**: Single-server vs distributed architecture options
- **Scalability**: Horizontal scaling considerations

### Technology Stack
- **Backend**: Python 3.8+, Flask 2.x
- **Market Data**: KiteTicker (real-time streaming)
- **Database**: SQLite (trades), PostgreSQL (OHLCV data)
- **Concurrency**: Threading, asyncio
- **API Communication**: HTTP/REST, WebSocket

### Design Patterns

#### 1. **Broker Abstraction Pattern**
Decouples trading strategy from specific broker implementation:
```python
class BrokerInterface:
    def place_order(self, symbol, qty, side, price): pass
    def modify_order(self, order_id, qty, price): pass
    def cancel_order(self, order_id): pass
    def get_orderbook(self): pass
    def place_gtt(self, symbol, trigger, qty, side): pass
```

Implementations:
- KiteBroker (REST API to Zerodha)
- WebSocketBroker (WebSocket connection)
- PaperBroker (Paper trading simulation)

**Benefits**:
- Easy broker switching
- Testability (mock broker)
- Future extensibility (new brokers)

#### 2. **Event-Driven Architecture**
Strategy responds to events rather than polling:
- **Market Events**: LTP ticks, OHLC updates
- **Order Events**: Fills, GTT triggers, rejections
- **System Events**: Strategy start/stop, parameter changes

#### 3. **3-Position Ladder Pattern**
Progressive position entry with reduced average cost:
```
Entry Signal
    │
    ├─→ P1: Entry at signal price
    │       SL: -2.5%, Target: +5%
    │
    ├─→ P2: When P1 PnL > 0.25%
    │       SL: -2.5%, Target: Runner
    │
    └─→ P3: When avg(P1,P2) PnL > 1%
            SL: -5%, Target: Runner
```

#### 4. **Trailing Stop Pattern**
Dynamic stop-loss that only moves up (profit protection):
- Track highest price since entry
- SL = highest × (1 - SL%)
- Update via GTT orders
- Never moves down (ensures profit protection)

### Data Flow Architecture

```
Market Data Flow:
┌─────────────────────────────────────┐
│  KiteTicker (Real-time Streaming)  │
│  - 100+ ticks/second               │
│  - LTP mode for efficiency         │
└──────────────────┬──────────────────┘
                   │
        ┌──────────▼──────────┐
        │  LTP Service       │
        │  - Price cache     │
        │  - Thread-safe     │
        │  - Callback system │
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────────────────────┐
        │  Strategy Engine                  │
        │  - PnL calculation                │
        │  - Position laddering             │
        │  - GTT management                 │
        └──────────┬──────────────────────────┘
                   │
        ┌──────────▼──────────┐
        │  Database Layer    │
        │  - Trade tracking  │
        │  - GTT orders      │
        │  - Historical data │
        └────────────────────┘
```

Order Flow:
```
Strategy Decision → Place Order → Order Sent → Fill Confirmed → GTT Created
    │                 │              │            │               │
    └─ Entry Signal    └─ REST API   └─ KiteTicker └─ Database    └─ SL/Target
```

### Performance Considerations

#### 1. **Optimization Areas**
- **LTP Update Frequency**: 100+ ticks/second (optimized with LTP mode)
- **Strategy Scan Cycle**: 60-second intervals (configurable)
- **Database Queries**: Indexed on symbol, status (< 100ms)
- **API Response Time**: < 500ms for most endpoints

#### 2. **Bottlenecks & Solutions**
| Bottleneck | Current | Solution |
|-----------|---------|----------|
| Database Writes | 1 query/order | Batch writes, async operations |
| Market Data Processing | 100+ ticks/sec | Separate thread, caching |
| API Response Time | ~200ms avg | Caching, query optimization |
| Memory Usage | ~500MB baseline | Streaming, pagination |

#### 3. **Scaling Considerations**
- **Single Server**: Current (supports 200+ positions)
- **Distributed**: Trade data sharded by symbol
- **Microservices**: Separate strategy, market data, order execution
- **Cloud**: AWS/GCP Kubernetes deployment

### Security Architecture

#### 1. **Authentication & Authorization**
- API key-based authentication
- Session-based user authentication
- Role-based access control (RBAC)

#### 2. **Data Protection**
- Sensitive data encrypted at rest
- API keys stored in environment variables
- TLS/SSL for all network communication
- Rate limiting to prevent abuse

#### 3. **Order Execution Safety**
- Order validation before submission
- Quantity limits per order
- Price range validation
- Duplicate order prevention

### Database Architecture

#### SQLite (Trade Persistence)
```sql
trades:
├── trade_id (Primary Key)
├── symbol
├── entry_price
├── qty
├── entry_time
├── exit_time
├── exit_price
├── position_number (1-3)
├── stop_loss
├── target
├── gtt_id
├── pnl
├── pnl_pct
└── status

Indices:
├── (symbol, status) - fast position queries
├── (entry_time) - time-based queries
└── (pnl) - sorting by performance
```

#### PostgreSQL (Historical Data)
```sql
ohlcv_data:
├── timestamp
├── symbol
├── open
├── high
├── low
├── close
├── volume
├── ema_9
├── ema_21
├── ema_50
├── ema_200
└── Indices: (symbol, timestamp)

candle_aggregation:
└── Aggregates tick data to 15-min candles
```

### Deployment Architecture

#### Development
- Local Flask server (port 5000-5050)
- SQLite for all data
- Mock market data for testing

#### Production
- Gunicorn WSGI server
- PostgreSQL for data persistence
- Redis for caching
- Nginx reverse proxy
- Systemd for process management

#### Cloud (Future)
- Docker containerization
- Kubernetes orchestration
- AWS RDS for PostgreSQL
- CloudWatch for monitoring
- Lambda for scheduled tasks

### Integration Points

#### 1. **Broker API (Zerodha Kite)**
- Authentication: Access token (session-based)
- Data Streaming: KiteTicker (WebSocket)
- Order Placement: REST API
- Order Status: WebSocket + REST polling

#### 2. **Market Data**
- Real-time Ticks: KiteTicker
- Historical OHLCV: Kite API + Local storage
- Technical Indicators: Local calculation

#### 3. **External Systems** (Future)
- Email Notifications: SMTP
- SMS Alerts: Twilio
- Analytics: Kafka/Elasticsearch
- Dashboard: React/Vue frontend

## Related Documentation

- 📊 [Strategy Guides](../guides/README.md)
- 🌐 [WebSocket Implementation](../websocket/README.md)
- 📱 [Dashboard Reference](../ui/README.md)
- ✨ [Features Documentation](../features/README.md)
- 📋 [Session Logs](../session-logs/README.md)

## Quick Reference

### Key Metrics
- **Max Concurrent Positions**: 90
- **Capital per Position**: ₹3,000
- **Total Capital Deployed**: ₹240,000
- **Scan Interval**: 60 seconds
- **LTP Update Frequency**: 100+ ticks/second
- **API Response Time**: < 500ms

### File Locations
- Main App: `Webapp/app.py`
- Strategy: `Webapp/momentum_strategy.py`
- Startup: `Webapp/main.py`
- Config: `Support Files/param.yaml`
- Database: `*.db` files

### Important Classes
- `Trade`: Position model (@dataclass)
- `BrokerInterface`: Broker abstraction
- `LTPStore`: Price cache (thread-safe)
- `CandleAgg`: Tick-to-candle conversion

## Design Principles

1. **Single Responsibility**: Each component has one clear purpose
2. **DRY (Don't Repeat Yourself)**: Shared code in utilities
3. **SOLID Principles**: Especially Open/Closed for extensibility
4. **Fail Fast**: Validate inputs early, fail with clear errors
5. **Observable**: Comprehensive logging and monitoring

## Future Enhancements

1. **Multi-Broker Support**: Switch between brokers without code change
2. **Multi-Strategy Support**: Run different strategies on different portfolios
3. **Advanced Analytics**: Machine learning for pattern recognition
4. **Automated Deployment**: CI/CD pipeline with automated testing
5. **Mobile App**: Native iOS/Android app for monitoring

## Questions & Troubleshooting

**Q: How is the system scalable?**
A: See "Scaling Considerations" section above. Current architecture supports up to 200+ positions on single server.

**Q: What about broker redundancy?**
A: Broker abstraction pattern (see Design Patterns) allows switching brokers. WebSocket implementation provides alternative to REST API.

**Q: How are trades persisted?**
A: SQLite for active trades/GTT state, PostgreSQL for historical OHLCV data and performance analytics.

**Q: What's the upgrade path?**
A: Modular design allows incremental upgrades. See Future Enhancements section.
