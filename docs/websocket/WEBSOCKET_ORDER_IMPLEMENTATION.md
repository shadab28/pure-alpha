# WebSocket-Based Order Placement Architecture

## Overview

This guide provides a complete blueprint for replacing Kite API order placement with WebSocket-based orders, while keeping the strategy logic intact.

---

## Core Concept

### Current Architecture (Kite)
```
Strategy → Kite REST API → Orders executed → Kite Webhooks → Strategy updated
```

### WebSocket Architecture
```
Strategy → Broker Interface → WebSocket → Orders executed → WebSocket events → Strategy updated
```

### Key Advantage
**Broker-agnostic design**: Your strategy doesn't know or care whether orders go via Kite, a custom broker, or WebSocket. It just calls `broker.place_order()`.

---

## Phase 1: Create Broker Interface

### File: `broker/interface.py`

```python
"""
Abstract broker interface for order placement.
Decouples strategy from specific broker implementation.
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional, List, Callable, Any
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


@dataclass
class Order:
    """Represents a broker order."""
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: Optional[float]
    order_type: OrderType
    status: OrderStatus
    filled_qty: int = 0
    filled_price: Optional[float] = None
    timestamp: Optional[datetime] = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()


@dataclass
class GTTOrder:
    """Represents a Good-Till-Triggered order."""
    gtt_id: str
    symbol: str
    trigger_price: float
    quantity: int
    status: str = "ACTIVE"  # ACTIVE, TRIGGERED, CANCELLED
    created_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()


class BrokerInterface(ABC):
    """
    Abstract base class for broker implementations.
    
    Strategy code should depend on this interface, not on specific brokers.
    """

    @abstractmethod
    def place_order(self, symbol: str, quantity: int, side: OrderSide,
                   price: Optional[float] = None,
                   order_type: OrderType = OrderType.MARKET) -> Order:
        """
        Place an order (BUY/SELL).
        
        Args:
            symbol: Trading symbol (e.g., 'RELIANCE')
            quantity: Order quantity
            side: BUY or SELL
            price: Limit price (required for LIMIT orders)
            order_type: MARKET or LIMIT
        
        Returns:
            Order object with order_id and status
        
        Raises:
            OrderPlacementError: If order placement fails
        """
        pass

    @abstractmethod
    def place_gtt(self, symbol: str, trigger_price: float,
                 quantity: int, side: OrderSide = OrderSide.SELL) -> GTTOrder:
        """
        Place a Good-Till-Triggered order (typically for stop-loss).
        
        Args:
            symbol: Trading symbol
            trigger_price: Price at which to execute
            quantity: Order quantity
            side: BUY or SELL
        
        Returns:
            GTTOrder object with gtt_id
        """
        pass

    @abstractmethod
    def modify_gtt(self, gtt_id: str, new_trigger_price: float) -> GTTOrder:
        """
        Modify GTT trigger price.
        
        Args:
            gtt_id: ID of GTT order to modify
            new_trigger_price: New trigger price
        
        Returns:
            Updated GTTOrder
        """
        pass

    @abstractmethod
    def cancel_gtt(self, gtt_id: str) -> Dict[str, Any]:
        """
        Cancel a GTT order.
        
        Args:
            gtt_id: ID of GTT order to cancel
        
        Returns:
            {'status': 'CANCELLED', 'gtt_id': '...'}
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an active order."""
        pass

    @abstractmethod
    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        pass

    @abstractmethod
    def get_orders(self) -> List[Order]:
        """Get all recent orders."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get current holdings.
        
        Returns:
            List of positions: [{
                'symbol': 'RELIANCE',
                'quantity': 1,
                'buy_price': 2840.50,
                'current_price': 2850.75,
                'pnl': 10.25
            }, ...]
        """
        pass

    @abstractmethod
    def get_holdings(self) -> List[Dict[str, Any]]:
        """Get lifetime holdings."""
        pass

    @abstractmethod
    def register_order_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Register callback for order update events.
        
        Callback signature: callback(event_dict)
        Event dict contains:
            - type: 'ORDER_UPDATE' | 'GTT_TRIGGERED' | etc.
            - order_id or gtt_id
            - status
            - ... (broker-specific fields)
        """
        pass

    @abstractmethod
    def start(self):
        """Start broker (e.g., connect WebSocket)."""
        pass

    @abstractmethod
    def stop(self):
        """Stop broker (e.g., close WebSocket)."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if broker is connected."""
        pass
```

---

## Phase 2: WebSocket Broker Implementation

### File: `broker/websocket_broker.py`

```python
"""
WebSocket-based broker implementation.
Sends orders via WebSocket instead of REST API.
"""

import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, Optional, List, Callable, Any
from queue import Queue, Empty

import websockets

from broker.interface import (
    BrokerInterface, Order, GTTOrder, OrderSide, OrderStatus, OrderType
)


logger = logging.getLogger(__name__)


class WebSocketBroker(BrokerInterface):
    """
    Broker implementation using WebSocket for order placement.
    
    Connection: wss://broker.example.com/ws
    Authentication: Send AUTH message with api_key
    """

    def __init__(self, ws_url: str, api_key: str, max_reconnect_attempts: int = 10):
        """
        Initialize WebSocket broker.
        
        Args:
            ws_url: WebSocket URL (e.g., 'wss://broker.example.com/ws')
            api_key: API key for authentication
            max_reconnect_attempts: Max retries before giving up
        """
        self.ws_url = ws_url
        self.api_key = api_key
        self.max_reconnect_attempts = max_reconnect_attempts
        
        # Connection state
        self.ws = None
        self.is_connected = False
        self.is_running = False
        
        # Event handling
        self.event_queue: Queue = Queue()
        self.order_callbacks: List[Callable] = []
        
        # Pending orders/GTTs
        self.pending_orders: Dict[str, Order] = {}
        self.pending_gtts: Dict[str, GTTOrder] = {}
        
        # Response handling
        self.responses: Dict[str, Dict[str, Any]] = {}  # request_id -> response
        self.response_event = threading.Event()
        
        # Threads
        self.connection_thread = None
        self.event_processor_thread = None

    def start(self):
        """Start WebSocket connection and event processing."""
        self.is_running = True
        
        # Start connection loop in background
        self.connection_thread = threading.Thread(
            target=self._connection_loop,
            name="ws-broker-connection",
            daemon=True
        )
        self.connection_thread.start()
        
        # Start event processor
        self.event_processor_thread = threading.Thread(
            target=self._event_processor_loop,
            name="ws-broker-events",
            daemon=True
        )
        self.event_processor_thread.start()
        
        # Wait for initial connection
        for _ in range(30):  # 30s timeout
            if self.is_connected:
                logger.info("WebSocket broker connected")
                return
            time.sleep(1)
        
        raise RuntimeError("Failed to connect to WebSocket broker")

    def stop(self):
        """Stop WebSocket connection."""
        self.is_running = False
        
        if self.ws:
            asyncio.run_coroutine_threadsafe(
                self.ws.close(),
                asyncio.get_event_loop()
            )

    def is_connected(self) -> bool:
        """Check connection status."""
        return self.is_connected

    def _connection_loop(self):
        """Run WebSocket connection in separate thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        while self.is_running:
            try:
                loop.run_until_complete(self._websocket_main())
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                time.sleep(2)
        
        loop.close()

    async def _websocket_main(self):
        """Main WebSocket loop."""
        reconnect_attempt = 0
        
        while self.is_running and reconnect_attempt < self.max_reconnect_attempts:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    self.ws = ws
                    logger.info(f"Connected to {self.ws_url}")
                    
                    # Authenticate
                    auth_msg = {
                        'type': 'AUTH',
                        'api_key': self.api_key,
                        'timestamp': datetime.utcnow().isoformat()
                    }
                    await ws.send(json.dumps(auth_msg))
                    
                    self.is_connected = True
                    reconnect_attempt = 0
                    
                    # Listen for messages
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            self.event_queue.put(data)
                        except json.JSONDecodeError as e:
                            logger.error(f"Invalid JSON: {e}")
            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                self.is_connected = False
                reconnect_attempt += 1
                
                if reconnect_attempt < self.max_reconnect_attempts:
                    wait_time = min(2 ** reconnect_attempt, 30)
                    logger.info(f"Reconnecting in {wait_time}s... (attempt {reconnect_attempt})")
                    await asyncio.sleep(wait_time)

    def _event_processor_loop(self):
        """Process incoming WebSocket events."""
        while self.is_running:
            try:
                event = self.event_queue.get(timeout=1)
                self._handle_event(event)
            except Empty:
                continue
            except Exception as e:
                logger.error(f"Event processing error: {e}")

    def _handle_event(self, event: Dict[str, Any]):
        """Route incoming events to appropriate handlers."""
        msg_type = event.get('type')
        request_id = event.get('request_id')
        
        if msg_type == 'RESPONSE':
            # Store response for synchronous calls
            self.responses[request_id] = event
            self.response_event.set()
        
        elif msg_type == 'ORDER_UPDATE':
            self._handle_order_update(event)
        
        elif msg_type == 'GTT_TRIGGERED':
            self._handle_gtt_trigger(event)
        
        elif msg_type == 'ERROR':
            logger.error(f"Broker error: {event.get('message')}")
        
        else:
            logger.debug(f"Unknown event type: {msg_type}")

    def _handle_order_update(self, event: Dict[str, Any]):
        """Handle order update event."""
        order_id = event.get('order_id')
        
        if order_id in self.pending_orders:
            order = self.pending_orders[order_id]
            order.status = OrderStatus[event.get('status', 'PENDING')]
            
            if event.get('filled_price'):
                order.filled_price = event.get('filled_price')
            if event.get('filled_qty'):
                order.filled_qty = event.get('filled_qty')
        
        # Trigger callbacks
        for callback in self.order_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def _handle_gtt_trigger(self, event: Dict[str, Any]):
        """Handle GTT trigger event."""
        gtt_id = event.get('gtt_id')
        
        if gtt_id in self.pending_gtts:
            gtt = self.pending_gtts[gtt_id]
            gtt.status = "TRIGGERED"
        
        # Trigger callbacks
        for callback in self.order_callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def _send_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send message to broker and wait for response.
        
        Returns: Response dict from broker
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to broker")
        
        request_id = str(uuid.uuid4())
        msg['request_id'] = request_id
        msg['timestamp'] = datetime.utcnow().isoformat()
        
        # Send message
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(
            self.ws.send(json.dumps(msg)),
            loop
        )
        
        # Wait for response (5s timeout)
        self.response_event.clear()
        self.response_event.wait(timeout=5)
        
        response = self.responses.pop(request_id, None)
        if not response:
            raise RuntimeError("No response from broker")
        
        if response.get('status') == 'ERROR':
            raise RuntimeError(response.get('message', 'Unknown error'))
        
        return response

    # ========== Order Placement ==========

    def place_order(self, symbol: str, quantity: int, side: OrderSide,
                   price: Optional[float] = None,
                   order_type: OrderType = OrderType.MARKET) -> Order:
        """Place an order via WebSocket."""
        
        msg = {
            'type': 'PLACE_ORDER',
            'symbol': symbol,
            'quantity': quantity,
            'side': side.value,
            'order_type': order_type.value,
        }
        
        if order_type == OrderType.LIMIT and price:
            msg['price'] = price
        
        response = self._send_message(msg)
        
        order = Order(
            order_id=response['order_id'],
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            status=OrderStatus.PENDING,
            filled_qty=0
        )
        
        self.pending_orders[order.order_id] = order
        logger.info(f"Order placed: {symbol} {quantity}@{price} (ID: {order.order_id})")
        
        return order

    def place_gtt(self, symbol: str, trigger_price: float,
                 quantity: int, side: OrderSide = OrderSide.SELL) -> GTTOrder:
        """Place a GTT order via WebSocket."""
        
        msg = {
            'type': 'PLACE_GTT',
            'symbol': symbol,
            'trigger_price': trigger_price,
            'quantity': quantity,
            'side': side.value,
        }
        
        response = self._send_message(msg)
        
        gtt = GTTOrder(
            gtt_id=response['gtt_id'],
            symbol=symbol,
            trigger_price=trigger_price,
            quantity=quantity,
            status='ACTIVE'
        )
        
        self.pending_gtts[gtt.gtt_id] = gtt
        logger.info(f"GTT placed: {symbol} @{trigger_price} (ID: {gtt.gtt_id})")
        
        return gtt

    def modify_gtt(self, gtt_id: str, new_trigger_price: float) -> GTTOrder:
        """Modify GTT trigger price."""
        
        msg = {
            'type': 'MODIFY_GTT',
            'gtt_id': gtt_id,
            'new_trigger_price': new_trigger_price,
        }
        
        response = self._send_message(msg)
        
        if gtt_id in self.pending_gtts:
            gtt = self.pending_gtts[gtt_id]
            gtt.trigger_price = new_trigger_price
            logger.info(f"GTT modified: {gtt_id} → {new_trigger_price}")
            return gtt
        
        raise ValueError(f"GTT not found: {gtt_id}")

    def cancel_gtt(self, gtt_id: str) -> Dict[str, Any]:
        """Cancel a GTT order."""
        
        msg = {
            'type': 'CANCEL_GTT',
            'gtt_id': gtt_id,
        }
        
        response = self._send_message(msg)
        
        if gtt_id in self.pending_gtts:
            gtt = self.pending_gtts[gtt_id]
            gtt.status = 'CANCELLED'
            del self.pending_gtts[gtt_id]
        
        logger.info(f"GTT cancelled: {gtt_id}")
        return {'status': 'CANCELLED', 'gtt_id': gtt_id}

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an order."""
        
        msg = {
            'type': 'CANCEL_ORDER',
            'order_id': order_id,
        }
        
        response = self._send_message(msg)
        return response

    # ========== Query Methods ==========

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID from pending orders."""
        return self.pending_orders.get(order_id)

    def get_orders(self) -> List[Order]:
        """Get all pending orders."""
        return list(self.pending_orders.values())

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions."""
        msg = {'type': 'GET_POSITIONS'}
        response = self._send_message(msg)
        return response.get('positions', [])

    def get_holdings(self) -> List[Dict[str, Any]]:
        """Get lifetime holdings."""
        msg = {'type': 'GET_HOLDINGS'}
        response = self._send_message(msg)
        return response.get('holdings', [])

    def register_order_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Register callback for order events."""
        self.order_callbacks.append(callback)
        logger.info(f"Registered order callback: {callback.__name__}")
```

---

## Phase 3: Kite Adapter (for comparison)

### File: `broker/kite_broker.py`

```python
"""
Kite API broker adapter (for reference/comparison).
Implements BrokerInterface using Kite REST API.
"""

from typing import Dict, Optional, List, Callable, Any
from kiteconnect import KiteConnect, KiteTicker

from broker.interface import (
    BrokerInterface, Order, GTTOrder, OrderSide, OrderStatus, OrderType
)


class KiteBroker(BrokerInterface):
    """Adapter for Kite API broker."""

    def __init__(self, kite: KiteConnect, ticker: KiteTicker = None):
        self.kite = kite
        self.ticker = ticker
        self.order_callbacks: List[Callable] = []
        
        # Register ticker callbacks if provided
        if ticker:
            @ticker.on_order_update
            def on_order_update(data):
                self._handle_order_update(data)

    def place_order(self, symbol: str, quantity: int, side: OrderSide,
                   price: Optional[float] = None,
                   order_type: OrderType = OrderType.MARKET) -> Order:
        """Place order via Kite REST API."""
        
        order_response = self.kite.place_order(
            variety='regular',
            tradingsymbol=symbol,
            transaction_type=side.value,
            quantity=quantity,
            price=price or 0,
            order_type=order_type.value,
            product='MIS'
        )
        
        order = Order(
            order_id=str(order_response['order_id']),
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            order_type=order_type,
            status=OrderStatus.COMPLETE
        )
        
        return order

    def place_gtt(self, symbol: str, trigger_price: float,
                 quantity: int, side: OrderSide = OrderSide.SELL) -> GTTOrder:
        """Place GTT via Kite API."""
        
        gtt_response = self.kite.place_gtt(
            trigger_values={'trigger_values': [trigger_price]},
            orders=[{
                'variety': 'regular',
                'tradingsymbol': symbol,
                'transaction_type': side.value,
                'quantity': quantity,
                'price': trigger_price,
                'order_type': 'LIMIT',
                'product': 'MIS'
            }]
        )
        
        return GTTOrder(
            gtt_id=str(gtt_response['gtt_id']),
            symbol=symbol,
            trigger_price=trigger_price,
            quantity=quantity,
            status='ACTIVE'
        )

    def modify_gtt(self, gtt_id: str, new_trigger_price: float) -> GTTOrder:
        """Modify GTT via Kite API."""
        
        self.kite.modify_gtt(
            gtt_id=gtt_id,
            trigger_values={'trigger_values': [new_trigger_price]}
        )
        
        return GTTOrder(
            gtt_id=gtt_id,
            symbol='',
            trigger_price=new_trigger_price,
            quantity=0
        )

    def cancel_gtt(self, gtt_id: str) -> Dict[str, Any]:
        """Cancel GTT via Kite API."""
        self.kite.delete_gtt(gtt_id=gtt_id)
        return {'status': 'CANCELLED', 'gtt_id': gtt_id}

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel order via Kite API."""
        self.kite.cancel_order(variety='regular', order_id=order_id)
        return {'status': 'CANCELLED', 'order_id': order_id}

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order from Kite."""
        orders = self.kite.orders()
        for o in orders:
            if str(o['order_id']) == order_id:
                return Order(
                    order_id=str(o['order_id']),
                    symbol=o['tradingsymbol'],
                    side=OrderSide[o['transaction_type']],
                    quantity=int(o['quantity']),
                    price=float(o['price']),
                    order_type=OrderType[o['order_type']],
                    status=OrderStatus[o['status']]
                )
        return None

    def get_orders(self) -> List[Order]:
        """Get all orders from Kite."""
        orders = self.kite.orders()
        return [
            Order(
                order_id=str(o['order_id']),
                symbol=o['tradingsymbol'],
                side=OrderSide[o['transaction_type']],
                quantity=int(o['quantity']),
                price=float(o['price']),
                order_type=OrderType[o['order_type']],
                status=OrderStatus[o['status']]
            )
            for o in orders
        ]

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get positions from Kite."""
        positions = self.kite.positions()
        return positions.get('net', [])

    def get_holdings(self) -> List[Dict[str, Any]]:
        """Get holdings from Kite."""
        return self.kite.holdings()

    def register_order_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Register callback (handled by ticker)."""
        self.order_callbacks.append(callback)

    def start(self):
        """Connect Kite (no-op, already connected)."""
        pass

    def stop(self):
        """Disconnect Kite."""
        pass

    def is_connected(self) -> bool:
        """Check Kite connection."""
        return True  # Assume already authenticated

    def _handle_order_update(self, data):
        """Handle Kite order update event."""
        for callback in self.order_callbacks:
            callback(data)
```

---

## Phase 4: Integration with Strategy

### File: `momentum_strategy.py` (modified sections)

```python
# At top of file
from broker.interface import BrokerInterface, OrderSide, OrderType

class MomentumStrategy:
    def __init__(self, broker: BrokerInterface, symbols: List[str], 
                 ltp_callback: Callable):
        """
        Initialize strategy with any broker (Kite, WebSocket, etc).
        
        Args:
            broker: BrokerInterface implementation
            symbols: List of trading symbols
            ltp_callback: Function to get current LTP
        """
        self.broker = broker  # ← Use interface, not specific broker
        self.symbols = symbols
        self.ltp_callback = ltp_callback
        
        # Register for order events
        self.broker.register_order_callback(self._on_order_event)
    
    def place_entry_order(self, symbol: str, quantity: int) -> str:
        """Place BUY order (works with any broker)."""
        ltp = self.ltp_callback(symbol)
        
        order = self.broker.place_order(
            symbol=symbol,
            quantity=quantity,
            side=OrderSide.BUY,
            price=ltp,
            order_type=OrderType.MARKET  # Use market order
        )
        
        logger.info(f"Entry order placed: {symbol} qty={quantity} price={ltp} "
                   f"order_id={order.order_id}")
        
        return order.order_id
    
    def place_stop_loss_gtt(self, symbol: str, stop_loss_price: float, 
                           quantity: int) -> str:
        """Place stop-loss GTT (works with any broker)."""
        
        gtt = self.broker.place_gtt(
            symbol=symbol,
            trigger_price=stop_loss_price,
            quantity=quantity,
            side=OrderSide.SELL
        )
        
        logger.info(f"GTT placed: {symbol} qty={quantity} trigger={stop_loss_price} "
                   f"gtt_id={gtt.gtt_id}")
        
        return gtt.gtt_id
    
    def update_trailing_stop(self, gtt_id: str, new_sl_price: float):
        """Update stop-loss (works with any broker)."""
        try:
            gtt = self.broker.modify_gtt(gtt_id, new_sl_price)
            logger.info(f"GTT modified: {gtt_id} → {new_sl_price}")
        except Exception as e:
            logger.error(f"Failed to modify GTT: {e}")
    
    def _on_order_event(self, event: Dict[str, Any]):
        """
        Handle order events from any broker.
        
        Expected event formats (broker-independent):
        {
            'type': 'ORDER_UPDATE',
            'order_id': '...',
            'status': 'COMPLETE' | 'CANCELLED' | 'FAILED',
            'filled_price': 2840.50,
            'symbol': 'RELIANCE'
        }
        
        {
            'type': 'GTT_TRIGGERED',
            'gtt_id': '...',
            'symbol': 'RELIANCE',
            'trigger_price': 2800.00
        }
        """
        
        if event.get('type') == 'ORDER_UPDATE':
            self._handle_order_update(event)
        
        elif event.get('type') == 'GTT_TRIGGERED':
            self._handle_gtt_trigger(event)
    
    def _handle_order_update(self, event: Dict[str, Any]):
        """Handle order fill event."""
        order_id = event.get('order_id')
        status = event.get('status')
        
        if status == 'COMPLETE':
            # Find trade in DB matching this order_id
            trade = self._find_trade_by_order_id(order_id)
            if trade:
                trade.order_id = order_id
                self._db.update_trade(trade)
                logger.info(f"Order executed: {order_id}")
    
    def _handle_gtt_trigger(self, event: Dict[str, Any]):
        """Handle GTT trigger (stop-loss execution)."""
        gtt_id = event.get('gtt_id')
        symbol = event.get('symbol')
        
        # Find trade matching this GTT
        trade = self._find_trade_by_gtt_id(gtt_id)
        if trade:
            trade.exit_time = datetime.utcnow()
            trade.exit_price = event.get('trigger_price')
            trade.status = TradeStatus.CLOSED
            
            # Calculate PnL
            trade.pnl = (trade.exit_price - trade.entry_price) * trade.qty
            
            self._db.close_trade(trade)
            logger.info(f"Trade closed by GTT: {symbol} PnL={trade.pnl}")
```

---

## Phase 5: Configuration

### File: `config.yaml`

```yaml
# Select broker type: kite | websocket
broker:
  type: websocket  # or 'kite'
  
  # For WebSocket broker
  websocket:
    url: "wss://broker.example.com/ws"
    api_key: "${BROKER_API_KEY}"
    max_reconnect_attempts: 10
  
  # For Kite broker (for reference)
  kite:
    api_key: "${KITE_API_KEY}"
    access_token: "${KITE_ACCESS_TOKEN}"

strategy:
  capital: 240000
  per_position: 3000
  max_positions: 90
  scan_interval: 60
  min_rank_gm: 2.5

database:
  sqlite: "./trading_strategy.db"
  postgres:
    host: "localhost"
    port: 5432
    database: "trading"
    user: "${DB_USER}"
    password: "${DB_PASSWORD}"
```

---

## Phase 6: Startup Code

### File: `main.py` (modified sections)

```python
import yaml
from broker.websocket_broker import WebSocketBroker
from broker.kite_broker import KiteBroker
from momentum_strategy import MomentumStrategy

def create_broker(config: Dict) -> BrokerInterface:
    """Factory function to create appropriate broker."""
    broker_type = config.get('broker', {}).get('type', 'websocket')
    
    if broker_type == 'websocket':
        ws_config = config.get('broker', {}).get('websocket', {})
        broker = WebSocketBroker(
            ws_url=ws_config['url'],
            api_key=ws_config.get('api_key'),
            max_reconnect_attempts=ws_config.get('max_reconnect_attempts', 10)
        )
    
    elif broker_type == 'kite':
        # For comparison - existing Kite setup
        from kiteconnect import KiteConnect, KiteTicker
        kite = KiteConnect(api_key=API_KEY, access_token=TOKEN)
        ticker = KiteTicker(api_key=API_KEY, access_token=TOKEN)
        broker = KiteBroker(kite, ticker)
    
    else:
        raise ValueError(f"Unknown broker type: {broker_type}")
    
    return broker

def main():
    # Load config
    with open('config.yaml') as f:
        config = yaml.safe_load(f)
    
    # Create broker (works with any implementation!)
    broker = create_broker(config)
    broker.start()
    
    # Load symbols
    symbols = load_symbol_list('stocks2026')
    
    # Create strategy with broker interface
    strategy = MomentumStrategy(
        broker=broker,
        symbols=symbols,
        ltp_callback=ltp_store.get_ltp
    )
    
    # Start strategy
    strategy_thread = threading.Thread(
        target=strategy.run,
        daemon=False
    )
    strategy_thread.start()
    
    # ... rest of main loop
    
    try:
        while True:
            time.sleep(1)
    finally:
        broker.stop()
        strategy.stop()

if __name__ == '__main__':
    main()
```

---

## WebSocket Message Specification

### Client → Server

#### PLACE_ORDER
```json
{
  "type": "PLACE_ORDER",
  "request_id": "uuid-1234",
  "symbol": "RELIANCE",
  "quantity": 1,
  "side": "BUY",
  "order_type": "MARKET",
  "price": null,
  "timestamp": "2026-02-02T09:30:15Z"
}
```

**Response:**
```json
{
  "type": "RESPONSE",
  "request_id": "uuid-1234",
  "status": "SUCCESS",
  "order_id": "ORD-1234567",
  "timestamp": "2026-02-02T09:30:16Z"
}
```

#### PLACE_GTT
```json
{
  "type": "PLACE_GTT",
  "request_id": "uuid-5678",
  "symbol": "RELIANCE",
  "trigger_price": 2800.00,
  "quantity": 1,
  "side": "SELL",
  "timestamp": "2026-02-02T09:35:00Z"
}
```

**Response:**
```json
{
  "type": "RESPONSE",
  "request_id": "uuid-5678",
  "status": "SUCCESS",
  "gtt_id": "GTT-9876543",
  "timestamp": "2026-02-02T09:35:01Z"
}
```

#### MODIFY_GTT
```json
{
  "type": "MODIFY_GTT",
  "request_id": "uuid-9999",
  "gtt_id": "GTT-9876543",
  "new_trigger_price": 2810.00,
  "timestamp": "2026-02-02T14:00:00Z"
}
```

#### CANCEL_GTT
```json
{
  "type": "CANCEL_GTT",
  "request_id": "uuid-8888",
  "gtt_id": "GTT-9876543",
  "timestamp": "2026-02-02T15:00:00Z"
}
```

#### GET_POSITIONS
```json
{
  "type": "GET_POSITIONS",
  "request_id": "uuid-7777",
  "timestamp": "2026-02-02T09:30:15Z"
}
```

**Response:**
```json
{
  "type": "RESPONSE",
  "request_id": "uuid-7777",
  "status": "SUCCESS",
  "positions": [
    {
      "symbol": "RELIANCE",
      "quantity": 1,
      "buy_price": 2840.50,
      "current_price": 2850.75,
      "pnl": 10.25
    }
  ],
  "timestamp": "2026-02-02T09:30:16Z"
}
```

### Server → Client (Unsolicited)

#### ORDER_UPDATE
```json
{
  "type": "ORDER_UPDATE",
  "order_id": "ORD-1234567",
  "status": "COMPLETE",
  "symbol": "RELIANCE",
  "filled_price": 2840.50,
  "filled_qty": 1,
  "timestamp": "2026-02-02T09:30:20Z"
}
```

#### GTT_TRIGGERED
```json
{
  "type": "GTT_TRIGGERED",
  "gtt_id": "GTT-9876543",
  "symbol": "RELIANCE",
  "trigger_price": 2800.00,
  "timestamp": "2026-02-02T14:25:30Z"
}
```

#### ERROR
```json
{
  "type": "ERROR",
  "request_id": "uuid-1234",
  "status": "ERROR",
  "message": "Order placement failed: Insufficient balance",
  "timestamp": "2026-02-02T09:30:16Z"
}
```

---

## Testing Strategy

```python
# test_broker.py

def test_websocket_order_placement():
    """Test WebSocket order placement."""
    broker = WebSocketBroker('wss://localhost:8765', 'test_key')
    broker.start()
    
    # Place order
    order = broker.place_order(
        symbol='TESTSTOCK',
        quantity=1,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET
    )
    
    assert order.order_id is not None
    assert order.status == OrderStatus.PENDING
    
    # Register callback
    events = []
    broker.register_order_callback(lambda e: events.append(e))
    
    # Simulate order fill
    # ... (mock WebSocket server would send ORDER_UPDATE)
    
    # Check callback was triggered
    assert len(events) > 0
    
    broker.stop()

def test_strategy_with_websocket_broker():
    """Test strategy using WebSocket broker."""
    broker = WebSocketBroker('wss://localhost:8765', 'test_key')
    broker.start()
    
    strategy = MomentumStrategy(
        broker=broker,
        symbols=['RELIANCE', 'TCS'],
        ltp_callback=lambda s: 2840.50
    )
    
    # Place entry
    order_id = strategy.place_entry_order('RELIANCE', 1)
    assert order_id is not None
    
    # Place stop-loss
    gtt_id = strategy.place_stop_loss_gtt('RELIANCE', 2800.00, 1)
    assert gtt_id is not None
    
    broker.stop()
```

---

## Migration Checklist

- [ ] Create `broker/interface.py` with `BrokerInterface`
- [ ] Implement `broker/websocket_broker.py`
- [ ] Create `broker/kite_broker.py` adapter
- [ ] Update `momentum_strategy.py` to use `self.broker` interface
- [ ] Update `app.py` API endpoints to use `app.broker`
- [ ] Create `config.yaml` with broker selection
- [ ] Update `main.py` with factory function
- [ ] Write unit tests for WebSocket broker
- [ ] Test with mock WebSocket server
- [ ] Document WebSocket message protocol
- [ ] Add error handling & retry logic
- [ ] Implement connection health checks
- [ ] Add metrics/logging for orders
- [ ] Test failover from WebSocket to REST (if needed)
- [ ] Deploy and monitor in production

---

## Summary

This architecture allows you to:

1. **Swap brokers easily**: Change `config.yaml` to use a different broker
2. **Keep strategy code unchanged**: Strategy depends only on `BrokerInterface`
3. **Support multiple brokers simultaneously**: Run multiple strategies with different brokers
4. **Test with mocks**: Implement a mock broker for testing
5. **Scale independently**: Broker and strategy are decoupled

The key principle: **Depend on abstractions, not implementations.**

