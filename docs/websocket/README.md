# WebSocket Implementation

Complete guide for implementing WebSocket-based broker integration as an alternative to the Kite API.

## Contents

### **WEBSOCKET_ORDER_IMPLEMENTATION.md**
Production-ready guide with:
- **Broker Abstraction Pattern**: Interface design for pluggable brokers
- **Complete WebSocket Implementation**: 400+ lines of production code
  - Connection management and reconnection logic
  - Message parsing and routing
  - Error handling and resilience
  - Event callbacks and order confirmations
- **Message Protocol**: Complete specification for order/quote messages
- **Kite Adapter Reference**: How to implement for Kite API
- **6-Phase Implementation Plan**: Step-by-step migration guide
- **Migration Checklist**: Verification steps for each phase

**Use this when**: Building a WebSocket broker integration, migrating from Kite API, or understanding broker abstraction pattern.

## Why WebSocket?

### Advantages over REST API
- **Real-time Updates**: Immediate order confirmations and fills
- **Event-Driven**: No polling required (better performance)
- **Lower Latency**: Direct connection to broker
- **Bidirectional Communication**: Both send and receive on same connection
- **Scalability**: Handles thousands of concurrent orders

### Trade-offs
- More complex state management
- Requires reconnection handling
- Message protocol must be defined upfront
- Testing is more involved (need to mock WebSocket)

## Architecture Overview

```
┌─────────────────────────────────────────┐
│     Trading Strategy (momentum.py)      │
└──────────────────┬──────────────────────┘
                   │
┌──────────────────▼──────────────────────┐
│      Broker Abstraction Layer           │
│  (BrokerInterface with multiple impl)   │
├─────────────────────────────────────────┤
│  ├─ KiteBroker (REST API)               │
│  ├─ WebSocketBroker (WebSocket)         │
│  └─ PaperBroker (Simulation)            │
└──────────────────┬──────────────────────┘
                   │
        ┌──────────┴──────────┬──────────────┐
        │                     │              │
    ┌───▼───┐        ┌───────▼──┐      ┌────▼─────┐
    │ Kite  │        │ WebSocket │     │  Paper   │
    │ API   │        │  Broker   │     │  Trading │
    │       │        │           │     │          │
    └───────┘        └───────────┘     └──────────┘
```

## Key Components

### 1. **BrokerInterface**
Abstract base class defining common operations:
- `place_order(symbol, qty, side, price)`
- `modify_order(order_id, price, qty)`
- `cancel_order(order_id)`
- `get_orderbook()`
- `place_gtt(symbol, trigger_price, qty, side)`

### 2. **WebSocketBroker**
Implementation using WebSocket protocol:
- Maintains persistent connection
- Handles reconnection with exponential backoff
- Queues orders during disconnection
- Confirms order fills via callbacks

### 3. **Message Protocol**
Standardized message format:
```json
{
  "type": "ORDER_PLACED",
  "order_id": "12345",
  "symbol": "INFY",
  "quantity": 1,
  "price": 1500.00,
  "side": "BUY",
  "status": "PENDING",
  "timestamp": "2026-02-02T10:30:45Z"
}
```

## Implementation Phases

### Phase 1: Setup & Testing
- Define message protocol
- Create abstract interface
- Write unit tests

### Phase 2: WebSocket Connection
- Implement connection manager
- Handle reconnection logic
- Setup message queuing

### Phase 3: Order Execution
- Implement order placement
- Add order tracking
- Confirm fills

### Phase 4: Error Handling
- Add retry logic
- Implement error callbacks
- Add circuit breaker pattern

### Phase 5: Performance Optimization
- Message batching
- Connection pooling
- Order queueing

### Phase 6: Production Hardening
- Load testing
- Chaos engineering
- Production monitoring

## Quick Start

1. **Read the Implementation Guide**: Start with WEBSOCKET_ORDER_IMPLEMENTATION.md section 1-3
2. **Review Code Examples**: Check the production code snippets in sections 4-5
3. **Follow Implementation Plan**: Use sections 6 for step-by-step guide
4. **Use Migration Checklist**: Verify each phase with the provided checklist

## File Structure

After WebSocket implementation, your project structure should look like:

```
Webapp/
├── brokers/
│   ├── __init__.py
│   ├── interface.py              # BrokerInterface (abstract)
│   ├── kite_broker.py            # Kite API implementation
│   ├── websocket_broker.py       # WebSocket implementation
│   ├── paper_broker.py           # Paper trading simulation
│   └── message_protocol.py       # Message definitions
├── app.py                         # Flask app (use BrokerInterface)
├── momentum_strategy.py          # Strategy (use BrokerInterface)
└── ...
```

## Related Documentation

- 📊 [Strategy Guides](../guides/README.md)
- 📱 [Dashboard Reference](../ui/DASHBOARD_TABS_REFERENCE.md)
- ⚙️ [Architecture Documentation](../architecture/)
- 🧪 [Testing Documentation](../features/TESTING_CHECKLIST.md)

## Testing Strategy

For WebSocket implementation:
1. **Unit Tests**: Mock WebSocket connections, test message parsing
2. **Integration Tests**: Test with real broker API (paper trading)
3. **Load Tests**: Simulate high-frequency message flow
4. **Chaos Tests**: Simulate network failures and reconnections

See `TESTING_CHECKLIST.md` for detailed testing procedures.

## Performance Metrics

After WebSocket implementation, you should see:
- **Order Latency**: < 100ms (vs 500ms+ with REST)
- **Connection Uptime**: > 99.9%
- **Message Throughput**: 1000+ messages/sec
- **Reconnection Time**: < 5 seconds

## Security Considerations

- ✅ API keys stored securely (environment variables)
- ✅ Message authentication (broker-specific)
- ✅ Encryption in transit (TLS/WSS)
- ✅ Rate limiting to prevent abuse
- ✅ Input validation on all messages

## Troubleshooting

See the implementation guide's "Common Issues" section for:
- Connection timeouts
- Message parsing errors
- Order confirmation delays
- Reconnection failures
