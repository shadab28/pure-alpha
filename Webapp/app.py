"""Flask app wiring routes to LTP service and using external template."""
from __future__ import annotations

import os
import sys
import logging
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

app = Flask(__name__, template_folder="templates")


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


# -------- In-memory store for orders placed via this webapp --------
_orders_lock = threading.RLock()
_orders_store: dict[str, dict] = {}

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
		except Exception:
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
			# broadcast to SSE listeners
			try:
				msg = json.dumps({"type":"order_update","order":data})
				for q in list(_order_event_queues):
					try:
						q.put_nowait(msg)
					except Exception:
						pass
			except Exception:
				# swallow broadcast errors
				pass
		except Exception:
			pass

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
	access_logger.info("GET /events/orders from %s", request.remote_addr)
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
			except Exception:
				# if client disconnects early, stop
				return
			while True:
				try:
					msg = q.get()
					yield f"data: {msg}\n\n"
				except GeneratorExit:
					break
				except Exception:
					time.sleep(0.5)
		# Ensure listener
		try:
			_ensure_order_listener()
		except Exception:
			pass
		return Response(gen(), mimetype='text/event-stream')
	except Exception as e:
		error_logger.exception("/events/orders failed: %s", e)
		return jsonify({"error": str(e)}), 500


@app.route("/api/ltp")
def api_ltp():
	try:
		access_logger.info("GET /api/ltp from %s", request.remote_addr)
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
		access_logger.info("GET /api/ck from %s", request.remote_addr)
		from ltp_service import get_ck_data  # type: ignore
		res = get_ck_data()
		if 'error' in res:
			return jsonify({"error": res.get('error')}), 500
		return jsonify(res)
	except Exception as e:
		error_logger.exception("/api/ck failed: %s", e)
		return jsonify({"error": str(e)}), 500


@app.get("/api/orderbook")
def api_orderbook():
	"""Return the active/open broker order book directly from Kite."""
	try:
		access_logger.info("GET /api/orderbook from %s", request.remote_addr)
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
			except Exception:
				pass
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
				except Exception:
					pass
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
		access_logger.info("GET /api/trades from %s", request.remote_addr)
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
			except Exception:
				pass
		return jsonify({"count": len(trades), "trades": trades, "gtt_by_symbol": gtt_summary})
	except Exception as e:
		error_logger.exception("/api/trades failed: %s", e)
		return jsonify({"error": str(e)}), 500


@app.get("/api/positions")
def api_positions():
	"""Return current positions (day and net) from Kite."""
	try:
		access_logger.info("GET /api/positions from %s", request.remote_addr)
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
		access_logger.info("GET /api/holdings from %s", request.remote_addr)
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
					except Exception:
						pass
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
			access_logger.info("GET /api/gtt from %s", request.remote_addr)
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

@app.route("/")
def index():
	access_logger.info("GET / from %s", request.remote_addr)
	# Ensure we are listening to order updates
	try:
		_ensure_order_listener()
	except Exception:
		pass
	return render_template("index.html")


@app.route("/export/ltp.csv")
def export_ltp_csv():
	try:
		access_logger.info("GET /export/ltp.csv from %s", request.remote_addr)
		data = fetch_ltp()
		rows = [
			"symbol,last_price,last_close_15m,sma200_15m,sma50_15m,ratio_15m_50_200,rank_gm,pct_vs_15m_sma200,pct_vs_daily_sma50,drawdown_15m_200_pct,days_since_golden_cross,daily_sma50,daily_sma200,daily_ratio_50_200,volume_ratio_d5_d200"
		]
		for sym, info in data.get("data", {}).items():
			rows.append(
				f"{sym},{info.get('last_price','')},{info.get('last_close','')},{info.get('sma200_15m','')},{info.get('sma50_15m','')},{info.get('ratio_15m_50_200','')},{info.get('rank_gm','')},{info.get('pct_vs_15m_sma200','')},{info.get('pct_vs_daily_sma50','')},{info.get('drawdown_15m_200_pct','')},{info.get('days_since_golden_cross','')},{info.get('daily_sma50','')},{info.get('daily_sma200','')},{info.get('daily_ratio_50_200','')},{info.get('volume_ratio_d5_d200','')}"
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
						except Exception:
							pass
	except Exception:
		pass
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
					except Exception:
						pass
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
						if hasattr(kite, 'modify_gtt') and st.get('gtt_id'):
							if gtt_type == 'oco':
								# OCO GTT: update both stop and target triggers
								# Target stays at original % above current LTP
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
								trail_logger.info("TRAIL_MODIFY_OCO symbol=%s gtt_id=%s stop=%.2f target=%.2f", sym, st.get('gtt_id'), new_trig, new_target)
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
							with _trail_lock_obj():
								st['trigger'] = new_trig
						else:
							# replace: delete old and place new
							if st.get('gtt_id') and hasattr(kite, 'delete_gtt'):
								try:
									kite.delete_gtt(st['gtt_id'])
								except Exception:
									pass
							new_id = _place_sl_gtt(kite, sym, st['qty'], ref_price=ltp, sl_pct=st['sl_pct'])
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
		state.update({
			'symbol': symbol,  # Store symbol for quote lookups
			'qty': qty,
			'sl_pct': sl_pct,
			'tick': tick,
			'gtt_id': str(gtt_id),
			'trigger': trigger,
			'gtt_type': gtt_type,
			'target_pct': target_pct,
		})
		_trail_state[str(gtt_id)] = state
	_ensure_trailer_thread(kite)


@app.post("/api/order/buy")
def api_place_buy():
	"""Place a CNC LIMIT buy at LTP with budget INR 10,000 (adjust quantity)."""
	try:
		payload = request.get_json(silent=True) or {}
		symbol = (payload.get('symbol') or '').strip().upper()
		budget = float(payload.get('budget', 10000))
		offset_pct = float(payload.get('offset_pct', 0.001))
		# Enable trailing SL by default unless explicitly disabled
		with_tsl = bool(payload.get('tsl', True))
		sl_pct = float(payload.get('sl_pct', 0.05))
		use_market = bool(payload.get('market', False))
		if not symbol:
			return jsonify({"error": "symbol is required"}), 400
		order_logger.info("ORDER_REQ symbol=%s budget=%.2f offset=%.4f market=%s tsl=%s sl_pct=%.3f from=%s", symbol, budget, offset_pct, use_market, with_tsl, sl_pct, request.remote_addr)
		kite = get_kite()
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
		order_id = kite.place_order(**order_kwargs)
		gtt_id = None
		if with_tsl:
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
				# Don't fail the buy if GTT fails; return both
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
					"gtt_error": str(ge)
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
		return jsonify({
			"status": "ok",
			"order_id": order_id,
			"symbol": symbol,
			"limit_price": limit_price,
			"quantity": qty,
			"gtt_id": gtt_id
		})
	except Exception as e:
		error_logger.exception("/api/order/buy failed: %s", e)
		return jsonify({"error": str(e)}), 500


@app.get("/api/orders")
def api_orders():
	"""Return orders placed via this app with live LTP, trailing stop, and PnL."""
	try:
		access_logger.info("GET /api/orders from %s", request.remote_addr)
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
				except Exception:
					pass
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
								except Exception:
									pass
							if vals:
								mx = max(vals)
								mn = min(vals)
								cand_high = mx if cand_high is None else max(cand_high, mx)
								cand_low = mn if cand_low is None else min(cand_low, mn)

						if cand_high is not None:
							target_price = cand_high if (target_price is None or cand_high > target_price) else target_price
						if cand_low is not None:
							stop_price = cand_low if (stop_price is None or cand_low < stop_price) else stop_price
					except Exception:
						pass
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
				except Exception:
					pass
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
			except Exception:
				pass
			try:
				if (stop_price is None or not isinstance(stop_price, (int,float))) and rec.get('gtt_stop_price') is not None:
					tmp2 = rec.get('gtt_stop_price')
					if isinstance(tmp2, (int,float)):
						stop_price = float(tmp2)
			except Exception:
				pass
			# recompute percent fields if LTP available
			try:
				if isinstance(ltp, (int,float)) and ltp>0:
					if isinstance(target_price, (int,float)):
						target_pct_from_ltp = ((target_price/float(ltp)) - 1.0) * 100.0
					if isinstance(stop_price, (int,float)):
						stop_pct_from_ltp = (1.0 - (stop_price/float(ltp))) * 100.0
			except Exception:
				pass
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
	def api_cancel_order():
		"""Cancel a broker order by order_id (JSON body: {"order_id": "..."})."""
		try:
			access_logger.info("POST /api/order/cancel from %s", request.remote_addr)
			payload = request.get_json(silent=True) or {}
			order_id = str(payload.get('order_id') or payload.get('orderId') or '').strip()
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
			return jsonify({"status": "ok", "order_id": order_id, "result": res})
		except Exception as e:
			error_logger.exception("/api/order/cancel failed: %s", e)
			return jsonify({"error": str(e)}), 500


	@app.post('/api/gtt/cancel')
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
			except Exception:
				pass
			return jsonify({"status": "ok", "gtt_id": gtt_id, "result": res})
		except Exception as e:
			error_logger.exception("/api/gtt/cancel failed: %s", e)
			return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
	# Use 5050 default to avoid macOS AirPlay occupying 5000
	app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5050)), debug=True)

