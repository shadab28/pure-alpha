# pure-alpha webapp quick start

- Copy `.env.example` to `.env` and fill values. Do not commit `.env` (ignored).
- Generate `Core_files/token.txt` via `Core_files/auth.py` and keep it local.
- Start LTP dashboard:
  - Python: 3.11+
  - Run: `python3 -m flask --app Webapp/app.py run --host 0.0.0.0 --port 5050`

Notes
- `.gitignore` prevents secrets and local caches from being tracked.
- If `.env` or `token.txt` ever get added accidentally, run:
  - `git rm --cached .env Core_files/token.txt`
  - Commit and push.

Troubleshooting
- Import error for kiteconnect: project includes local `kiteconnect/` fallback.
- 15m candles come from Postgres `ohlcv_data`. Make sure the main ingestion is running.

# RSI-EMA Intraday Backtester

Files:
- `strategies/rsi_ema_intraday.py` - strategy implementation and utilities
- `backtest_runner.py` - simple backtester that selects top 10 by turnover at 09:25, runs strategy, and writes `sample_trade_log.csv`.

Usage:
- Ensure Aug 2025 CSVs are present in `Csvs/stock_data_aug_2025/` (they are in the repo).
- Create a virtualenv and install requirements:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

- Run the runner to produce a sample trade log:

```bash
python backtest_runner.py
```

Notes:
- This is a first pass. It implements the rules described and produces a trade log and basic metrics. Further tuning and validation recommended.
