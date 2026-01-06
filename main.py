#!/usr/bin/env python3
"""Subscribe to a configured universe and keep an in-memory last-price map.

- Resolves instrument tokens using a cached CSV (Support Files/param.yaml->instruments_csv)
  or falls back to Kite REST instruments fetch and caches it.
- Subscribes to tokens over KiteTicker in LTP mode.
- Maintains thread-safe dicts: by_token[token]=ltp, by_symbol[symbol]=ltp.

Environment required:
  KITE_API_KEY in env, and a valid access token present at Core_files/token.txt

CLI:
	python main.py [--universe <list_name>] [--mode ltp|quote] [--log-level INFO]
"""
from __future__ import annotations

import os
import csv
import sys
import time
from datetime import datetime, timedelta, timezone, time as dt_time
from zoneinfo import ZoneInfo
import argparse
import logging
import threading
from typing import Dict, List, Tuple

import yaml

from kiteconnect import KiteConnect, KiteTicker
from pgAdmin_database.db_connection import pg_cursor, test_connection


# Paths
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SUPPORT_DIR = os.path.join(REPO_ROOT, 'Support Files')
PARAM_YAML = os.path.join(SUPPORT_DIR, 'param.yaml')
TOKEN_FILE = os.path.join(REPO_ROOT, 'Core_files', 'token.txt')


# Shared LTP store (thread-safe)
class LTPStore:
	def __init__(self):
		self._lock = threading.RLock()
		self.by_token: Dict[int, float] = {}
		self.by_symbol: Dict[str, float] = {}

	def update_tick(self, instrument_token: int, last_price: float, token_to_symbol: Dict[int, str]):
		symbol = token_to_symbol.get(instrument_token)
		with self._lock:
			self.by_token[instrument_token] = last_price
			if symbol:
				self.by_symbol[symbol] = last_price

	def snapshot(self) -> Tuple[Dict[int, float], Dict[str, float]]:
		with self._lock:
			return dict(self.by_token), dict(self.by_symbol)


def load_params(path: str) -> dict:
	try:
		with open(path, 'r') as f:
			return yaml.safe_load(f) or {}
	except Exception:
		return {}


def load_dotenv(path: str) -> None:
	"""Minimal .env loader: parse KEY=VALUE lines and set if absent."""
	try:
		if not os.path.exists(path):
			return
		with open(path, 'r') as f:
			for line in f:
				line = line.strip()
				if not line or line.startswith('#') or '=' not in line:
					continue
				k, v = line.split('=', 1)
				k = k.strip()
				v = v.strip().strip('"').strip("'")
				os.environ.setdefault(k, v)
	except Exception:
		pass


def load_nifty_module():
	"""Dynamically import `Support Files/NiftySymbol.py` despite space in path."""
	import importlib.util
	module_path = os.path.join(SUPPORT_DIR, 'NiftySymbol.py')
	spec = importlib.util.spec_from_file_location('nifty_symbols', module_path)
	mod = importlib.util.module_from_spec(spec)
	assert spec and spec.loader, 'Failed to spec NiftySymbol.py'
	spec.loader.exec_module(mod)  # type: ignore[attr-defined]
	return mod


def resolve_universe(universe_name: str) -> List[str]:
	"""Return list of symbols for the requested universe from NiftySymbol.py."""
	mod = load_nifty_module()
	if hasattr(mod, universe_name):
		syms = getattr(mod, universe_name)
		if isinstance(syms, (list, tuple)):
			return list(syms)
	raise SystemExit(f"Universe '{universe_name}' not found in NiftySymbol.py. Update param.yaml:universe_list or NiftySymbol.py.")


def read_token_file(path: str = TOKEN_FILE) -> str:
	try:
		with open(path, 'r') as f:
			return f.read().strip()
	except Exception:
		raise SystemExit(f"Access token not found at {path}. Run Core_files/auth.py to generate it.")


def ensure_instruments_csv(kite: KiteConnect, csv_path: str) -> None:
	"""Ensure a simple instruments CSV with columns tradingsymbol,instrument_token exists."""
	if os.path.exists(csv_path):
		return
	logging.info("Instruments CSV not found, fetching from Kite and caching: %s", csv_path)
	rows = kite.instruments("NSE")
	os.makedirs(os.path.dirname(csv_path), exist_ok=True)
	with open(csv_path, 'w', newline='', encoding='utf-8') as f:
		w = csv.writer(f)
		w.writerow(["tradingsymbol", "instrument_token"])  # minimal columns
		for r in rows:
			try:
				w.writerow([r.get("tradingsymbol"), r.get("instrument_token")])
			except Exception:
				continue


def load_symbol_token_map(csv_path: str, symbols: List[str]) -> Tuple[Dict[str, int], Dict[int, str]]:
	"""Load mapping from instruments CSV for requested symbols.

	Returns (symbol->token, token->symbol). Only includes requested symbols.
	"""
	wanted = set(symbols)
	sym_to_token: Dict[str, int] = {}
	token_to_sym: Dict[int, str] = {}
	if not os.path.exists(csv_path):
		raise SystemExit(f"Instruments CSV missing at {csv_path}.")

	with open(csv_path, 'r', newline='', encoding='utf-8') as f:
		reader = csv.DictReader(f)
		for row in reader:
			tsym = row.get('tradingsymbol')
			tok = row.get('instrument_token')
			if not tsym or not tok:
				continue
			if tsym in wanted:
				try:
					itok = int(tok)
				except Exception:
					continue
				sym_to_token[tsym] = itok
				token_to_sym[itok] = tsym

	missing = [s for s in symbols if s not in sym_to_token]
	if missing:
		logging.warning("Symbols missing from instruments CSV (will be skipped): %s", ','.join(missing[:10]) + ("..." if len(missing) > 10 else ""))
	return sym_to_token, token_to_sym


def run(universe_name: str | None, mode: str, log_level: str):
	logging.basicConfig(level=getattr(logging, log_level.upper(), logging.INFO), format='[%(levelname)s] %(message)s')

	# Startup timestamp
	start_dt = datetime.now()
	logging.info("Program starting at %s", start_dt.isoformat(sep=' '))

	# Load params for instruments CSV path (and optionally default universe)
	params = load_params(PARAM_YAML)
	instruments_csv = params.get('instruments_csv') or os.path.join(REPO_ROOT, 'Csvs', 'instruments.csv')
	# If no universe provided via CLI, use param.yaml default
	if not universe_name:
		universe_name = params.get('universe_list', 'allstocks')

	# Resolve symbols
	symbols = resolve_universe(universe_name)
	logging.info("Universe '%s' size: %d", universe_name, len(symbols))

	# Auth
	# Load .env from repo root and Core_files for convenience
	load_dotenv(os.path.join(REPO_ROOT, '.env'))
	load_dotenv(os.path.join(REPO_ROOT, 'Core_files', '.env'))
	api_key = os.getenv('KITE_API_KEY')
	if not api_key:
		raise SystemExit("KITE_API_KEY not set in environment.")
	access_token = read_token_file(TOKEN_FILE)

	kite = KiteConnect(api_key=api_key)
	kite.set_access_token(access_token)

	# Ensure instruments cache and load mappings
	ensure_instruments_csv(kite, os.path.join(REPO_ROOT, instruments_csv))
	sym_to_token, token_to_sym = load_symbol_token_map(os.path.join(REPO_ROOT, instruments_csv), symbols)

	tokens = list(token_to_sym.keys())
	if not tokens:
		raise SystemExit("No tokens resolved for the selected universe. Check instruments CSV and symbols.")

	# LTP store
	store = LTPStore()
	start_time = time.time()

	# Per-symbol 15m candle aggregation (OHLC from LTP)
	class CandleAgg:
		def __init__(self):
			self.open: Dict[str, float] = {}
			self.high: Dict[str, float] = {}
			self.low: Dict[str, float] = {}
			self.close: Dict[str, float] = {}

		def update(self, symbol: str, ltp: float):
			if symbol not in self.open:
				self.open[symbol] = ltp
				self.high[symbol] = ltp
				self.low[symbol] = ltp
				self.close[symbol] = ltp
			else:
				self.high[symbol] = max(self.high[symbol], ltp)
				self.low[symbol] = min(self.low[symbol], ltp)
				self.close[symbol] = ltp

		def snapshot_rows(self, ts_dt: datetime, timeframe: str = '15m'):
			rows = []
			for s in self.open.keys():
				# (timeframe, stockname, candle_stock, open, high, low, close, volume)
				rows.append((timeframe, s, ts_dt, self.open[s], self.high[s], self.low[s], self.close[s], 0))
			return rows

		def reset(self):
			self.open.clear()
			self.high.clear()
			self.low.clear()
			self.close.clear()

	agg = CandleAgg()

	# Prepare DB table once (if DB available)
	def ensure_ltp_table():
		try:
			with pg_cursor() as (cur, _):
				cur.execute(
					"""
					CREATE TABLE IF NOT EXISTS ltp_snapshots (
						symbol TEXT NOT NULL,
						ts TIMESTAMP WITH TIME ZONE NOT NULL,
						ltp DOUBLE PRECISION NOT NULL
					)
					"""
				)
		except Exception as e:
			logging.warning("DB init skipped: %s", e)

	if test_connection():
		ensure_ltp_table()
	else:
		logging.warning("Database not reachable; snapshot saving will be skipped.")

	# Ticker callbacks
	def on_ticks(ws, ticks):  # noqa: ANN001
		for t in ticks:
			try:
				token = int(t.get('instrument_token'))
				ltp = float(t.get('last_price'))
			except Exception:
				continue
			store.update_tick(token, ltp, token_to_sym)
			sym = token_to_sym.get(token)
			if sym:
				agg.update(sym, ltp)
		# Print current LTPs for the arriving batch (small sample)
		To_print =False
		by_symbol = store.snapshot()[1]
		if by_symbol and To_print:
			# Print up to 10 symbols from this batch
			items = list(by_symbol.items())[:10]
			print("LTPs:", ", ".join([f"{s}: {p}" for s, p in items]))

	def on_connect(ws, response):  # noqa: ANN001
		logging.info("Ticker connected. Subscribing to %d tokens in %s mode...", len(tokens), mode)
		ws.subscribe(tokens)
		if mode.lower() == 'ltp':
			ws.set_mode(ws.MODE_LTP, tokens)
		elif mode.lower() == 'quote':
			ws.set_mode(ws.MODE_QUOTE, tokens)
		else:
			ws.set_mode(ws.MODE_LTP, tokens)

	def on_close(ws, code, reason):  # noqa: ANN001
		logging.info("Ticker closed: %s %s", code, reason)

	def on_error(ws, code, reason):  # noqa: ANN001
		logging.error("Ticker error: %s %s", code, reason)

	# Start websocket (threaded)
	kws = KiteTicker(api_key, access_token, debug=False)
	kws.on_ticks = on_ticks
	kws.on_connect = on_connect
	kws.on_close = on_close
	kws.on_error = on_error

	# Run in a background thread
	kws.connect(threaded=True)

	# Helper to round current time down to nearest 15-minute boundary
	def floor_to_15m(dt: datetime) -> datetime:
		minute = (dt.minute // 15) * 15
		return dt.replace(minute=minute, second=0, microsecond=0)

	def is_trading_bar(bar_end_dt: datetime) -> bool:
		"""Return True if bar_end_dt (IST) falls within trading hours (Mon-Fri, 09:15–15:30)."""
		# Monday=0, Sunday=6
		if bar_end_dt.weekday() >= 5:
			return False
		t = bar_end_dt.time()
		start = dt_time(9, 15)
		end = dt_time(15, 30)
		return start <= t <= end

	# Simple monitor loop
	try:
		last_status_print: float = 0.0
		while True:
			time.sleep(5)
			# Periodic status logging (every 60s)
			now_ts = time.time()
			if now_ts - last_status_print >= 60:
				symbol_count = len(store.by_symbol)
				agg_count = len(agg.open)
				logging.info("Status: %d symbols tracked, %d in aggregation", symbol_count, agg_count)
				last_status_print = now_ts
			# Persist at exact 00/15/30/45 minute boundaries
			# Use exchange timezone (IST) and then store naive timestamps (timestamp without time zone)
			now = datetime.now(ZoneInfo("Asia/Kolkata"))
			bar_end = floor_to_15m(now)
			# guard against duplicate writes within the same 15m window
			if not hasattr(run, "_last_saved_bar_end"):
				setattr(run, "_last_saved_bar_end", None)
			last_saved = getattr(run, "_last_saved_bar_end")
			# Save only when we are within the first 10 seconds of a 15m boundary and haven't saved it yet
			if now - bar_end < timedelta(seconds=10) and bar_end != last_saved:
				# Bar boundary reached; save only during trading hours, otherwise just reset.
				if is_trading_bar(bar_end) and test_connection():
					# Use the bar end as ts (naive) for TIMESTAMP WITHOUT TIME ZONE
					ts_naive = bar_end.replace(tzinfo=None)
					try:
						with pg_cursor() as (cur, _):
							# Ensure table exists with the expected schema
							cur.execute(
								"""
								CREATE TABLE IF NOT EXISTS ohlcv_data (
									timeframe VARCHAR(10) NOT NULL,
									stockname VARCHAR(50) NOT NULL,
									candle_stock TIMESTAMP WITHOUT TIME ZONE NOT NULL,
									open NUMERIC NOT NULL,
									high NUMERIC NOT NULL,
									low NUMERIC NOT NULL,
									close NUMERIC NOT NULL,
									volume BIGINT NOT NULL DEFAULT 0
								)
								"""
							)
							rows = agg.snapshot_rows(ts_naive, '15m')
							logging.info("Attempting to save candles at %s. Aggregation has %d symbols tracked.", ts_naive.isoformat(sep=' '), len(agg.open))
							if rows:
								cur.executemany(
									"INSERT INTO ohlcv_data(timeframe, stockname, candle_stock, open, high, low, close, volume) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
									rows,
								)
							logging.info("Saved %d 15m candles to ohlcv_data at %s.", len(rows), ts_naive.isoformat(sep=' '))
					except Exception as e:
						logging.warning("Failed to save 15m candles: %s", e)
				# Whether saved or not, mark boundary processed and reset aggregation
				setattr(run, "_last_saved_bar_end", bar_end)
				agg.reset()
				if not is_trading_bar(bar_end):
					logging.debug("Skipped saving candle outside trading hours at %s.", bar_end.isoformat())
			# Optional periodic debug
			# logging.debug("Heartbeat: %d symbols tracked", len(store.by_symbol))
	except KeyboardInterrupt:
		logging.info("Interrupted; stopping ticker...")
		try:
			kws.stop()
		except Exception:
			pass


def main(argv=None):
	parser = argparse.ArgumentParser(description="Subscribe to a 200-stock universe and store LTPs.")
	parser.add_argument('--universe', default=None, help='Universe variable name in NiftySymbol.py (default: from param.yaml)')
	parser.add_argument('--mode', default='ltp', choices=['ltp', 'quote'], help='Streaming mode')
	parser.add_argument('--log-level', default='INFO', help='Logging level (DEBUG, INFO, WARNING)')
	args = parser.parse_args(argv)

	run(args.universe, args.mode, args.log_level)


if __name__ == '__main__':
	main()

