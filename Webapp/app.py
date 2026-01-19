"""Flask app wiring routes to LTP service and using external template."""
from __future__ import annotations

import os
import sys
import logging

# Import will happen AFTER main.py configures logging, so we don't need to configure here
# Just ensure werkzeug doesn't log too much
logging.getLogger("werkzeug").setLevel(logging.WARNING)

from flask import Flask, jsonify, render_template, Response, request
import time
import threading
import json

# Allow direct execution without treating Webapp as a package
CURRENT_DIR = os.path.dirname(__file__)
REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
	sys.path.append(CURRENT_DIR)
if REPO_ROOT not in sys.path:
	sys.path.insert(0, REPO_ROOT)

# Import validation module from src.security
try:
	from src.security.validation import (
		validate_symbol, validate_quantity, validate_price,
		validate_order_type, ValidationError
	)
except ImportError:
	# Fallback if validation module not available
	class ValidationError(ValueError):
		pass
if CURRENT_DIR not in sys.path:
	sys.path.append(CURRENT_DIR)
if REPO_ROOT not in sys.path:
	sys.path.insert(0, REPO_ROOT)

def load_dotenv(path: str):
	"""Minimal .env loader (does not overwrite existing vars)."""
	if not os.path.exists(path):
		return
	with open(path) as f:
		for line in f:
			line = line.strip()
			if not line or line.startswith('#') or '=' not in line:
				continue
			k, v = line.split('=', 1)
			k = k.strip()
			v = v.strip().strip("'\"")
			os.environ.setdefault(k, v)

# Load repo root .env before importing data service
load_dotenv(os.path.join(REPO_ROOT, '.env'))

from ltp_service import fetch_ltp  # type: ignore
from ltp_service import get_kite  # type: ignore

# Import security modules
try:
	from src.security.security_headers import add_security_headers
except ImportError:
	def add_security_headers(app):
		"""Fallback if security_headers module not available."""
		@app.after_request
		def set_basic_headers(response):
			response.headers['X-Frame-Options'] = 'DENY'
			response.headers['X-Content-Type-Options'] = 'nosniff'
			return response
		return app

# Import authentication modules
try:
	from src.security.auth import (
		init_auth, get_current_user, login_user, logout_user,
		require_login, hash_password, verify_password, User, UserRole
	)
except ImportError:
	# Fallback if auth module not available
	def init_auth(app):
		return app
	def get_current_user():
		return None
	def login_user(user):
		pass
	def logout_user():
		pass
	def require_login(required_role=None):
		def decorator(f):
			return f
		return decorator
	def hash_password(pwd):
		return pwd
	def verify_password(pwd, hashed):
		return pwd == hashed
	class User:
		pass
	class UserRole:
		pass

try:
	from src.security.csrf_protection import init_csrf, generate_csrf_token, validate_csrf_token, csrf_protect, CSRFError
except ImportError:
	# Fallback if CSRF module not available
	def init_csrf(app):
		return app
	def generate_csrf_token():
		return ""
	def validate_csrf_token(token):
		return True
	def csrf_protect(f):
		return f
	class CSRFError(Exception):
		pass

app = Flask(__name__, template_folder="templates")

# -------- Security Headers Setup --------
# Add OWASP-recommended security headers to all responses
app = add_security_headers(app)

# -------- Authentication & CSRF Setup --------
# Set secret key for session/CSRF management
if not app.secret_key:
	app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

# Secure session configuration
app.config['SESSION_COOKIE_SECURE'] = True  # HTTPS only
app.config['SESSION_COOKIE_HTTPONLY'] = True  # No JavaScript access
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
app.config['PERMANENT_SESSION_LIFETIME'] = 3600  # 1 hour session

# Initialize authentication and CSRF protection
app = init_auth(app)
app = init_csrf(app)

# -------- Rate Limiting (DISABLED) --------
# Dummy limiter - decorators do nothing
class DummyLimiter:
	def limit(self, *args, **kwargs):
		def decorator(f):
			return f
		return decorator

limiter = DummyLimiter()

# -------- Logging setup (date-wise subfolders) --------
LOG_BASE_DIR = os.path.join(REPO_ROOT, 'logs')
os.makedirs(LOG_BASE_DIR, exist_ok=True)

from datetime import datetime

def _get_date_log_dir() -> str:
	"""Get or create today's log directory: logs/2026-01-06/"""
	today = datetime.now().strftime('%Y-%m-%d')
	date_dir = os.path.join(LOG_BASE_DIR, today)
	os.makedirs(date_dir, exist_ok=True)
	return date_dir

class DateFolderFileHandler(logging.FileHandler):
	"""A file handler that writes to date-based subfolders.
	
	Creates structure like:
	  logs/2026-01-06/orders.log
	  logs/2026-01-06/trailing.log
	  logs/2026-01-05/orders.log
	  etc.
	"""
	def __init__(self, filename: str, mode='a', encoding='utf-8'):
		self.base_filename = filename
		self._current_date = None
		# Initialize with today's path
		self._update_stream()
		super().__init__(self._get_current_path(), mode=mode, encoding=encoding)
	
	def _get_current_path(self) -> str:
		return os.path.join(_get_date_log_dir(), self.base_filename)
	
	def _update_stream(self):
		"""Check if date changed and update file path if needed."""
		today = datetime.now().strftime('%Y-%m-%d')
		if self._current_date != today:
			self._current_date = today
			return True
		return False
	
	def emit(self, record):
		"""Emit a record, switching to new date folder if needed."""
		if self._update_stream():
			# Date changed, close old stream and open new one
			if self.stream:
				self.stream.close()
				self.stream = None
			self.baseFilename = self._get_current_path()
			os.makedirs(os.path.dirname(self.baseFilename), exist_ok=True)
			self.stream = self._open()
		super().emit(record)

def _get_logger(name: str, filename: str) -> logging.Logger:
	"""Create a logger that writes to date-based subfolders.
	
	Log files will be organized like:
	  logs/2026-01-06/orders.log
	  logs/2026-01-06/trailing.log
	  logs/2026-01-06/errors.log
	"""
	logger = logging.getLogger(name)
	if logger.handlers:
		return logger
	logger.setLevel(logging.INFO)
	handler = DateFolderFileHandler(filename)
	formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s - %(message)s')
	handler.setFormatter(formatter)
	logger.addHandler(handler)
	return logger

access_logger = _get_logger('access', 'access.log')
order_logger = _get_logger('orders', 'orders.log')
error_logger = _get_logger('errors', 'errors.log')
trail_logger = _get_logger('trail', 'trailing.log')
# Enable debug-level tracing for trailing worker to aid diagnosis
try:
	trail_logger.setLevel(logging.DEBUG)
except Exception:
	pass

# -------- In-memory store for orders placed via this webapp --------
_orders_lock = threading.RLock()
_orders_store: dict[str, dict] = {}

# -------- Last stop-trigger timestamps to prevent immediate re-entry --------
# Records when a stop-loss (or stop-triggered sell) occurs for a symbol.
_last_stop_ts_lock = threading.RLock()
_last_stop_ts: dict[str, datetime] = {}

def _record_stop_trigger(symbol: str) -> None:
	"""Record that a stop trigger/exit happened for `symbol` now.

	This is used to enforce a short cooldown before taking a new entry.
	"""
	try:
		if not symbol:
			return
		with _last_stop_ts_lock:
			_last_stop_ts[symbol] = datetime.now()
		order_logger.info("STOP_TRIGGER recorded for %s at %s", symbol, _last_stop_ts[symbol].isoformat())
	except Exception as e:
		error_logger.warning("Failed to record stop trigger for %s: %s", symbol, e)

def _can_enter_symbol(symbol: str, cooldown_seconds: int = 180) -> tuple[bool, int | None]:
	"""Return (allowed, remaining_seconds).

	allowed is True when no recent stop within cooldown_seconds. If False, remaining_seconds
	is how many seconds left before entry is permitted.
	"""
	with _last_stop_ts_lock:
		ts = _last_stop_ts.get(symbol)
	if not ts:
		return True, None
	delta = datetime.now() - ts
	rem = cooldown_seconds - int(delta.total_seconds())
	if rem <= 0:
		return True, None
	return False, rem

# Cached broker orders (updated via Kite Ticker order_update), and cached GTTs
_broker_orders_cache: dict[str, dict] = {}
_broker_orders_lock = threading.RLock()
_gtt_cache: dict[str, list] = {}
_gtt_cache_ts: float = 0.0
_order_event_queues: list = []

def _fetch_gtts_with_timeout(kite, timeout=2.0):
	"""Attempt to fetch GTTs from kite but return quickly using a thread timeout.

	If fetch doesn't complete within `timeout` seconds, return cached _gtt_cache flattened.
	"""
	import threading
	result = {}
	def worker():
		try:
			if hasattr(kite, 'get_gtts'):
				res = kite.get_gtts() or []
			elif hasattr(kite, 'get_gtt'):
				res = [kite.get_gtt()] or []
			else:
				res = []
			result['v'] = res
		except Exception as e:
			error_logger.warning("Failed to fetch GTT list: %s", e)
			result['v'] = []

	th = threading.Thread(target=worker, daemon=True)
	th.start()
	th.join(timeout)
	if 'v' in result:
		return result['v']
	# timeout -> return flattened cache
	tmp = []
	for arr in _gtt_cache.values():
		tmp.extend(arr)
	return tmp

def _ensure_order_listener():
	"""Start Kite Ticker to listen for order updates and keep a local cache."""
	try:
		kite = get_kite()
		# If ticker is not available, skip silently
		if not hasattr(kite, 'ticker'):
			return
		ticker = kite.ticker()
	except Exception as e:
		error_logger.error("Failed to init Kite ticker: %s", e)
		return

	def on_order_update(data):
		try:
			oid = str(data.get('order_id') or data.get('id') or '')
			if not oid:
				return
			with _broker_orders_lock:
				_broker_orders_cache[oid] = data
			# If this order update looks like a stop-loss / exit (SELL filled, or trigger by stop), record it
			try:
				otype = (data.get('transaction_type') or data.get('transactionType') or '').upper()
				status = (data.get('status') or data.get('order_status') or '').upper()
				symbol = data.get('tradingsymbol') or data.get('instrument_token') or None
				# Heuristic: a SELL order that reached 'COMPLETE' or 'TRIGGERED' likely indicates an exit
				if otype == 'SELL' and status in ('COMPLETE', 'TRIGGERED', 'FILLED', 'COMPLETE'):
					# tradingsymbol should be a symbol string; only record if looks like NSE symbol
					if isinstance(symbol, str) and symbol:
						_record_stop_trigger(symbol)
			except Exception:
				# don't let this break order handling
				pass
			# broadcast to SSE listeners
			try:
				msg = json.dumps({"type":"order_update","order":data})
				for q in list(_order_event_queues):
					try:
						q.put_nowait(msg)
					except Exception as e:
						error_logger.debug("Failed to broadcast order update to queue: %s", e)
			except Exception as e:
				# swallow broadcast errors but log them
				error_logger.warning("Error broadcasting order updates: %s", e)
		except Exception as e:
			error_logger.warning("Error in order event handler: %s", e)

	def on_connect(params):
		# On connect, prime cache with current orders once
		try:
			orders = kite.orders() or []
			with _broker_orders_lock:
				for o in orders:
					oid = str(o.get('order_id') or '')
					if oid:
						_broker_orders_cache[oid] = o
		except Exception as e:
			error_logger.error("orders() prime failed: %s", e)

	try:
		ticker.on_order_update = on_order_update
		ticker.on_connect = on_connect
		threading.Thread(target=ticker.connect, name="kite_order_listener", daemon=True).start()
		access_logger.info("Order listener started")
	except Exception as e:
		error_logger.error("Failed to start ticker listener: %s", e)

@app.get('/events/orders')
def sse_orders():
	"""Server-Sent Events: stream order updates to clients."""
	try:
		import queue
		q = queue.Queue(maxsize=1000)
		_order_event_queues.append(q)
		def gen():
			# send a prime snapshot
			with _broker_orders_lock:
				snapshot = list(_broker_orders_cache.values())
			try:
				yield f"data: {json.dumps({'type':'snapshot','orders':snapshot})}\n\n"
			except Exception as e:
				# if client disconnects early, stop
				error_logger.debug("Client disconnected from SSE stream: %s", e)
				return
			while True:
				try:
					msg = q.get()
					yield f"data: {msg}\n\n"
				except GeneratorExit:
					break
				except Exception as e:
					error_logger.debug("Error in SSE generation: %s", e)
					time.sleep(0.5)
		# Ensure listener
		try:
			_ensure_order_listener()
		except Exception as e:
			error_logger.warning("Failed to ensure order listener: %s", e)
		return Response(gen(), mimetype='text/event-stream')
	except Exception as e:
		error_logger.exception("/events/orders failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.route("/api/ltp")
def api_ltp():
	try:
		return jsonify(fetch_ltp())
	except Exception as e:
		error_logger.exception("/api/ltp failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.route('/api/ck')
def api_ck():
	"""Return CK dashboard data (symbols and 15m RSI).

	Uses ltp_service.get_ck_data() which will compute/return RSI when available.
	"""
	try:
		from ltp_service import get_ck_data  # type: ignore
		res = get_ck_data()
		if 'error' in res:
			return jsonify({"error": res.get('error')}), 500
		return jsonify(res)
	except Exception as e:
		error_logger.exception("/api/ck failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.route('/api/vcp')
def api_vcp():
	"""Return VCP (Volatility Contraction Pattern) breakout analysis.
	
	VCP is a technical pattern showing contracting volatility followed by breakout.
	Uses 15m candlestick data to detect patterns (deterministic, non-repainting).
	
	Response includes:
	- stage: Broken Out, Breakout Ready / At Level, or Consolidating
	- consolidation_low/high: Price range during contraction
	- breakout_level: Price level to watch for breakout
	- distance_to_breakout: % distance from current price to breakout level
	- num_contractions: Number of detected volatility contractions
	- volatility_trend: Decreasing or Increasing
	"""
	try:
		from ltp_service import get_vcp_data  # type: ignore
		res = get_vcp_data()
		if 'error' in res:
			return jsonify({"error": res.get('error')}), 500
		return jsonify(res)
	except Exception as e:
		error_logger.exception("/api/vcp failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.route('/api/refresh/averages', methods=['POST'])
def api_refresh_averages():
	"""Manually trigger a refresh of all daily moving averages from database.
	
	This endpoint forces an immediate refresh of EMA5, EMA8, EMA10, EMA20, EMA50, 
	EMA100, and EMA200 from the ohlcv_data table in PostgreSQL.
	
	Response includes:
	- status: "success" or "error"
	- symbols_requested: Number of symbols requested
	- symbols_cached: Number of symbols with cached data
	- symbols_with_data: Number of symbols with actual EMA values computed
	- last_updated: ISO format timestamp
	"""
	try:
		access_logger.info("POST /api/refresh/averages from %s", request.remote_addr)
		from ltp_service import manual_refresh_all_averages  # type: ignore
		res = manual_refresh_all_averages()
		if 'error' in res:
			error_logger.error("Average refresh failed: %s", res.get('error'))
			return jsonify(res), 500
		return jsonify(res)
	except Exception as e:
		error_logger.exception("/api/refresh/averages failed: %s", e)
		return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/user-support', methods=['GET', 'POST'])
def api_user_support():
	"""Save or fetch user support levels from database."""
	from pgAdmin_database.db_connection import pg_cursor
	
	if request.method == 'POST':
		try:
			data = request.get_json()
			symbol = data.get('symbol')
			support_price = data.get('support_price')
			
			if not symbol:
				return jsonify({"error": "Missing symbol"}), 400
			
			with pg_cursor() as (cur, conn):
				# Create table if it doesn't exist
				cur.execute("""
					CREATE TABLE IF NOT EXISTS user_support_levels (
						id SERIAL PRIMARY KEY,
						symbol TEXT UNIQUE NOT NULL,
						support_price NUMERIC,
						updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
					)
				""")
				
				# Upsert the support level
				cur.execute("""
					INSERT INTO user_support_levels (symbol, support_price)
					VALUES (%s, %s)
					ON CONFLICT (symbol) DO UPDATE
					SET support_price = EXCLUDED.support_price, updated_at = CURRENT_TIMESTAMP
				""", (symbol, support_price if support_price else None))
				
				conn.commit()
			
			access_logger.info("/api/user-support POST: symbol=%s, support_price=%s", symbol, support_price)
			return jsonify({"success": True, "symbol": symbol, "support_price": support_price})
		except Exception as e:
			error_logger.exception("/api/user-support POST failed: %s", e)
			return jsonify({"error": str(e)}), 500
	
	else:  # GET
		try:
			with pg_cursor() as (cur, conn):
				cur.execute("SELECT symbol, support_price FROM user_support_levels")
				rows = cur.fetchall()
				result = {row[0]: row[1] for row in rows}
			
			return jsonify(result)
		except Exception as e:
			error_logger.exception("/api/user-support GET failed: %s", e)
			return jsonify({})

@app.post("/api/major-support")
@app.get("/api/major-support")
def api_major_support():
	"""Save or fetch major support levels from database."""
	from pgAdmin_database.db_connection import pg_cursor
	
	if request.method == 'POST':
		try:
			data = request.get_json()
			symbol = data.get('symbol')
			major_support = data.get('major_support')
			
			if not symbol:
				return jsonify({"error": "Missing symbol"}), 400
			
			with pg_cursor() as (cur, conn):
				# Create table if it doesn't exist
				cur.execute("""
					CREATE TABLE IF NOT EXISTS major_support_levels (
						id SERIAL PRIMARY KEY,
						symbol TEXT UNIQUE NOT NULL,
						major_support NUMERIC,
						updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
					)
				""")
				
				# Upsert the major support level
				cur.execute("""
					INSERT INTO major_support_levels (symbol, major_support)
					VALUES (%s, %s)
					ON CONFLICT (symbol) DO UPDATE
					SET major_support = EXCLUDED.major_support, updated_at = CURRENT_TIMESTAMP
				""", (symbol, major_support if major_support else None))
				
				conn.commit()
			
			access_logger.info("/api/major-support POST: symbol=%s, major_support=%s", symbol, major_support)
			return jsonify({"success": True, "symbol": symbol, "major_support": major_support})
		except Exception as e:
			error_logger.exception("/api/major-support POST failed: %s", e)
			return jsonify({"error": str(e)}), 500
	
	else:  # GET
		try:
			with pg_cursor() as (cur, conn):
				cur.execute("SELECT symbol, major_support FROM major_support_levels")
				rows = cur.fetchall()
				result = {row[0]: row[1] for row in rows}
			
			return jsonify(result)
		except Exception as e:
			error_logger.exception("/api/major-support GET failed: %s", e)
			return jsonify({})

@app.get("/api/orderbook")
def api_orderbook():
	"""Return the active/open broker order book directly from Kite."""
	try:
		kite = get_kite()
		orders = []
		try:
			orders = kite.orders() or []
		except Exception as oe:
			error_logger.error("orders() failed: %s", oe)
			orders = []
		# Filter to active/open statuses
		active_status = {"OPEN", "PENDING", "OPEN_PENDING", "TRIGGER_PENDING"}
		active = [o for o in orders if (o.get('status') or '').upper() in active_status]
		return jsonify({"count": len(active), "orders": active})
	except Exception as e:
		error_logger.exception("/api/orderbook failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.post("/api/gtt/recreate")
def api_gtt_recreate():
	"""Fetch current GTT orders then (re)place stop-loss GTTs per symbol.

	JSON body fields:
	  symbols: optional list of symbols; default: all symbols in _orders_store
	  sl_pct: stop-loss percent below ref LTP (default 0.05)
	  force: if true, always place a new GTT even if one exists

	Returns summary per symbol.
	"""
	try:
		payload = request.get_json(silent=True) or {}
		symbols_req = payload.get('symbols') or []
		if isinstance(symbols_req, str):
			symbols_req = [symbols_req]
		force = bool(payload.get('force', False))
		sl_pct = float(payload.get('sl_pct', 0.05))
		kite = get_kite()
		# current gtt book
		gtts = []
		try:
			gtts = _fetch_gtts_with_timeout(kite, timeout=2.0) or []
		except Exception as ge:
			error_logger.error("gtt() failed: %s", ge)
			gtts = []
		gtt_by_symbol = {}
		for g in gtts:
			ts = g.get('tradingsymbol') or g.get('symbol')
			if not ts: continue
			gtt_by_symbol.setdefault(ts, []).append(g)
		with _orders_lock:
			all_syms = {rec.get('symbol') for rec in _orders_store.values() if rec.get('symbol')}
		if symbols_req:
			symbols = [s.upper() for s in symbols_req if isinstance(s, str)]
		else:
			symbols = sorted(all_syms)
		if not symbols:
			return jsonify({"error": "No symbols available for GTT recreation"}), 400
		# fetch LTPs via data service (preferred) then fallback to quote
		data_map = {}
		try:
			data_map = fetch_ltp().get('data', {})
		except Exception:
			data_map = {}
		quote_map = {}
		try:
			quote_map = kite.quote([f"NSE:{s}" for s in symbols]) or {}
		except Exception as qe:
			error_logger.error("quote() failed during gtt recreate: %s", qe)
			quote_map = {}
		inst_csv = os.path.join(REPO_ROOT, os.getenv('INSTRUMENTS_CSV', os.path.join('Csvs','instruments.csv')))
		ticks = _load_tick_sizes(inst_csv)
		results = []
		for sym in symbols:
			ltp = None
			try:
				if sym in data_map and isinstance(data_map[sym].get('last_price'), (int,float)):
					ltp = data_map[sym].get('last_price')
			except Exception as e:
				error_logger.debug("Silent exception ignored: %s", e)
			if not isinstance(ltp, (int,float)) or ltp <= 0:
				q = quote_map.get(f"NSE:{sym}") or {}
				v = q.get('last_price')
				if isinstance(v, (int,float)) and v>0:
					ltp = v
			if not isinstance(ltp, (int,float)) or ltp <= 0:
				results.append({"symbol": sym, "error": "No valid LTP"})
				continue
			existing_sell_gtt_ids = []
			for g in gtt_by_symbol.get(sym, []):
				try:
					for o in (g.get('orders') or []):
						if (o.get('transaction_type') or '').upper() == 'SELL':
							existing_sell_gtt_ids.append(g.get('id') or g.get('trigger_id'))
				except Exception as e:
					error_logger.debug("Silent exception ignored: %s", e)
			placed = None
			trigger_price = None
			qty = None
			# derive quantity from order store if possible
			with _orders_lock:
				for orec in _orders_store.values():
					if orec.get('symbol') == sym:
						qty = orec.get('quantity')
						break
			if qty is None:
				qty = 1  # fallback minimal
			should_place = force or not existing_sell_gtt_ids
			if should_place:
				try:
					# Place bracket OCO with target +7.5% and stop -sl_pct
					placed_id = _place_bracket_oco_gtt(kite, sym, int(qty), ref_price=ltp, target_pct=0.075, sl_pct=sl_pct)
					trigger_price = round(ltp * (1 - sl_pct), 2)
					placed = placed_id
					# update local order store gtt id if matching symbol without gtt
					with _orders_lock:
						for oid, orec in _orders_store.items():
							if orec.get('symbol') == sym and not orec.get('gtt_id'):
								orec['gtt_id'] = placed_id
					# ensure trailing manager state (OCO bracket with target +7.5%)
					_start_trailing(kite, sym, int(qty), sl_pct, ltp=ltp, tick=ticks.get(sym, 0.05), gtt_id=placed_id, gtt_type='oco', target_pct=0.075)
				except Exception as pe:
					results.append({"symbol": sym, "error": f"Place failed: {pe}"})
					continue
			results.append({
				"symbol": sym,
				"ltp": ltp,
				"existing_sell_gtt_ids": existing_sell_gtt_ids,
				"new_gtt_id": placed,
				"sl_pct": sl_pct,
				"trigger_price": trigger_price,
				"quantity": qty,
				"action": "placed" if placed else "skipped"
			})
		return jsonify({"count": len(results), "results": results})
	except Exception as e:
		error_logger.exception("/api/gtt/recreate failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.post("/api/trailing/start")
def api_start_trailing():
	"""Start trailing for an existing GTT order that wasn't placed through the webapp."""
	try:
		payload = request.get_json(silent=True) or {}
		symbol = (payload.get('symbol') or '').strip().upper()
		gtt_id = payload.get('gtt_id')
		qty = int(payload.get('qty', 0))
		stop_price = float(payload.get('stop_price', 0))
		gtt_type = payload.get('gtt_type', 'single')  # 'oco' or 'single'
		target_price = payload.get('target_price')
		
		if not symbol:
			return jsonify({"error": "symbol is required"}), 400
		if not gtt_id:
			return jsonify({"error": "gtt_id is required"}), 400
		if qty <= 0:
			return jsonify({"error": "qty must be positive"}), 400
		if stop_price <= 0:
			return jsonify({"error": "stop_price must be positive"}), 400
		
		order_logger.info("TRAIL_START_REQ symbol=%s gtt_id=%s qty=%d stop=%.2f type=%s from=%s", 
						  symbol, gtt_id, qty, stop_price, gtt_type, request.remote_addr)
		
		kite = get_kite()
		
		# Get current LTP for the symbol
		qd = kite.quote([f"NSE:{symbol}"]) or {}
		q = qd.get(f"NSE:{symbol}") or {}
		ltp = q.get('last_price')
		if not isinstance(ltp, (int, float)) or ltp <= 0:
			return jsonify({"error": "Failed to get LTP for symbol"}), 400
		
		# Calculate sl_pct from current stop and LTP
		# sl_pct = how far below LTP the stop should trail
		sl_pct = (ltp - stop_price) / ltp if ltp > stop_price else 0.05
		sl_pct = max(0.01, min(sl_pct, 0.20))  # Clamp between 1% and 20%
		
		# Calculate target_pct if OCO
		target_pct = 0.075  # default
		if gtt_type == 'oco' and target_price and float(target_price) > 0:
			target_pct = (float(target_price) - ltp) / ltp if ltp > 0 else 0.075
			target_pct = max(0.02, min(target_pct, 0.50))  # Clamp between 2% and 50%
		
		# Get tick size for the symbol
		inst_csv = os.path.join(REPO_ROOT, os.getenv('INSTRUMENTS_CSV', os.path.join('Csvs','instruments.csv')))
		ticks = _load_tick_sizes(inst_csv)
		tick = ticks.get(symbol) or _fallback_tick_by_price(ltp)
		
		# Start trailing - use stop_price as initial trigger so it knows where the GTT currently is
		_start_trailing(kite, symbol, qty, sl_pct, ltp=ltp, tick=tick, gtt_id=str(gtt_id), gtt_type=gtt_type, target_pct=target_pct, initial_trigger=stop_price)
		
		trail_logger.info("TRAIL_STARTED symbol=%s gtt_id=%s qty=%d sl_pct=%.3f ltp=%.2f type=%s", 
						  symbol, gtt_id, qty, sl_pct, ltp, gtt_type)
		
		return jsonify({
			"status": "ok",
			"symbol": symbol,
			"gtt_id": gtt_id,
			"qty": qty,
			"sl_pct": round(sl_pct, 4),
			"ltp": ltp,
			"gtt_type": gtt_type,
			"target_pct": round(target_pct, 4) if gtt_type == 'oco' else None,
			"message": f"Trailing started for {symbol}"
		})
	except Exception as e:
		error_logger.exception("/api/trailing/start failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.get("/api/trades")
def api_trades():
	"""Return recent executed trades and current GTT (GTC) orders for reference."""
	try:
		kite = get_kite()
		trades = []
		try:
			trades = kite.trades() or []
		except Exception as te:
			error_logger.error("trades() failed: %s", te)
			trades = []
		# Fetch GTTs to correlate stops/targets if needed (non-blocking)
		gtts = []
		try:
			gtts = _fetch_gtts_with_timeout(kite, timeout=2.0) or []
		except Exception as ge:
			error_logger.error("gtt() failed: %s", ge)
			gtts = []
		# Minimal shape: return raw trades with a symbol-level gtt summary
		gtt_summary = {}
		for g in gtts:
			try:
				ts = g.get('tradingsymbol') or g.get('symbol')
				if not ts:
					continue
				entry = gtt_summary.get(ts) or {"count": 0, "items": []}
				entry["count"] += 1
				entry["items"].append({
					"id": g.get('id') or g.get('trigger_id'),
					"status": g.get('status'),
					"type": g.get('type') or g.get('trigger_type'),
					"trigger_values": g.get('trigger_values') or [],
					"orders": g.get('orders') or [],
				})
				gtt_summary[ts] = entry
			except Exception as e:
				error_logger.debug("Silent exception ignored: %s", e)
		return jsonify({"count": len(trades), "trades": trades, "gtt_by_symbol": gtt_summary})
	except Exception as e:
		error_logger.exception("/api/trades failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.get("/api/positions")
def api_positions():
	"""Return current positions (day and net) from Kite."""
	try:
		kite = get_kite()
		pos = {}
		try:
			pos = kite.positions() or {}
		except Exception as pe:
			error_logger.error("positions() failed: %s", pe)
			pos = {}
		return jsonify(pos)
	except Exception as e:
		error_logger.exception("/api/positions failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.get("/api/holdings")
def api_holdings():
	"""Return current holdings from Kite."""
	try:
		kite = get_kite()
		holdings = []
		try:
			holdings = kite.holdings() or []
		except Exception as he:
			error_logger.error("holdings() failed: %s", he)
			holdings = []
		# Enrich with LTP and Holding PnL using latest prices
		try:
			# Build symbol list
			symbols = [h.get('tradingsymbol') or h.get('symbol') for h in holdings if (h.get('tradingsymbol') or h.get('symbol'))]
			# Prefer app's data service for LTP
			ltp_map = {}
			try:
				data = fetch_ltp() or {}
				for s, d in (data.get('data') or {}).items():
					try:
						lp = d.get('last_price')
						if isinstance(lp, (int, float)):
							ltp_map[s] = float(lp)
					except Exception as e:
						error_logger.debug("Silent exception ignored: %s", e)
			except Exception:
				ltp_map = {}
			# Fallback to broker quotes for any missing symbols
			missing = [s for s in symbols if s not in ltp_map]
			if missing:
				try:
					qd = kite.quote([f"NSE:{s}" for s in missing]) or {}
					for s in missing:
						q = qd.get(f"NSE:{s}") or {}
						lp = q.get('last_price')
						if isinstance(lp, (int, float)):
							ltp_map[s] = float(lp)
				except Exception as qe:
					error_logger.error("quote() failed in /api/holdings LTP: %s", qe)
			total_pnl = 0.0
			for h in holdings:
				try:
					s = h.get('tradingsymbol') or h.get('symbol')
					# Use opening_quantity (includes T+1) or sum of quantity + t1_quantity
					qty = h.get('opening_quantity')
					if qty is None:
						settled_qty = h.get('quantity') or 0
						t1_qty = h.get('t1_quantity') or 0
						if isinstance(settled_qty, (int, float)) and isinstance(t1_qty, (int, float)):
							qty = int(settled_qty) + int(t1_qty)
					qty = int(qty) if isinstance(qty, (int, float, str)) and str(qty).strip() != '' else None
					# Store total_quantity for frontend use
					h['total_quantity'] = qty
					avg = h.get('average_price') if 'average_price' in h else h.get('average')
					avgf = float(avg) if isinstance(avg, (int, float, str)) and str(avg).strip() != '' else None
					ltp = ltp_map.get(s)
					if isinstance(ltp, (int, float)):
						h['last_price'] = float(ltp)
					# Compute Holding PnL using total quantity (including T+1)
					pnl = None
					pnl_pct = None
					if qty is not None and avgf is not None and isinstance(ltp, (int, float)):
						pnl = (float(ltp) - avgf) * qty
						if avgf:
							pnl_pct = ((float(ltp) / avgf) - 1.0) * 100.0
					if pnl is not None:
						total_pnl += float(pnl)
					h['holding_pnl'] = pnl
					h['holding_pnl_pct'] = pnl_pct
				except Exception:
					# Leave fields unset on error
					pass
			return jsonify({
				"count": len(holdings),
				"total_holding_pnl": total_pnl,
				"holdings": holdings
			})
		except Exception:
			# If enrichment fails, return raw holdings
			return jsonify({"count": len(holdings), "holdings": holdings})
	except Exception as e:
		error_logger.exception("/api/holdings failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.get("/api/gtt")
def api_gtt_list():
		"""Return current GTT (GTC) orders grouped by tradingsymbol."""
		try:
			kite = get_kite()
			gtts = []
			try:
				if hasattr(kite, 'get_gtts'):
					gtts = kite.get_gtts() or []
			except Exception as ge:
				error_logger.error("gtt() failed: %s", ge)
				gtts = []
			# group by tradingsymbol
			grouped: dict[str, list] = {}
			for g in gtts:
				ts = g.get('tradingsymbol') or g.get('symbol')
				if not ts:
					continue
				grouped.setdefault(ts, []).append({
					'id': g.get('id') or g.get('trigger_id'),
					'status': g.get('status'),
					'type': g.get('type') or g.get('trigger_type'),
					'trigger_values': g.get('trigger_values') or [],
					'orders': g.get('orders') or [],
				})
			return jsonify({
				'count': len(gtts),
				'by_symbol': grouped,
				'items': gtts,
			})
		except Exception as e:
			error_logger.exception("/api/gtt failed: %s", e)
			return jsonify({"error": str(e)}), 500

# ============================================================================
# AUTHENTICATION ENDPOINTS (Phase 3B)
# ============================================================================

@app.post("/api/auth/login")
def api_login():
	"""
	Login endpoint - authenticates user with email and password.
	
	Request body:
		{
			"email": "user@example.com",
			"password": "secure_password",
			"csrf_token": "..."
		}
	
	Response:
		{
			"success": true,
			"user": {
				"user_id": 1,
				"username": "john",
				"email": "john@example.com",
				"role": "TRADER"
			}
		}
	"""
	try:
		# Validate CSRF token
		try:
			csrf_token = request.get_json(silent=True, force=True).get('csrf_token')
			if csrf_token:
				validate_csrf_token(csrf_token)
		except CSRFError as e:
			order_logger.warning("Login CSRF validation failed: %s", e)
			return jsonify({"error": "CSRF validation failed"}), 403
		
		payload = request.get_json(force=True)
		email = payload.get('email', '').strip()
		password = payload.get('password', '')
		
		# Validate input
		if not email or not password:
			return jsonify({"error": "Email and password required"}), 400
		
		# TODO: Query user from database
		# This is a placeholder - in production, query user from DB
		# user = db.query_user_by_email(email)
		# if not user or not verify_password(password, user.password_hash):
		#     return jsonify({"error": "Invalid credentials"}), 401
		
		# For now, create a test user (remove in production)
		if email == 'demo@example.com' and password == 'DemoPass123!':
			from datetime import datetime
			user = User(
				user_id=1,
				username='demo',
				email='demo@example.com',
				role=UserRole.TRADER,
				created_at=datetime.now(),
			)
			login_user(user)
			order_logger.info("User logged in: %s", email)
			return jsonify({
				"success": True,
				"user": user.to_dict()
			}), 200
		
		return jsonify({"error": "Invalid credentials"}), 401
		
	except Exception as e:
		error_logger.exception("/api/auth/login failed: %s", e)
		return jsonify({"error": str(e)}), 500


@app.post("/api/auth/logout")
@require_login()
def api_logout():
	"""
	Logout endpoint - clears user session.
	
	Request body:
		{
			"csrf_token": "..."
		}
	
	Response:
		{
			"success": true
		}
	"""
	try:
		# Validate CSRF token
		try:
			csrf_token = request.get_json(silent=True, force=True).get('csrf_token')
			if csrf_token:
				validate_csrf_token(csrf_token)
		except CSRFError as e:
			order_logger.warning("Logout CSRF validation failed: %s", e)
			return jsonify({"error": "CSRF validation failed"}), 403
		
		user = get_current_user()
		logout_user()
		order_logger.info("User logged out: %s", user.username if user else "unknown")
		
		return jsonify({"success": True}), 200
		
	except Exception as e:
		error_logger.exception("/api/auth/logout failed: %s", e)
		return jsonify({"error": str(e)}), 500


@app.get("/api/auth/me")
@require_login()
def api_auth_me():
	"""
	Get current user endpoint - returns authenticated user info.
	
	Response:
		{
			"user_id": 1,
			"username": "john",
			"email": "john@example.com",
			"role": "TRADER",
			"created_at": "2026-01-14T10:00:00"
		}
	"""
	try:
		user = get_current_user()
		if not user:
			return jsonify({"error": "Not authenticated"}), 401
		
		return jsonify(user.to_dict()), 200
		
	except Exception as e:
		error_logger.exception("/api/auth/me failed: %s", e)
		return jsonify({"error": str(e)}), 500


@app.get("/api/auth/csrf")
def api_get_csrf_token():
	"""
	Get CSRF token endpoint - returns token for form submissions.
	
	Response:
		{
			"csrf_token": "..."
		}
	"""
	try:
		token = generate_csrf_token()
		return jsonify({"csrf_token": token}), 200
		
	except Exception as e:
		error_logger.exception("/api/auth/csrf failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.route("/")
def index():
	# Ensure we are listening to order updates
	try:
		_ensure_order_listener()
	except Exception as e:
		error_logger.debug("Silent exception ignored: %s", e)
	return render_template("index.html")

@app.get("/health")
def health_check():
	"""Health check endpoint for monitoring and load balancers.
	
	Returns:
	- status: 'healthy' or 'degraded'
	- timestamp: ISO 8601 timestamp
	- checks: dict with individual check results
	
	HTTP Status:
	- 200 if all checks pass (healthy)
	- 503 if any critical check fails (degraded)
	"""
	try:
		checks = {
			'database': False,
			'broker_api': False,
			'data_service': False,
		}
		
		# Check database connectivity
		try:
			from ltp_service import pg_cursor
			with pg_cursor() as (cur, conn):
				cur.execute("SELECT 1")
			checks['database'] = True
		except Exception as e:
			error_logger.warning("Health check: database connectivity failed: %s", e)
		
		# Check broker API connectivity
		try:
			kite = get_kite()
			if kite:
				# Try to get profile (lightweight check)
				kite.profile()
				checks['broker_api'] = True
		except Exception as e:
			error_logger.warning("Health check: broker API check failed: %s", e)
		
		# Check data service (cache populated)
		try:
			from ltp_service import _sma15m_cache
			checks['data_service'] = len(_sma15m_cache) > 0
		except Exception as e:
			error_logger.warning("Health check: data service check failed: %s", e)
		
		# Consider healthy if database and broker API are working
		healthy = checks['database'] and checks['broker_api']
		
		response = {
			'status': 'healthy' if healthy else 'degraded',
			'checks': checks,
			'timestamp': datetime.utcnow().isoformat() + 'Z',
		}
		
		status_code = 200 if healthy else 503
		return jsonify(response), status_code
	
	except Exception as e:
		error_logger.exception("Health check endpoint failed: %s", e)
		return jsonify({
			'status': 'error',
			'error': str(e),
			'timestamp': datetime.utcnow().isoformat() + 'Z',
		}), 500

@app.route("/export/ltp.csv")
def export_ltp_csv():
	try:
		data = fetch_ltp()
		rows = [
			"symbol,last_price,last_close_15m,sma200_15m,sma50_15m,ratio_15m_50_200,rank_gm,pct_vs_15m_sma50,pct_vs_daily_sma20,drawdown_15m_200_pct,days_since_golden_cross,daily_sma20,daily_sma50,daily_sma200,daily_ratio_50_200,volume_ratio_d5_d200"
		]
		for sym, info in data.get("data", {}).items():
			rows.append(
				f"{sym},{info.get('last_price','')},{info.get('last_close','')},{info.get('sma200_15m','')},{info.get('sma50_15m','')},{info.get('ratio_15m_50_200','')},{info.get('rank_gm','')},{info.get('pct_vs_15m_sma50','')},{info.get('pct_vs_daily_sma20','')},{info.get('drawdown_15m_200_pct','')},{info.get('days_since_golden_cross','')},{info.get('daily_sma20','')},{info.get('daily_sma50','')},{info.get('daily_sma200','')},{info.get('volume_ratio_d5_d200','')}"
			)
		csv_content = "\n".join(rows) + "\n"
		return Response(csv_content, mimetype="text/csv", headers={
			"Content-Disposition": "attachment; filename=ltp.csv"
		})
	except Exception as e:
		error_logger.exception("/export/ltp.csv failed: %s", e)
		return jsonify({"error": str(e)}), 500

def _load_tick_sizes(csv_path: str) -> dict:
	"""Load tick sizes from instruments CSV. Fallback to 0.05 when missing."""
	result = {}
	try:
		import csv as _csv
		if os.path.exists(csv_path):
			with open(csv_path, 'r', newline='', encoding='utf-8') as f:
				reader = _csv.DictReader(f)
				for row in reader:
					ts = row.get('tradingsymbol')
					tick = row.get('tick_size') or row.get('tick')
					if ts and tick:
						try:
							result[ts] = float(tick)
						except Exception as e:
							error_logger.debug("Silent exception ignored: %s", e)
	except Exception as e:
		error_logger.debug("Silent exception ignored: %s", e)
	return result

def _round_to_tick(price: float, tick: float) -> float:
	if not tick:
		tick = 0.05
	return round(round(price / tick) * tick, 2)

def _fallback_tick_by_price(ltp: float) -> float:
	"""Derive tick size when instrument CSV lacks data.

	Rule: use 0.1 for lower-priced stocks, otherwise 0.5.
	Threshold chosen as 100 by convention; adjust if needed.
	"""
	try:
		if not isinstance(ltp, (int, float)) or ltp <= 0:
			return 0.1
		return 0.1 if ltp < 100 else 0.5
	except Exception:
		return 0.1

def _place_sl_gtt(kite, symbol: str, qty: int, ref_price: float, sl_pct: float = 0.05):
	"""Place a single-leg GTT stop-loss (sell) at sl_pct below reference price.

	Returns GTT ID. Note: Kite doesn't support server-managed trailing; this is static.
	"""
	if not hasattr(kite, 'place_gtt'):
		raise RuntimeError('GTT not supported by current Kite client')
	# Ensure trigger and limit adhere to instrument tick size
	inst_csv = os.path.join(REPO_ROOT, os.getenv('INSTRUMENTS_CSV', os.path.join('Csvs','instruments.csv')))
	ticks = _load_tick_sizes(inst_csv)
	tick = ticks.get(symbol)
	if not tick:
		tick = _fallback_tick_by_price(ref_price)
	trigger = _round_to_tick(ref_price * (1 - sl_pct), tick)
	limit = trigger
	if trigger <= 0 or limit <= 0:
		raise ValueError('Computed GTT prices invalid')
	# Use a sell LIMIT at trigger price; acts like stop-limit when triggered
	resp = kite.place_gtt(
		trigger_type=kite.GTT_TYPE_SINGLE,
		tradingsymbol=symbol,
		exchange='NSE',
		trigger_values=[trigger],
		last_price=ref_price,
		orders=[{
			"transaction_type": kite.TRANSACTION_TYPE_SELL,
			"quantity": qty,
			"product": kite.PRODUCT_CNC,
			"order_type": kite.ORDER_TYPE_LIMIT,
			"price": limit,
		}]
	)
	return resp.get('trigger_id') or resp.get('id')

def _place_bracket_oco_gtt(kite, symbol: str, qty: int, ref_price: float, target_pct: float = 0.075, sl_pct: float = 0.05):
	"""Place a two-leg OCO GTT: profit target (SELL LIMIT) and stop-loss (SELL LIMIT).

	Target at +target_pct above ref_price; Stop at -sl_pct below ref_price.
	Prices are snapped to instrument tick size.
	Returns GTT ID.
	"""
	if not hasattr(kite, 'place_gtt'):
		raise RuntimeError('GTT not supported by current Kite client')
	inst_csv = os.path.join(REPO_ROOT, os.getenv('INSTRUMENTS_CSV', os.path.join('Csvs','instruments.csv')))
	ticks = _load_tick_sizes(inst_csv)
	tick = ticks.get(symbol) or _fallback_tick_by_price(ref_price)
	target_px = _round_to_tick(ref_price * (1 + target_pct), tick)
	stop_px = _round_to_tick(ref_price * (1 - sl_pct), tick)
	if target_px <= 0 or stop_px <= 0:
		raise ValueError('Computed GTT prices invalid')
	resp = kite.place_gtt(
		trigger_type=kite.GTT_TYPE_OCO,
		tradingsymbol=symbol,
		exchange='NSE',
		trigger_values=[stop_px, target_px],
		last_price=ref_price,
		orders=[
			{
				"transaction_type": kite.TRANSACTION_TYPE_SELL,
				"quantity": qty,
				"product": kite.PRODUCT_CNC,
				"order_type": kite.ORDER_TYPE_LIMIT,
				"price": stop_px,
			},
			{
				"transaction_type": kite.TRANSACTION_TYPE_SELL,
				"quantity": qty,
				"product": kite.PRODUCT_CNC,
				"order_type": kite.ORDER_TYPE_LIMIT,
				"price": target_px,
			},
		]
	)
	return resp.get('trigger_id') or resp.get('id')

# ---------- Trailing GTT manager (best-effort client-side) ---------- 
# Now keyed by gtt_id to support multiple GTTs per symbol
_trail_lock = None
_trail_state = {}  # key: gtt_id, value: {symbol, qty, sl_pct, tick, trigger, gtt_type, target_pct}
_trail_thread_started = False

def _trail_lock_obj():
	global _trail_lock
	if _trail_lock is None:
		import threading
		_trail_lock = threading.RLock()
	return _trail_lock

def _ensure_trailer_thread(kite):
	"""Starts a background thread that raises SL GTT as price rises (no server-side trailing)."""
	global _trail_thread_started
	if _trail_thread_started:
		return
	_trail_thread_started = True
    
	# Bootstrap: import existing active GTTs from broker into our trail state so
	# we can manage GTTs that were created before this process started.
	def _bootstrap_trailing_from_broker():
		if not hasattr(kite, 'get_gtts'):
			return
		try:
			resp = kite.get_gtts() or {}
		except Exception:
			# If broker query fails, skip bootstrap silently
			error_logger.debug("Failed to fetch broker GTTs for bootstrap", exc_info=True)
			return
		items = resp.get('items') or resp.get('triggers') or []
		for g in items:
			try:
				if (g.get('status') or '').lower() != 'active':
					continue
				cond = g.get('condition') or {}
				sym = cond.get('tradingsymbol') or g.get('tradingsymbol')
				if not sym:
					continue
				# Determine stop trigger (first trigger value)
				trigs = cond.get('trigger_values') or []
				if not trigs:
					continue
				stop_trig = float(trigs[0])
				last_price = float(cond.get('last_price') or g.get('last_price') or stop_trig)
				# estimate sl_pct from last_price and stop trigger
				sl_pct = (last_price - stop_trig) / last_price if last_price and last_price > 0 else 0.05
				# qty from orders
				qty = 0
				for o in (g.get('orders') or []):
					if (o.get('transaction_type') or '').upper() == 'SELL':
						qty = int(o.get('quantity') or qty or 0)
				# pick tick for symbol
				try:
					inst_csv = os.path.join(REPO_ROOT, os.getenv('INSTRUMENTS_CSV', os.path.join('Csvs','instruments.csv')))
					ticks = _load_tick_sizes(inst_csv)
					tick = ticks.get(sym) or _fallback_tick_by_price(last_price)
				except Exception:
					tick = 0.05
				with _trail_lock_obj():
					if str(g.get('id') or g.get('trigger_id')) in _trail_state:
						continue
					# register trailing state
					_start_trailing(kite, sym, int(qty or 1), float(sl_pct), ltp=last_price, tick=tick, gtt_id=str(g.get('id') or g.get('trigger_id')), gtt_type=('oco' if (g.get('type') or '').lower() in ('two-leg','oco') else 'single'), initial_trigger=stop_trig)
			except Exception:
				# don't let bootstrap failures stop the app
				error_logger.debug("Trailing bootstrap failed for a trigger", exc_info=True)

	# run bootstrap once before starting worker
	try:
		_bootstrap_trailing_from_broker()
	except Exception:
		error_logger.debug("Trailing bootstrap runner failed", exc_info=True)
	import threading, time

	def worker():
		while True:
			try:
				with _trail_lock_obj():
					items = list(_trail_state.items())  # items: [(gtt_id, state_dict), ...]
				if not items:
					time.sleep(30)
					continue
				# Batch quote request - get unique symbols from all tracked GTTs
				symbols_to_quote = list(set(st.get('symbol') for _, st in items if st.get('symbol')))
				syms = [f"NSE:{s}" for s in symbols_to_quote]
				qd = {}
				try:
					qd = kite.quote(syms) or {}
				except Exception as e:
					trail_logger.error("TRAIL_QUOTE_FAIL %s", e)
					time.sleep(30)
					continue
				# Also try LTP from our data service to prefer cached/universe prices
				ltp_data = None
				try:
					ltp_data = fetch_ltp().get('data', {})
				except Exception:
					ltp_data = None
				for gtt_id, st in items:
					sym = st.get('symbol')
					if not sym:
						continue
					q = qd.get(f"NSE:{sym}") or {}
					ltp = q.get('last_price')
					# prefer app data LTP when available
					try:
						if ltp_data and sym in ltp_data and isinstance(ltp_data[sym].get('last_price'), (int,float)):
							ltp = ltp_data[sym].get('last_price')
					except Exception as e:
						error_logger.debug("Silent exception ignored: %s", e)
					if not isinstance(ltp, (int,float)) or ltp <= 0:
						continue
					
					# Trailing logic: only modify GTT if gap between LTP and stop exceeds 5%
					# If gap is under 5%, do nothing. If gap > 5%, bring stop back to 5% below LTP.
					current_trigger = st.get('trigger')
					TRAIL_THRESHOLD = 0.05  # Fixed 5% threshold for trailing
					
					if current_trigger is None or current_trigger <= 0:
						# No trigger set yet, initialize it
						new_trig = _round_to_tick(ltp * (1 - TRAIL_THRESHOLD), st['tick'])
						st['trigger'] = new_trig
						continue
					
					# Calculate current gap: how far is LTP above the stop (as % of LTP)
					current_gap_pct = (ltp - current_trigger) / ltp
					
					# Only modify if gap exceeds 5% threshold
					if current_gap_pct <= TRAIL_THRESHOLD:
						# Gap is within acceptable range, do nothing
						continue
					
					# Gap exceeded 5%, bring stop back to exactly 5% below LTP
					new_trig = _round_to_tick(ltp * (1 - TRAIL_THRESHOLD), st['tick'])
					
					# Safety: never lower the stop, only raise it
					if new_trig <= current_trigger:
						continue
					
					# Log the trailing action
					trail_logger.info("TRAIL_CHECK symbol=%s gtt_id=%s ltp=%.2f current_stop=%.2f gap=%.1f%% new_stop=%.2f", 
						sym, gtt_id, ltp, current_trigger, current_gap_pct*100, new_trig)
					
					# raise SL by modifying/replacing GTT
					try:
						gtt_type = st.get('gtt_type', 'single')  # default to single for backward compat
						# Diagnostic log: show state and available kite methods
						trail_logger.debug("TRAIL_DECIDE symbol=%s gtt_id=%s has_modify=%s gtt_type=%s current_stop=%.2f new_stop=%.2f gap=%.2f ltp=%.2f st=%s",
								sym, st.get('gtt_id'), hasattr(kite, 'modify_gtt'), gtt_type, current_trigger, new_trig, current_gap_pct*100, ltp, {k: st.get(k) for k in ('qty','sl_pct','initial_target','target_pct')})
						if hasattr(kite, 'modify_gtt') and st.get('gtt_id'):
							if gtt_type == 'oco':
								# OCO GTT: update stop trigger but keep target price fixed (don't change it)
								new_target = st.get('initial_target')  # Use original target price
								if not new_target:
									# Fallback if initial_target not set (legacy state)
									target_pct = st.get('target_pct', 0.075)
									new_target = _round_to_tick(ltp * (1 + target_pct), st['tick'])
								resp = kite.modify_gtt(
									trigger_id=st['gtt_id'],
									trigger_type=kite.GTT_TYPE_OCO,
									tradingsymbol=sym,
									exchange='NSE',
									trigger_values=[new_trig, new_target],
									last_price=ltp,
									orders=[
										{
											"transaction_type": kite.TRANSACTION_TYPE_SELL,
											"quantity": st['qty'],
											"product": kite.PRODUCT_CNC,
											"order_type": kite.ORDER_TYPE_LIMIT,
											"price": new_trig,
										},
										{
											"transaction_type": kite.TRANSACTION_TYPE_SELL,
											"quantity": st['qty'],
											"product": kite.PRODUCT_CNC,
											"order_type": kite.ORDER_TYPE_LIMIT,
											"price": new_target,
										},
									]
								)
								trail_logger.info("TRAIL_MODIFY_OCO symbol=%s gtt_id=%s stop=%.2f target=%.2f (fixed)", sym, st.get('gtt_id'), new_trig, new_target)
								trail_logger.debug("TRAIL_MODIFY_RESP symbol=%s gtt_id=%s resp=%s", sym, st.get('gtt_id'), str(resp))
							else:
								# Single-leg GTT
								resp = kite.modify_gtt(
									trigger_id=st['gtt_id'],
									trigger_type=kite.GTT_TYPE_SINGLE,
									tradingsymbol=sym,
									exchange='NSE',
									trigger_values=[new_trig],
									last_price=ltp,
									orders=[{
										"transaction_type": kite.TRANSACTION_TYPE_SELL,
										"quantity": st['qty'],
										"product": kite.PRODUCT_CNC,
										"order_type": kite.ORDER_TYPE_LIMIT,
										"price": new_trig,
									}]
								)
								trail_logger.info("TRAIL_MODIFY symbol=%s gtt_id=%s trigger=%.2f", sym, st.get('gtt_id'), new_trig)
								trail_logger.debug("TRAIL_MODIFY_RESP symbol=%s gtt_id=%s resp=%s", sym, st.get('gtt_id'), str(resp))
							with _trail_lock_obj():
								st['trigger'] = new_trig
						else:
							# replace: delete old and place new
							if st.get('gtt_id') and hasattr(kite, 'delete_gtt'):
								try:
									kite.delete_gtt(st['gtt_id'])
								except Exception as e:
									error_logger.debug("Silent exception ignored: %s", e)
							new_id = _place_sl_gtt(kite, sym, st['qty'], ref_price=ltp, sl_pct=st['sl_pct'])
							trail_logger.debug("TRAIL_REPLACE_NEW symbol=%s new_gtt_id=%s new_trigger=%.2f resp_placeholder=NA", sym, new_id, new_trig)
							with _trail_lock_obj():
								st['gtt_id'] = new_id
								st['trigger'] = new_trig
								st['gtt_type'] = 'single'  # replaced with single-leg
							trail_logger.info("TRAIL_REPLACE symbol=%s gtt_id=%s trigger=%.2f", sym, new_id, new_trig)
					except Exception as e:
						trail_logger.error("TRAIL_UPDATE_FAIL symbol=%s err=%s", sym, e)
				time.sleep(30)
			except Exception as e:
				trail_logger.exception("TRAIL_LOOP_ERR %s", e)
				time.sleep(30)

	threading.Thread(target=worker, name="gtt_trailer", daemon=True).start()

def _start_trailing(kite, symbol: str, qty: int, sl_pct: float, ltp: float, tick: float, gtt_id: str|None, gtt_type: str = 'oco', target_pct: float = 0.075, initial_trigger: float|None = None):
	if not gtt_id:
		trail_logger.warning("Cannot start trailing without gtt_id for %s", symbol)
		return
	with _trail_lock_obj():
		# Use gtt_id as key to support multiple GTTs per symbol
		state = _trail_state.get(str(gtt_id)) or {}
		# Use provided initial_trigger (from existing GTT stop) or compute from LTP
		trigger = initial_trigger if initial_trigger is not None else _round_to_tick(ltp * (1 - sl_pct), tick)
		# Calculate initial target price (stays fixed, not recalculated on modifications)
		initial_target = _round_to_tick(ltp * (1 + target_pct), tick) if gtt_type == 'oco' else None
		state.update({
			'symbol': symbol,  # Store symbol for quote lookups
			'qty': qty,
			'sl_pct': sl_pct,
			'tick': tick,
			'gtt_id': str(gtt_id),
			'trigger': trigger,
			'gtt_type': gtt_type,
			'target_pct': target_pct,
			'initial_target': initial_target,  # Store fixed target price
		})
		_trail_state[str(gtt_id)] = state
		trail_logger.info("TRAIL_REGISTER gtt_id=%s symbol=%s qty=%s sl_pct=%.4f trigger=%.2f gtt_type=%s", str(gtt_id), symbol, qty, sl_pct, trigger or 0.0, gtt_type)
	_ensure_trailer_thread(kite)

# Helper function to log trade entries to the database
def _log_trade_entry(symbol, qty, entry_price, order_id, gtt_id):
	"""Automatically log a trade entry when order is placed."""
	from datetime import datetime
	from pgAdmin_database.db_connection import pg_cursor
	
	try:
		trade_id = f"ENTRY_{order_id}_{int(time.time())}"
		timestamp = datetime.utcnow().isoformat()
		
		with pg_cursor() as (cur, conn):
			# Create table if it doesn't exist
			cur.execute("""
				CREATE TABLE IF NOT EXISTS trade_journal (
					id SERIAL PRIMARY KEY,
					trade_id TEXT UNIQUE,
					symbol TEXT NOT NULL,
					entry_date TIMESTAMP,
					entry_price NUMERIC,
					entry_qty INTEGER,
					exit_date TIMESTAMP,
					exit_price NUMERIC,
					pnl NUMERIC,
					pnl_pct NUMERIC,
					duration NUMERIC,
					strategy TEXT,
					status TEXT,
					notes TEXT,
					order_id TEXT,
					gtt_id TEXT,
					created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
					updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
				)
			""")
			
			# Insert trade entry
			cur.execute("""
				INSERT INTO trade_journal (trade_id, symbol, entry_date, entry_price, entry_qty, status, order_id, gtt_id, notes)
				VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
				ON CONFLICT (trade_id) DO UPDATE SET
					updated_at = CURRENT_TIMESTAMP
			""", (trade_id, symbol, timestamp, entry_price, qty, 'open', order_id, gtt_id, 'Auto-logged from app'))
			
			conn.commit()
			order_logger.info("Trade journal entry created: trade_id=%s symbol=%s qty=%s price=%s", trade_id, symbol, qty, entry_price)
	except Exception as e:
		order_logger.error("Failed to log trade entry: %s", e)
		raise

def _log_trade_exit(order_id, symbol, exit_price, exit_qty, exit_type='completed'):
	"""Automatically log a trade exit when order is completed or GTT triggered."""
	from datetime import datetime
	from pgAdmin_database.db_connection import pg_cursor
	
	try:
		timestamp = datetime.utcnow().isoformat()
		
		with pg_cursor() as (cur, conn):
			# Find the matching entry for this order
			cur.execute("""
				SELECT id, entry_price, entry_qty, entry_date FROM trade_journal 
				WHERE order_id = %s AND status = 'open'
				LIMIT 1
			""", (str(order_id),))
			
			entry_record = cur.fetchone()
			if not entry_record:
				order_logger.warning("No open trade entry found for order_id=%s", order_id)
				return
			
			entry_id, entry_price, entry_qty, entry_date = entry_record
			
			# Ensure all values are float for arithmetic
			entry_price = float(entry_price)
			exit_price = float(exit_price)
			exit_qty = int(exit_qty)
			
			# Calculate PnL
			pnl = (exit_price - entry_price) * exit_qty
			pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
			
			# Calculate duration in hours
			duration_hours = None
			if entry_date:
				from datetime import datetime
				exit_dt = datetime.fromisoformat(timestamp)
				entry_dt = datetime.fromisoformat(entry_date) if isinstance(entry_date, str) else entry_date
				if isinstance(entry_dt, str):
					entry_dt = datetime.fromisoformat(entry_dt)
				duration_hours = (exit_dt - entry_dt).total_seconds() / 3600
			
			# Update trade journal entry with exit data
			cur.execute("""
				UPDATE trade_journal 
				SET exit_date = %s, exit_price = %s, pnl = %s, pnl_pct = %s, 
					duration = %s, status = %s, notes = %s, updated_at = CURRENT_TIMESTAMP
				WHERE id = %s
			""", (timestamp, exit_price, pnl, pnl_pct, duration_hours, 'closed', 
				  f'Auto-logged exit ({exit_type})', entry_id))
			
			conn.commit()
			order_logger.info("Trade exit logged: order_id=%s symbol=%s exit_price=%.2f pnl=%.2f pnl_pct=%.2f", 
							 order_id, symbol, exit_price, pnl, pnl_pct)
	except Exception as e:
		order_logger.error("Failed to log trade exit: %s", e)
		# Don't raise - exit logging should not block main flow

@app.post("/api/order/buy")
@limiter.limit("5 per minute")  # Max 5 buy orders per minute per IP
@csrf_protect  # CSRF token validation
@require_login(required_role=UserRole.TRADER)  # Require trader role
def api_place_buy():
	"""Place a CNC LIMIT buy at LTP with budget INR 10,000 (adjust quantity)."""
	try:
		payload = request.get_json(silent=True) or {}
		
		# ===== INPUT VALIDATION =====
		try:
			symbol = validate_symbol(payload.get('symbol', ''))
		except ValidationError as e:
			order_logger.warning("Symbol validation failed: %s", e)
			return jsonify({"error": f"Invalid symbol: {e}"}), 400
		
		try:
			budget = validate_price(payload.get('budget', 10000), min_price=100, max_price=999999)
		except ValidationError as e:
			order_logger.warning("Budget validation failed: %s", e)
			return jsonify({"error": f"Invalid budget: {e}"}), 400
		
		offset_pct = float(payload.get('offset_pct', 0.001))
		if not (0 <= offset_pct <= 0.1):
			return jsonify({"error": "offset_pct must be between 0 and 0.1"}), 400
		
		# Enable trailing SL by default unless explicitly disabled
		with_tsl = bool(payload.get('tsl', True))
		
		try:
			sl_pct = validate_price(payload.get('sl_pct', 0.05), min_price=0.001, max_price=1.0)
		except ValidationError as e:
			order_logger.warning("SL pct validation failed: %s", e)
			return jsonify({"error": f"Invalid stop loss: {e}"}), 400
		
		use_market = bool(payload.get('market', False))
		# ===== END VALIDATION =====
		
		# Log authenticated user
		user = get_current_user()
		order_logger.info("ORDER_REQ symbol=%s budget=%.2f offset=%.4f market=%s tsl=%s sl_pct=%.3f user=%s from=%s", symbol, budget, offset_pct, use_market, with_tsl, sl_pct, user.username if user else "anonymous", request.remote_addr)
		kite = get_kite()

		# Enforce cooldown after a stop-trigger for this symbol (default 3 minutes)
		allowed, remaining = _can_enter_symbol(symbol, cooldown_seconds=180)
		if not allowed:
			order_logger.warning("ENTRY_BLOCKED_COOLDOWN symbol=%s remaining_seconds=%s", symbol, remaining)
			return jsonify({"error": "Entry blocked: recent stop-loss/exit for this symbol. Try again later.", "retry_after_seconds": remaining}), 429
		# Fetch fresh LTP
		qd = kite.quote([f"NSE:{symbol}"]) or {}
		q = qd.get(f"NSE:{symbol}") or {}
		ltp = q.get('last_price')
		if not isinstance(ltp, (int, float)) or ltp <= 0:
			return jsonify({"error": "Invalid LTP received"}), 400
		# Determine price basis and qty
		price_basis = ltp
		limit_price = None
		if not use_market:
			# Tick rounding for limit entry - place at LTP
			inst_csv = os.path.join(REPO_ROOT, os.getenv('INSTRUMENTS_CSV', os.path.join('Csvs','instruments.csv')))
			ticks = _load_tick_sizes(inst_csv)
			tick = ticks.get(symbol) or _fallback_tick_by_price(ltp)
			limit_price = _round_to_tick(ltp, tick)
			if limit_price <= 0:
				return jsonify({"error": "Computed price invalid"}), 400
			price_basis = limit_price
		qty = int(budget // price_basis)
		if qty <= 0:
			return jsonify({"error": "Budget too small for 1 share at target price"}), 400
		order_kwargs = dict(
			variety=kite.VARIETY_REGULAR,
			exchange='NSE',
			tradingsymbol=symbol,
			transaction_type=kite.TRANSACTION_TYPE_BUY,
			quantity=qty,
			product=kite.PRODUCT_CNC,
			validity=kite.VALIDITY_DAY,
		)
		if use_market:
			order_kwargs.update(order_type=kite.ORDER_TYPE_MARKET)
		else:
			order_kwargs.update(order_type=kite.ORDER_TYPE_LIMIT, price=limit_price)
		
		# Place the limit order with proper error handling
		try:
			order_id = kite.place_order(**order_kwargs)
		except Exception as order_err:
			error_str = str(order_err).lower()
			# Check for specific error conditions
			if 'margin' in error_str or 'insufficient' in error_str or 'required' in error_str:
				order_logger.error("ORDER_MARGIN_ERROR symbol=%s qty=%s price=%s err=%s", symbol, qty, limit_price, order_err)
				return jsonify({"error": f"Margin/Insufficient funds: {str(order_err)}"}), 400
			elif 'market' in error_str or 'closed' in error_str or 'trading hours' in error_str:
				order_logger.error("ORDER_MARKET_CLOSED symbol=%s qty=%s err=%s", symbol, qty, order_err)
				return jsonify({"error": f"Market closed or trading hours issue: {str(order_err)}"}), 400
			else:
				order_logger.error("ORDER_PLACEMENT_FAILED symbol=%s qty=%s price=%s err=%s", symbol, qty, limit_price, order_err)
				return jsonify({"error": f"Order placement failed: {str(order_err)}"}), 400
		
		# Verify order was accepted (not rejected) before placing GTT
		order_status = "PENDING"  # Default assumption
		try:
			# Check order status from broker
			orders = kite.orders() or []
			for o in orders:
				if str(o.get('order_id')) == str(order_id):
					order_status = (o.get('status') or '').upper()
					break
		except Exception as status_err:
			order_logger.warning("Could not verify order status for %s: %s", order_id, status_err)
		
		# Only place GTT if order was accepted (not rejected)
		gtt_id = None
		if order_status == "REJECTED":
			order_logger.error("ORDER_REJECTED symbol=%s order_id=%s status=%s", symbol, order_id, order_status)
			return jsonify({
				"status": "rejected",
				"error": "Order was rejected by broker",
				"order_id": order_id,
				"symbol": symbol,
				"reason": "The order was not accepted. This could be due to margin, market hours, or broker restrictions. No GTT was placed."
			}), 400
		
		if with_tsl and order_id and order_status != "REJECTED":
			try:
				# Place bracket OCO: target +7.5%, stop -5%
				gtt_id = _place_bracket_oco_gtt(kite, symbol, qty, ref_price=ltp, target_pct=0.075, sl_pct=sl_pct)
				# Start trailing from last LTP (raise-only)
				inst_csv = os.path.join(REPO_ROOT, os.getenv('INSTRUMENTS_CSV', os.path.join('Csvs','instruments.csv')))
				tmap = _load_tick_sizes(inst_csv)
				trail_tick = tmap.get(symbol) or _fallback_tick_by_price(ltp)
				_start_trailing(kite, symbol, qty, sl_pct, ltp=ltp, tick=trail_tick, gtt_id=gtt_id, gtt_type='oco', target_pct=0.075)
				# Persist computed GTT target/stop immediately so UI can show them
				try:
					target_px = _round_to_tick(ltp * (1 + 0.075), trail_tick)
					stop_px = _round_to_tick(ltp * (1 - sl_pct), trail_tick)
					target_pct_from_ltp = ((target_px / float(ltp)) - 1.0) * 100.0 if ltp else None
					stop_pct_from_ltp = (1.0 - (stop_px / float(ltp))) * 100.0 if ltp else None
					with _orders_lock:
						# attach to any existing temp record for this order id if present
						rec = _orders_store.get(str(order_id))
						if rec is None:
							# create minimal record
							_orders_store[str(order_id)] = {
								"symbol": symbol,
								"quantity": qty,
								"limit_price": limit_price,
								"gtt_id": gtt_id,
								"gtt_target_price": target_px,
								"gtt_target_pct_from_ltp": target_pct_from_ltp,
								"gtt_stop_price": stop_px,
								"gtt_stop_pct_from_ltp": stop_pct_from_ltp,
								"ts": int(time.time()),
							}
						else:
							rec['gtt_id'] = gtt_id
							rec['gtt_target_price'] = target_px
							rec['gtt_target_pct_from_ltp'] = target_pct_from_ltp
							rec['gtt_stop_price'] = stop_px
							rec['gtt_stop_pct_from_ltp'] = stop_pct_from_ltp
				except Exception:
					# non-fatal: continue
					pass
			except Exception as ge:
				order_logger.error("ORDER_GTT_FAIL symbol=%s qty=%s sl_pct=%.3f err=%s", symbol, qty, sl_pct, ge)
				# GTT placement failed but order succeeded; return partial success
				gtt_error_msg = str(ge)
				# Check for specific GTT errors
				if 'margin' in gtt_error_msg.lower() or 'insufficient' in gtt_error_msg.lower():
					gtt_error_msg = f"GTT Margin Error: {ge}"
				elif 'market' in gtt_error_msg.lower() or 'closed' in gtt_error_msg.lower():
					gtt_error_msg = f"GTT Market Closed: {ge}"
				with _orders_lock:
					_orders_store[str(order_id)] = {
						"symbol": symbol,
						"quantity": qty,
						"limit_price": limit_price,
						"gtt_id": None,
						"ts": int(time.time()),
					}
				return jsonify({
					"status": "partial",
					"order_id": order_id,
					"symbol": symbol,
					"limit_price": limit_price,
					"quantity": qty,
					"gtt_error": gtt_error_msg,
					"message": "Limit order placed successfully, but GTT placement failed"
				}), 202
		# Record order in store
		with _orders_lock:
			_orders_store[str(order_id)] = {
				"symbol": symbol,
				"quantity": qty,
				"limit_price": limit_price,
				"gtt_id": gtt_id,
				"ts": int(time.time()),
			}
		order_logger.info("ORDER_OK symbol=%s qty=%s price=%s order_id=%s gtt_id=%s trailing=%s", symbol, qty, limit_price, order_id, gtt_id, with_tsl)
		
		# Automatically log to trade journal
		try:
			from datetime import datetime
			_log_trade_entry(symbol, qty, limit_price or ltp, order_id, gtt_id)
		except Exception as je:
			order_logger.warning("Failed to log trade journal entry for %s: %s", symbol, je)
		
		return jsonify({
			"status": "ok",
			"order_id": order_id,
			"symbol": symbol,
			"limit_price": limit_price,
			"quantity": qty,
			"gtt_id": gtt_id,
			"gtt_placed": bool(gtt_id),
			"message": "Limit order and GTT placed successfully" if gtt_id else "Limit order placed successfully (GTT not requested)"
		})
	except Exception as e:
		error_logger.exception("/api/order/buy failed: %s", e)
		return jsonify({"error": str(e)}), 500
@app.get("/api/orders")
def api_orders():
	"""Return orders placed via this app with live LTP, trailing stop, and PnL."""
	try:
		with _orders_lock:
			items = list(_orders_store.items())
		if not items:
			return jsonify({"count": 0, "orders": []})
		kite = get_kite()
		# Prefer cached broker orders; fallback to immediate fetch if empty
		with _broker_orders_lock:
			broker_orders = list(_broker_orders_cache.values())
		if not broker_orders:
			try:
				broker_orders = kite.orders() or []
				with _broker_orders_lock:
					for o in broker_orders:
						oid = str(o.get('order_id') or '')
						if oid:
							_broker_orders_cache[oid] = o
			except Exception as oe:
				error_logger.error("orders() failed: %s", oe)
				broker_orders = []
		# Filter to open/active orders for relevance
		active_status = {"OPEN", "TRIGGER_PENDING", "PENDING", "OPEN_PENDING"}
		ord_map = {str(o.get('order_id')): o for o in broker_orders if (o.get('status') or '').upper() in active_status or str(o.get('order_id')) in _orders_store}

		# Fetch current GTTs (GTC) to get target/stop books
		# Use cached GTTs refreshed every 30s
		gtt_list = []
		global _gtt_cache_ts, _gtt_cache
		now = time.time()
		try:
			if now - _gtt_cache_ts > 30:
				gtt_list = _fetch_gtts_with_timeout(kite, timeout=2.0) or []
				_gtt_cache = {}
				for g in gtt_list:
					ts = g.get('tradingsymbol') or g.get('symbol')
					if not ts:
						continue
					_gtt_cache.setdefault(ts, []).append(g)
				_gtt_cache_ts = now
			else:
				# flatten cached gtt map
				tmp = []
				for arr in _gtt_cache.values():
					tmp.extend(arr)
				gtt_list = tmp
		except Exception as ge:
			error_logger.error("gtt() failed: %s", ge)
			gtt_list = []
		# index gtt by tradingsymbol for convenience
		gtt_by_symbol: dict[str, list] = {}
		for g in gtt_list:
			ts = g.get('tradingsymbol') or g.get('symbol')
			if not ts:
				continue
			gtt_by_symbol.setdefault(ts, []).append(g)
		# Quotes for symbols
		syms = sorted({ rec[1].get('symbol') for rec in items if rec[1].get('symbol') })
		# Quote fetch: only if needed and batched; prefer existing LTP from data service when possible
		quotes = {}
		try:
			quotes = kite.quote([f"NSE:{s}" for s in syms]) or {}
		except Exception as qe:
			error_logger.error("quote() failed: %s", qe)
			quotes = {}
		resp = []
		for oid, rec in items:
			symbol = rec.get('symbol')
			q = quotes.get(f"NSE:{symbol}") or {}
			ltp = q.get('last_price')
			# try app data service for LTP if quote missing
			if not isinstance(ltp, (int,float)) or ltp <= 0:
				try:
					dmap = fetch_ltp().get('data', {})
					if symbol in dmap and isinstance(dmap[symbol].get('last_price'), (int,float)):
						ltp = dmap[symbol].get('last_price')
				except Exception as e:
					error_logger.debug("Silent exception ignored: %s", e)
			od = ord_map.get(str(oid)) or {}
			status = od.get('status') or 'UNKNOWN'
			avg_price = od.get('average_price') or od.get('average') or rec.get('limit_price')
			qty = rec.get('quantity')
			# trailing trigger from state - now keyed by gtt_id
			trail_trigger = None
			try:
				# Look for any trailing state matching this symbol
				for gid, st in _trail_state.items():
					if st.get('symbol') == symbol:
						trail_trigger = st.get('trigger')
						break
			except Exception:
				trail_trigger = None

			# extract GTT target/stop info from current gtt book
			gtt_info = []
			target_price = None
			stop_price = None
			if symbol and symbol in gtt_by_symbol:
				for g in gtt_by_symbol.get(symbol, []):
					try:
						orders = g.get('orders') or []
						trigger_values = g.get('trigger_values') or []
						gtt_info.append({
							"id": g.get('id') or g.get('trigger_id'),
							"type": g.get('type') or g.get('trigger_type'),
							"status": g.get('status'),
							"trigger_values": trigger_values,
							"orders": orders,
						})
						# derive target/stop prices (robust to string values)
						cand_high = None
						cand_low = None
						for o in orders:
							if (o.get('transaction_type') or '').upper() == 'SELL':
								p = o.get('price')
								if not isinstance(p, (int, float)):
									try:
										p = float(p)
									except Exception:
										p = None
								if isinstance(p, (int,float)):
									cand_high = p if (cand_high is None or p > cand_high) else cand_high
									cand_low = p if (cand_low is None or p < cand_low) else cand_low
						# fall back to trigger values if order prices missing
						if cand_high is None or cand_low is None:
							vals = []
							for tv in (trigger_values or []):
								try:
									vals.append(float(tv))
								except Exception as e:
									error_logger.debug("Silent exception ignored: %s", e)
							if vals:
								mx = max(vals)
								mn = min(vals)
								cand_high = mx if cand_high is None else max(cand_high, mx)
								cand_low = mn if cand_low is None else min(cand_low, mn)

						if cand_high is not None:
							target_price = cand_high if (target_price is None or cand_high > target_price) else target_price
						if cand_low is not None:
							stop_price = cand_low if (stop_price is None or cand_low < stop_price) else stop_price
					except Exception as e:
						error_logger.debug("Silent exception ignored: %s", e)
			pnl = None
			pnl_pct = None
			try:
				if isinstance(ltp, (int,float)) and isinstance(avg_price, (int,float)) and qty:
					pnl = (float(ltp) - float(avg_price)) * int(qty)
					if float(avg_price):
						pnl_pct = (float(ltp) / float(avg_price) - 1.0) * 100.0
			except Exception:
				pnl = None
				pnl_pct = None
			# determine current stop price and % from LTP
			current_stop_price = None
			# prefer trailing trigger if available, else derived stop
			if isinstance(trail_trigger, (int,float)):
				current_stop_price = trail_trigger
			elif isinstance(stop_price, (int,float)):
				current_stop_price = stop_price
			elif symbol and symbol in gtt_by_symbol:
				try:
					for g in gtt_by_symbol.get(symbol, []):
						orders = g.get('orders') or []
						for o in orders:
							# look for SELL limit leg as stop
							if (o.get('transaction_type') or '').upper() == 'SELL':
								p = o.get('price')
								if not isinstance(p, (int, float)):
									try:
										p = float(p)
									except Exception:
										p = None
								if isinstance(p, (int,float)):
									current_stop_price = p
									break
						if current_stop_price is not None:
							break
				except Exception as e:
					error_logger.debug("Silent exception ignored: %s", e)
			stop_pct_from_ltp = None
			target_pct_from_ltp = None
			try:
				if isinstance(ltp, (int,float)) and isinstance(current_stop_price, (int,float)) and ltp>0:
					stop_pct_from_ltp = (1.0 - (current_stop_price/float(ltp))) * 100.0
				if isinstance(ltp, (int,float)) and isinstance(target_price, (int,float)) and ltp>0:
					target_pct_from_ltp = ((target_price/float(ltp)) - 1.0) * 100.0
			except Exception:
				stop_pct_from_ltp = None
				target_pct_from_ltp = None

			# Prefer persisted gtt values from our local store if broker didn't supply them
			try:
				if (target_price is None or not isinstance(target_price, (int,float))) and rec.get('gtt_target_price') is not None:
					tmp = rec.get('gtt_target_price')
					if isinstance(tmp, (int,float)):
						target_price = float(tmp)
			except Exception as e:
				error_logger.debug("Silent exception ignored: %s", e)
			try:
				if (stop_price is None or not isinstance(stop_price, (int,float))) and rec.get('gtt_stop_price') is not None:
					tmp2 = rec.get('gtt_stop_price')
					if isinstance(tmp2, (int,float)):
						stop_price = float(tmp2)
			except Exception as e:
				error_logger.debug("Silent exception ignored: %s", e)
			# recompute percent fields if LTP available
			try:
				if isinstance(ltp, (int,float)) and ltp>0:
					if isinstance(target_price, (int,float)):
						target_pct_from_ltp = ((target_price/float(ltp)) - 1.0) * 100.0
					if isinstance(stop_price, (int,float)):
						stop_pct_from_ltp = (1.0 - (stop_price/float(ltp))) * 100.0
			except Exception as e:
				error_logger.debug("Silent exception ignored: %s", e)
			resp.append({
				"order_id": oid,
				"symbol": symbol,
				"quantity": qty,
				"status": status,
				"avg_price": avg_price,
				"ltp": ltp,
				"trail_trigger": trail_trigger,
				"gtt_stop_price": current_stop_price,
				"gtt_stop_pct_from_ltp": stop_pct_from_ltp,
				"gtt_target_price": target_price,
				"gtt_target_pct_from_ltp": target_pct_from_ltp,
				"gtt_book": gtt_info,
				"pnl": pnl,
				"pnl_pct": pnl_pct,
				"gtt_id": rec.get('gtt_id'),
				"ts": rec.get('ts'),
			})
		return jsonify({"count": len(resp), "orders": resp})
	except Exception as e:
		error_logger.exception("/api/orders failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.post('/api/order/cancel')
@limiter.limit("10 per minute")  # Max 10 cancel requests per minute per IP
def api_cancel_order():
	"""Cancel a broker order by order_id (JSON body: {"order_id": "...", "symbol": "..."})."""
	try:
		access_logger.info("POST /api/order/cancel from %s", request.remote_addr)
		payload = request.get_json(silent=True) or {}
		order_id = str(payload.get('order_id') or payload.get('orderId') or '').strip()
		symbol = str(payload.get('symbol') or '').strip()
		if not order_id:
			return jsonify({"error": "order_id is required"}), 400
		kite = get_kite()
		# Try common cancel API names on the kite client
		try:
			if hasattr(kite, 'cancel_order'):
				# Many kite wrappers accept (order_id=...)
				try:
					res = kite.cancel_order(order_id=order_id)
				except TypeError:
					# fallback signature
					res = kite.cancel_order(variety=getattr(kite, 'VARIETY_REGULAR', 'regular'), order_id=order_id)
			else:
				return jsonify({"error": "cancel_order not supported by kite client"}), 501
		except Exception as ce:
			error_logger.error("cancel_order failed for %s: %s", order_id, ce)
			return jsonify({"error": str(ce)}), 500
		# Remove from local stores if present
		with _orders_lock:
			_orders_store.pop(str(order_id), None)
		with _broker_orders_lock:
			_broker_orders_cache.pop(str(order_id), None)
		
		# Auto-cancel linked GTT orders for the same symbol
		cancelled_gtt_id = None
		if symbol:
			try:
				with _broker_orders_lock:
					gtt_list = _gtt_cache.get(symbol, [])
					if gtt_list and len(gtt_list) > 0:
						# Get the first GTT for this symbol (typically only one stop-loss per stock)
						gtt_to_cancel = gtt_list[0]
						gtt_id = gtt_to_cancel.get('id') or gtt_to_cancel.get('trigger_id')
						if gtt_id:
							try:
								if hasattr(kite, 'delete_gtt'):
									kite.delete_gtt(gtt_id)
								elif hasattr(kite, 'cancel_gtt'):
									kite.cancel_gtt(gtt_id)
								cancelled_gtt_id = gtt_id
								# Remove from cache
								newarr = [g for g in gtt_list if str(g.get('id') or g.get('trigger_id')) != str(gtt_id)]
								if newarr:
									_gtt_cache[symbol] = newarr
								else:
									_gtt_cache.pop(symbol, None)
								access_logger.info("Auto-cancelled GTT %s for symbol %s when order %s cancelled", gtt_id, symbol, order_id)
							except Exception as gtt_err:
								error_logger.warning("Failed to auto-cancel GTT %s for symbol %s: %s", gtt_id, symbol, gtt_err)
			except Exception as e:
				error_logger.warning("Error checking/cancelling linked GTT: %s", e)
		
		response = {"status": "ok", "order_id": order_id, "result": res}
		if cancelled_gtt_id:
			response["linked_gtt_cancelled"] = True
			response["linked_gtt_id"] = cancelled_gtt_id
		return jsonify(response)
	except Exception as e:
		error_logger.exception("/api/order/cancel failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.post('/api/gtt/cancel')
@limiter.limit("10 per minute")  # Max 10 GTT cancel requests per minute per IP
def api_cancel_gtt():
	"""Cancel/delete a GTT by trigger id (JSON body: {"gtt_id": "..."})."""
	try:
		access_logger.info("POST /api/gtt/cancel from %s", request.remote_addr)
		payload = request.get_json(silent=True) or {}
		gtt_id = payload.get('gtt_id') or payload.get('trigger_id') or payload.get('id')
		if gtt_id is None:
			return jsonify({"error": "gtt_id is required"}), 400
		kite = get_kite()
		try:
			# prefer delete_gtt if available
			if hasattr(kite, 'delete_gtt'):
				res = kite.delete_gtt(gtt_id)
			elif hasattr(kite, 'cancel_gtt'):
				res = kite.cancel_gtt(gtt_id)
			else:
				return jsonify({"error": "GTT delete not supported by kite client"}), 501
		except Exception as ge:
			error_logger.error("gtt cancel failed for %s: %s", gtt_id, ge)
			return jsonify({"error": str(ge)}), 500
		# Remove from cached GTTs if present
		try:
			with _broker_orders_lock:
				# purge any cached entries that reference this id
				for k, arr in list(_gtt_cache.items()):
					newarr = [g for g in arr if str(g.get('id') or g.get('trigger_id')) != str(gtt_id)]
					if newarr:
						_gtt_cache[k] = newarr
					else:
						_gtt_cache.pop(k, None)
		except Exception as e:
			error_logger.debug("Silent exception ignored: %s", e)
		return jsonify({"status": "ok", "gtt_id": gtt_id, "result": res})
	except Exception as e:
		error_logger.exception("/api/gtt/cancel failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.post('/api/gtt/book')
@limiter.limit("5 per minute")  # Max 5 book profit requests per minute per IP
def api_book_gtt():
	"""Book profit: modify GTT to set target at LTP (current price), keep stop same.
	JSON body: {"gtt_id": "...", "symbol": "...", "qty": int, "stop_price": float, "target_price": float}
	"""
	try:
		access_logger.info("POST /api/gtt/book from %s", request.remote_addr)
		payload = request.get_json(silent=True) or {}
		gtt_id = payload.get('gtt_id') or payload.get('trigger_id') or payload.get('id')
		symbol = (payload.get('symbol') or '').strip().upper()
		qty = int(payload.get('qty', 0))
		stop_price = float(payload.get('stop_price', 0))
		target_price = float(payload.get('target_price', 0))
		
		if not gtt_id or not symbol or qty <= 0 or stop_price <= 0 or target_price <= 0:
			return jsonify({"error": "gtt_id, symbol, qty, stop_price, and target_price are required"}), 400
		
		kite = get_kite()
		
		# Fetch tick size for proper rounding
		inst_csv = os.path.join(REPO_ROOT, os.getenv('INSTRUMENTS_CSV', os.path.join('Csvs','instruments.csv')))
		ticks = _load_tick_sizes(inst_csv)
		tick = ticks.get(symbol) or _fallback_tick_by_price(target_price)
		
		# Round prices to proper tick
		stop_px = _round_to_tick(stop_price, tick)
		
		# target_price from frontend is LTP - we need to set target 0.3% above it
		# Zerodha API requires trigger price to differ from LTP by at least 0.25%
		ltp = _round_to_tick(target_price, tick)  # Keep original LTP for last_price param
		buffer_amount = ltp * 0.003  # 0.3% buffer above LTP
		target_px = _round_to_tick(ltp + buffer_amount, tick)
		
		if stop_px <= 0 or ltp <= 0:
			return jsonify({"error": "Invalid stop or LTP price after rounding"}), 400
		
		try:
			# Cancel the GTT order first
			try:
				kite.delete_gtt(trigger_id=gtt_id)
				order_logger.info("BOOK_GTT cancelled GTT %s for %s", gtt_id, symbol)
			except Exception as cancel_err:
				order_logger.warning("Failed to cancel GTT %s: %s", gtt_id, cancel_err)
				return jsonify({"error": f"Failed to cancel GTT: {str(cancel_err)}"}), 500
			
			# Place a limit sell order at LTP to get filled immediately
			order_id = kite.place_order(
				variety=kite.VARIETY_REGULAR,
				exchange='NSE',
				tradingsymbol=symbol,
				transaction_type=kite.TRANSACTION_TYPE_SELL,
				quantity=qty,
				product=kite.PRODUCT_CNC,
				order_type=kite.ORDER_TYPE_LIMIT,
				price=ltp
			)
			order_logger.info("BOOK_GTT placed SELL order symbol=%s order_id=%s qty=%d price=%.2f (cancelled_gtt=%s)", 
							  symbol, order_id, qty, ltp, gtt_id)
		except Exception as ge:
			order_logger.error("gtt book failed for %s: %s", gtt_id, ge)
			return jsonify({"error": f"Book order failed: {str(ge)}"}), 500
		
		return jsonify({
			"status": "ok",
			"gtt_id": gtt_id,
			"order_id": order_id,
			"symbol": symbol,
			"sell_price": ltp,
			"quantity": qty,
			"message": f"GTT cancelled, SELL order placed at {ltp:.2f}"
		})
	except Exception as e:
		order_logger.exception("/api/gtt/book failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.route('/api/trade-journal', methods=['GET', 'POST', 'PUT'])
def api_trade_journal():
	"""Get, create, or update trade journal entries with order book data."""
	from pgAdmin_database.db_connection import pg_cursor

	# Keep the function small and robust. Support both `timestamp` and `entry_date`
	# column names for backward compatibility.
	try:
		method = request.method
		if method == 'POST':
			data = request.get_json() or {}
			trade_id = data.get('trade_id')
			symbol = (data.get('symbol') or '').strip().upper()
			ts_val = data.get('timestamp') or data.get('entry_date')
			side = data.get('side')
			order_book_imbalance = data.get('order_book_imbalance')
			intended_entry_price = data.get('intended_entry_price')
			fill_price = data.get('fill_price')
			slippage = data.get('slippage')
			exit_price = data.get('exit_price')
			pnl = data.get('pnl')
			setup_description = data.get('setup_description')

			# basic validation: require symbol and a timestamp-like value
			if not symbol or not ts_val:
				return jsonify({"error": "symbol and timestamp (or entry_date) are required"}), 400

			with pg_cursor() as (cur, conn):
				# Create table if missing (idempotent). Include both columns to be tolerant.
				cur.execute("""
					CREATE TABLE IF NOT EXISTS trade_journal (
						id SERIAL PRIMARY KEY,
						trade_id TEXT UNIQUE,
						symbol TEXT NOT NULL,
						entry_date TIMESTAMP,
						timestamp TIMESTAMP,
						side TEXT,
						order_book_imbalance NUMERIC,
						intended_entry_price NUMERIC,
						fill_price NUMERIC,
						slippage NUMERIC,
						exit_price NUMERIC,
						pnl NUMERIC,
						setup_description TEXT,
						created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
						updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
						strategy TEXT,
						status TEXT
					)
				""")

				# inspect existing schema so we only include columns that actually exist
				cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'trade_journal'")
				cols = {r[0] for r in cur.fetchall()}
				if 'timestamp' in cols:
					ts_col = 'timestamp'
				elif 'entry_date' in cols:
					ts_col = 'entry_date'
				else:
					ts_col = 'entry_date'

				# build a mapping of candidate columns to values
				cand = {
					'trade_id': trade_id,
					'symbol': symbol,
					ts_col: ts_val,
					'side': side,
					'order_book_imbalance': order_book_imbalance,
					'intended_entry_price': intended_entry_price,
					'fill_price': fill_price,
					'slippage': slippage,
					'exit_price': exit_price,
					'pnl': pnl,
					'setup_description': setup_description,
				}

				# select only columns that exist and have non-None values
				insert_cols = []
				insert_vals = []
				for k, v in cand.items():
					if k in cols and v is not None:
						insert_cols.append(k)
						insert_vals.append(v)

				# perform insert/upsert depending on presence of trade_id
				new_id = None
				if not insert_cols:
					# nothing to insert
					access_logger.warning("/api/trade-journal POST: nothing to insert for symbol=%s", symbol)
				else:
					placeholders = ','.join(['%s'] * len(insert_cols))
					cols_sql = ', '.join(insert_cols)
					if trade_id and 'trade_id' in insert_cols:
						# build upsert; include commonly-updated columns only if they exist
						update_sets = []
						if 'exit_price' in cols:
							update_sets.append('exit_price = EXCLUDED.exit_price')
						if 'pnl' in cols:
							update_sets.append('pnl = EXCLUDED.pnl')
						if 'updated_at' in cols:
							update_sets.append('updated_at = CURRENT_TIMESTAMP')
						if update_sets:
							upsert_sql = f"INSERT INTO trade_journal ({cols_sql}) VALUES ({placeholders}) ON CONFLICT (trade_id) DO UPDATE SET {', '.join(update_sets)}"
						else:
							# no meaningful update columns; fall back to insert with ON CONFLICT DO NOTHING
							upsert_sql = f"INSERT INTO trade_journal ({cols_sql}) VALUES ({placeholders}) ON CONFLICT (trade_id) DO NOTHING"
						cur.execute(upsert_sql, tuple(insert_vals))
					else:
						# simple insert
						ins_sql = f"INSERT INTO trade_journal ({cols_sql}) VALUES ({placeholders})"
						cur.execute(ins_sql, tuple(insert_vals))
					conn.commit()

					# fetch id of inserted/updated row
					try:
						if trade_id:
							cur.execute("SELECT id FROM trade_journal WHERE trade_id = %s", (trade_id,))
							res = cur.fetchone()
							new_id = res[0] if res else None
						else:
							if ts_col in cols:
								cur.execute(f"SELECT id FROM trade_journal WHERE symbol = %s AND {ts_col} = %s ORDER BY id DESC LIMIT 1", (symbol, ts_val))
								res = cur.fetchone()
								new_id = res[0] if res else None
					except Exception:
						new_id = None

			access_logger.info("/api/trade-journal POST: symbol=%s, side=%s", symbol, side)
			resp = {"success": True, "id": new_id, "symbol": symbol}
			if side is not None:
				resp['side'] = side
			return jsonify(resp)

		elif method == 'PUT':
			data = request.get_json() or {}
			trade_id = data.get('trade_id')
			if not trade_id:
				return jsonify({"error": "trade_id is required for update"}), 400
			with pg_cursor() as (cur, conn):
				cur.execute(
					"""
					UPDATE trade_journal
					SET exit_price = %s, pnl = %s, updated_at = CURRENT_TIMESTAMP, exit_date = COALESCE(%s, exit_date)
					WHERE trade_id = %s
					""",
					(data.get('exit_price'), data.get('pnl'), data.get('exit_date'), trade_id)
				)
				conn.commit()
			access_logger.info("/api/trade-journal PUT: trade_id=%s, pnl=%s", trade_id, data.get('pnl'))
			return jsonify({"success": True, "trade_id": trade_id})

		else:  # GET
			symbol_filter = request.args.get('symbol')
			days_back = int(request.args.get('days', 30))
			with pg_cursor() as (cur, conn):
				# detect which timestamp-like column exists and use it
				# inspect schema and pick only columns that actually exist so this endpoint
				# is tolerant to schema variants (older/newer deployments may lack some cols)
				cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'trade_journal'")
				schema_cols = [r[0] for r in cur.fetchall()]
				if 'timestamp' in schema_cols:
					ts_col = 'timestamp'
				elif 'entry_date' in schema_cols:
					ts_col = 'entry_date'
				else:
					ts_col = 'entry_date'

				# Use a minimal stable set of fields to stay compatible with different DB schemas.
				fields = ['id', 'trade_id', 'symbol', f"{ts_col} AS ts"]

				base_select = "SELECT " + ", ".join(fields) + " FROM trade_journal"
				access_logger.debug("trade_journal schema_cols=%s", schema_cols)
				access_logger.debug("trade_journal selected fields=%s", fields)
				access_logger.debug("trade_journal base_select=%s", base_select)
				# Execute the constructed query; if the database complains about a missing
				# column (schema mismatch), fall back to a minimal safe select to avoid 500s.
				try:
					if symbol_filter:
						cur.execute(base_select + f" WHERE symbol = %s AND {ts_col} >= NOW() - INTERVAL '{days_back} days' ORDER BY {ts_col} DESC", (symbol_filter,))
					else:
						cur.execute(base_select + f" WHERE {ts_col} >= NOW() - INTERVAL '{days_back} days' ORDER BY {ts_col} DESC")
				except Exception:
					# Retry with minimal core fields only (id, trade_id, symbol, timestamp)
					minimal_fields = [
						'id', 'trade_id', 'symbol', f"{ts_col} AS ts"
					]
					minimal_select = "SELECT " + ", ".join(minimal_fields) + " FROM trade_journal"
					if symbol_filter:
						cur.execute(minimal_select + f" WHERE symbol = %s AND {ts_col} >= NOW() - INTERVAL '{days_back} days' ORDER BY {ts_col} DESC", (symbol_filter,))
					else:
						cur.execute(minimal_select + f" WHERE {ts_col} >= NOW() - INTERVAL '{days_back} days' ORDER BY {ts_col} DESC")
				rows = cur.fetchall()
				cols = [desc[0] for desc in cur.description]
				trades = [dict(zip(cols, row)) for row in rows]
				# normalize datetime fields to isoformat
				for trade in trades:
					if trade.get('ts'):
						trade['ts'] = trade['ts'].isoformat()
					if trade.get('created_at'):
						trade['created_at'] = trade['created_at'].isoformat()
					if trade.get('updated_at'):
						trade['updated_at'] = trade['updated_at'].isoformat()
				# present timestamp as 'timestamp' for clients expecting that field
				for t in trades:
					if 'ts' in t:
						t['timestamp'] = t.pop('ts')
				return jsonify(trades)

	except Exception as e:
		error_logger.exception("/api/trade-journal failed: %s", e)
		return jsonify({"error": str(e)}), 500

@app.post('/api/trade-journal/log-exit')
@limiter.limit("10 per minute")  # Max 10 trade exit logs per minute per IP
def api_log_trade_exit():
	"""Manually log a trade exit (called when order is completed/GTT triggered)."""
	try:
		payload = request.get_json(silent=True) or {}
		order_id = payload.get('order_id')
		symbol = payload.get('symbol', '').upper()
		exit_price = float(payload.get('exit_price', 0))
		exit_qty = int(payload.get('exit_qty', 1))
		exit_type = payload.get('exit_type', 'completed')  # 'completed', 'gtt_trigger', 'manual'
		
		if not order_id or exit_price <= 0:
			return jsonify({"error": "order_id and exit_price required"}), 400
		
		_log_trade_exit(order_id, symbol, exit_price, exit_qty, exit_type)
		return jsonify({"status": "exit logged", "order_id": order_id, "exit_price": exit_price}), 200
	
	except Exception as e:
		order_logger.error("Failed to log exit via API: %s", e)
		return jsonify({"error": str(e)}), 500

@app.route('/api/trade-journal/sync-exits', methods=['POST'])
def api_sync_trade_exits():
	"""Sync all open trades with current orders and log exits for completed ones."""
	from pgAdmin_database.db_connection import pg_cursor
	
	try:
		kite = get_kite()
		
		# First check if trade_journal table exists, if not return early
		with pg_cursor() as (cur, conn):
			cur.execute("""
				SELECT EXISTS (
					SELECT FROM information_schema.tables 
					WHERE table_name = 'trade_journal'
				)
			""")
			table_exists = cur.fetchone()[0]
			
			if not table_exists:
				# Table doesn't exist yet, nothing to sync
				return jsonify({"status": "no trades to sync", "synced_count": 0}), 200
		
		# Get all open trades from journal
		with pg_cursor() as (cur, conn):
			cur.execute("""
				SELECT id, order_id, symbol, entry_qty FROM trade_journal 
				WHERE status = 'open' AND order_id IS NOT NULL
			""")
			open_trades = cur.fetchall()
		
		synced_count = 0
		for trade_id, order_id, symbol, qty in open_trades:
			try:
				# Fetch order details from broker
				orders = kite.orders() or []
				matching_order = None
				for order in orders:
					if str(order.get('order_id')) == str(order_id):
						matching_order = order
						break
				
				if matching_order and matching_order.get('status') == 'COMPLETE':
					# Order is complete, check if we logged the exit
					with pg_cursor() as (cur, conn):
						cur.execute("""
							SELECT exit_price FROM trade_journal WHERE id = %s
						""", (trade_id,))
						result = cur.fetchone()
						
						if result and result[0] is None:  # No exit price yet
							# Get fill price from order
							fill_price = float(matching_order.get('average_price', 0))
							if fill_price > 0:
								_log_trade_exit(order_id, symbol, fill_price, qty, 'synced')
								synced_count += 1
			except Exception as e:
				order_logger.warning("Error syncing order %s: %s", order_id, e)
				continue
		
		return jsonify({"status": "sync complete", "synced_count": synced_count}), 200
	
	except Exception as e:
		order_logger.error("Failed to sync exits: %s", e)
		return jsonify({"error": str(e)}), 500

@app.route('/api/trade-journal/stats', methods=['GET'])
def api_trade_journal_stats():
	"""Get trade journal statistics (win rate, avg PnL, etc)."""
	from pgAdmin_database.db_connection import pg_cursor
	
	try:
		symbol_filter = request.args.get('symbol')
		days_back = int(request.args.get('days', 30))
		
		with pg_cursor() as (cur, conn):
			if symbol_filter:
				cur.execute("""
					SELECT 
						COUNT(*) as total_trades,
						COUNT(CASE WHEN pnl > 0 THEN 1 END) as winning_trades,
						COUNT(CASE WHEN pnl <= 0 THEN 1 END) as losing_trades,
						ROUND(CAST(COUNT(CASE WHEN pnl > 0 THEN 1 END) AS FLOAT) / COUNT(*) * 100, 2) as win_rate_pct,
						ROUND(CAST(COALESCE(SUM(pnl), 0) AS NUMERIC), 2) as total_pnl,
						ROUND(CAST(AVG(pnl) AS NUMERIC), 2) as avg_pnl,
						ROUND(CAST(MAX(pnl) AS NUMERIC), 2) as max_win,
						ROUND(CAST(MIN(pnl) AS NUMERIC), 2) as max_loss,
						symbol
					FROM trade_journal
					WHERE symbol = %s AND timestamp >= NOW() - INTERVAL '%s days'
					GROUP BY symbol
				""", (symbol_filter, days_back))
			else:
				cur.execute("""
					SELECT 
						COUNT(*) as total_trades,
						COUNT(CASE WHEN pnl > 0 THEN 1 END) as winning_trades,
						COUNT(CASE WHEN pnl <= 0 THEN 1 END) as losing_trades,
						ROUND(CAST(COUNT(CASE WHEN pnl > 0 THEN 1 END) AS FLOAT) / COUNT(*) * 100, 2) as win_rate_pct,
						ROUND(CAST(COALESCE(SUM(pnl), 0) AS NUMERIC), 2) as total_pnl,
						ROUND(CAST(AVG(pnl) AS NUMERIC), 2) as avg_pnl,
						ROUND(CAST(MAX(pnl) AS NUMERIC), 2) as max_win,
						ROUND(CAST(MIN(pnl) AS NUMERIC), 2) as max_loss,
						'Overall' as symbol
					FROM trade_journal
					WHERE timestamp >= NOW() - INTERVAL '%s days'
				""", (days_back,))
			
			row = cur.fetchone()
			if row:
				cols = [desc[0] for desc in cur.description]
				stats = dict(zip(cols, row))
				return jsonify(stats)
			else:
				return jsonify({
					"total_trades": 0,
					"winning_trades": 0,
					"losing_trades": 0,
					"win_rate_pct": 0,
					"total_pnl": 0,
					"avg_pnl": 0,
					"max_win": 0,
					"max_loss": 0
				})
	
	except Exception as e:
		error_logger.exception("/api/trade-journal/stats failed: %s", e)
		return jsonify({"error": str(e)}), 500


	@app.route('/api/trade-journal/min', methods=['GET'])
	def api_trade_journal_min():
		"""Minimal, tolerant read of trade_journal: returns id, trade_id, symbol, timestamp.
		Use this when the richer `/api/trade-journal` fails due to schema mismatches.
		Query param: days (int, default 30), symbol (optional)
		"""
		try:
			days_back = int(request.args.get('days', 30))
			symbol_filter = (request.args.get('symbol') or '').strip().upper() or None
			from pgAdmin_database.db_connection import pg_cursor
			with pg_cursor() as (cur, conn):
				# ensure table exists
				cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'trade_journal')")
				if not cur.fetchone()[0]:
					return jsonify([])

				# coalesce timestamp-like columns into a single column
				# Use explicit SQL to avoid referencing missing columns
				sql_where = f"COALESCE(timestamp, entry_date) >= NOW() - INTERVAL '{days_back} days'"
				if symbol_filter:
					cur.execute(f"SELECT id, trade_id, symbol, COALESCE(timestamp, entry_date) AS ts FROM trade_journal WHERE symbol = %s AND {sql_where} ORDER BY ts DESC", (symbol_filter,))
				else:
					cur.execute(f"SELECT id, trade_id, symbol, COALESCE(timestamp, entry_date) AS ts FROM trade_journal WHERE {sql_where} ORDER BY ts DESC")
				rows = cur.fetchall()
				cols = [desc[0] for desc in cur.description]
				trades = [dict(zip(cols, row)) for row in rows]
				for t in trades:
					if t.get('ts'):
						t['timestamp'] = t.pop('ts').isoformat() if hasattr(t['ts'], 'isoformat') else t.pop('ts')
				return jsonify(trades)
		except Exception as e:
			error_logger.exception("/api/trade-journal/min failed: %s", e)
			return jsonify({"error": str(e)}), 500

# --------- DEBUG ENDPOINTS FOR TROUBLESHOOTING MISSING DATA ---------

@app.route('/api/debug/missing-data', methods=['GET'])
def debug_missing_data():
	"""Show which symbols are missing specific data fields."""
	try:
		data = fetch_ltp()
		
		missing_drawdown = []
		missing_pullback = []
		missing_days_gc = []
		missing_sma15m = []
		missing_sma50_15m = []
		missing_high200 = []
		missing_low200 = []
		missing_last_close = []
		
		for sym, values in data.items():
			if values.get('drawdown_15m_200_pct') is None:
				missing_drawdown.append(sym)
			if values.get('pullback_15m_200_pct') is None:
				missing_pullback.append(sym)
			if values.get('days_since_golden_cross') is None:
				missing_days_gc.append(sym)
			if values.get('sma200_15m') is None:
				missing_sma15m.append(sym)
			if values.get('sma50_15m') is None:
				missing_sma50_15m.append(sym)
			# high200 is calculated from cache, so check for None
			if values.get('sma200_15m') is None or values.get('last_price') is None:
				missing_high200.append(sym)
			if values.get('sma200_15m') is None or values.get('last_price') is None:
				missing_low200.append(sym)
			if values.get('last_close') is None:
				missing_last_close.append(sym)
		
		total_symbols = len(data)
		
		return jsonify({
			"total_symbols": total_symbols,
			"missing_fields": {
				"drawdown_15m_200_pct": {
					"count": len(missing_drawdown),
					"percentage": round(100 * len(missing_drawdown) / total_symbols, 1),
					"first_20_symbols": sorted(missing_drawdown)[:20]
				},
				"pullback_15m_200_pct": {
					"count": len(missing_pullback),
					"percentage": round(100 * len(missing_pullback) / total_symbols, 1),
					"first_20_symbols": sorted(missing_pullback)[:20]
				},
				"days_since_golden_cross": {
					"count": len(missing_days_gc),
					"percentage": round(100 * len(missing_days_gc) / total_symbols, 1),
					"first_20_symbols": sorted(missing_days_gc)[:20]
				},
				"sma200_15m": {
					"count": len(missing_sma15m),
					"percentage": round(100 * len(missing_sma15m) / total_symbols, 1),
					"first_20_symbols": sorted(missing_sma15m)[:20]
				},
				"sma50_15m": {
					"count": len(missing_sma50_15m),
					"percentage": round(100 * len(missing_sma50_15m) / total_symbols, 1),
					"first_20_symbols": sorted(missing_sma50_15m)[:20]
				},
				"last_close": {
					"count": len(missing_last_close),
					"percentage": round(100 * len(missing_last_close) / total_symbols, 1),
					"first_20_symbols": sorted(missing_last_close)[:20]
				}
			},
			"status": "OK" if all([len(x) == 0 for x in [missing_drawdown, missing_pullback, missing_days_gc]]) else "INCOMPLETE"
		})
	except Exception as e:
		error_logger.exception("debug_missing_data failed: %s", e)
		return jsonify({"error": str(e), "type": type(e).__name__}), 500

@app.route('/api/debug/cache-status', methods=['GET'])
def debug_cache_status():
	"""Show cache population status and sizes."""
	try:
		from ltp_service import (
			_sma15m_cache, _sma15m_last_ts, _sma15m_lock,
			_days_since_gc_cache, _days_since_gc_last_ts, _days_since_gc_lock,
			_daily_ma_cache, _daily_ma_lock,
			_high200_15m_cache, _low200_15m_cache, _last15m_close_cache,
			_volratio_cache, _volratio_last_ts, _sr_cache, _sr_last_ts,
			_vcp_cache, _vcp_last_ts
		)
		
		with _sma15m_lock:
			sma_size = len(_sma15m_cache)
		with _days_since_gc_lock:
			gc_size = len(_days_since_gc_cache)
		with _daily_ma_lock:
			daily_size = len(_daily_ma_cache)
		
		high200_size = len(_high200_15m_cache)
		low200_size = len(_low200_15m_cache)
		last_close_size = len(_last15m_close_cache)
		volratio_size = len(_volratio_cache)
		sr_size = len(_sr_cache)
		vcp_size = len(_vcp_cache)
		
		return jsonify({
			"caches": {
				"sma15m_cache": {
					"size": sma_size,
					"last_refresh": _sma15m_last_ts.isoformat() if _sma15m_last_ts else None,
					"status": "populated" if sma_size > 100 else "warming_up" if sma_size > 0 else "empty"
				},
				"high200_15m_cache": {
					"size": high200_size,
					"status": "populated" if high200_size > 100 else "warming_up" if high200_size > 0 else "empty"
				},
				"low200_15m_cache": {
					"size": low200_size,
					"status": "populated" if low200_size > 100 else "warming_up" if low200_size > 0 else "empty"
				},
				"last15m_close_cache": {
					"size": last_close_size,
					"status": "populated" if last_close_size > 100 else "warming_up" if last_close_size > 0 else "empty"
				},
				"days_since_gc_cache": {
					"size": gc_size,
					"last_refresh": _days_since_gc_last_ts.isoformat() if _days_since_gc_last_ts else None,
					"status": "populated" if gc_size > 100 else "warming_up" if gc_size > 0 else "empty"
				},
				"daily_ma_cache": {
					"size": daily_size,
					"status": "populated" if daily_size > 100 else "warming_up" if daily_size > 0 else "empty"
				},
				"volratio_cache": {
					"size": volratio_size,
					"last_refresh": _volratio_last_ts.isoformat() if _volratio_last_ts else None,
					"status": "populated" if volratio_size > 100 else "warming_up" if volratio_size > 0 else "empty"
				},
				"sr_cache": {
					"size": sr_size,
					"last_refresh": _sr_last_ts.isoformat() if _sr_last_ts else None,
					"status": "populated" if sr_size > 100 else "warming_up" if sr_size > 0 else "empty"
				},
				"vcp_cache": {
					"size": vcp_size,
					"last_refresh": _vcp_last_ts.isoformat() if _vcp_last_ts else None,
					"status": "populated" if vcp_size > 100 else "warming_up" if vcp_size > 0 else "empty"
				}
			},
			"overall_status": "healthy" if sma_size > 100 and gc_size > 100 else "warming_up",
			"note": "Caches populate on first fetch_ltp() call. If 'warming_up', wait 30-60 seconds and refresh."
		})
	except Exception as e:
		error_logger.exception("debug_cache_status failed: %s", e)
		return jsonify({"error": str(e), "type": type(e).__name__}), 500

# -------- Momentum Strategy Integration --------
try:
	from momentum_strategy import register_strategy_routes
	register_strategy_routes(app)
	logging.getLogger("momentum_strategy").info("Momentum strategy routes registered")
except ImportError as e:
	logging.warning(f"Could not import momentum_strategy: {e}")
except Exception as e:
	logging.warning(f"Failed to register momentum strategy routes: {e}")

if __name__ == "__main__":
	# Use 5050 default to avoid macOS AirPlay occupying 5000
	# Production-safe configuration: localhost only by default, debug disabled by default
	debug_mode = os.environ.get('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
	host = os.environ.get('FLASK_HOST', '127.0.0.1')  # Localhost only by default
	port = int(os.environ.get('PORT', 5050))
	
	if debug_mode:
		logging.warning("⚠️  FLASK DEBUG MODE ENABLED - NOT FOR PRODUCTION")
	if host != '127.0.0.1':
		logging.warning("⚠️  FLASK LISTENING ON %s - EXPOSED TO NETWORK", host)
	
	app.run(host=host, port=port, debug=debug_mode)

