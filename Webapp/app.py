"""Flask app wiring routes to LTP service and using external template."""
from __future__ import annotations

import os
import sys
from flask import Flask, jsonify, render_template, Response

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

app = Flask(__name__, template_folder="templates")


@app.route("/api/ltp")
def api_ltp():
	try:
		return jsonify(fetch_ltp())
	except Exception as e:
		return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
	return render_template("index.html")


@app.route("/export/ltp.csv")
def export_ltp_csv():
	try:
		data = fetch_ltp()
		rows = ["symbol,last_price,last_close_15m,sma200_15m,sma50_15m,ratio_15m_50_200,pct_vs_15m_sma200,pct_vs_daily_sma50,daily_sma50,daily_sma200,daily_ratio_50_200"]
		for sym, info in data.get("data", {}).items():
			rows.append(
				f"{sym},{info.get('last_price','')},{info.get('last_close','')},{info.get('sma200_15m','')},{info.get('sma50_15m','')},{info.get('ratio_15m_50_200','')},{info.get('pct_vs_15m_sma200','')},{info.get('pct_vs_daily_sma50','')},{info.get('daily_sma50','')},{info.get('daily_sma200','')},{info.get('daily_ratio_50_200','')}"
			)
		csv_content = "\n".join(rows) + "\n"
		return Response(csv_content, mimetype="text/csv", headers={
			"Content-Disposition": "attachment; filename=ltp.csv"
		})
	except Exception as e:
		return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
	# Use 5050 default to avoid macOS AirPlay occupying 5000
	app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5050)), debug=True)

