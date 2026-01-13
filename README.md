# pure-alpha webapp quick start

## ⚠️ IMPORTANT SECURITY NOTICE

**API Key Setup Required:** See [SECURITY_ADVISORY.md](docs/SECURITY_ADVISORY.md) for critical security update.

## Setup Instructions

1. **Set API credentials** via environment variables:
   ```bash
   export KITE_API_KEY="your_api_key"
   export KITE_API_SECRET="your_api_secret"
   ```
   
   Or copy `.env.example` to `.env` and fill values:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   source .env
   ```

2. **Generate access token** via `Core_files/auth.py`:
   ```bash
   python3 Core_files/auth.py
   ```
   This creates `Core_files/token.txt` (keep it local, don't commit)

3. **Start LTP dashboard** (Python 3.11+ required):
   ```bash
   python3 Webapp/main.py --port 5050
   ```
   Or: `python3 -m flask --app Webapp/app.py run --host 127.0.0.1 --port 5050`

## Security Notes

- **Never commit `.env`, `token.txt`, or API keys** - already in `.gitignore`
- API key is loaded from `KITE_API_KEY` environment variable (not hardcoded)
- Access token is stored in `Core_files/token.txt` (local only, not committed)
- If secrets accidentally get added, run:
  ```bash
  git rm --cached .env Core_files/token.txt
  git commit -m "Remove accidentally committed secrets"
  ```
- **For git history cleanup:** See [SECURITY_ADVISORY.md](docs/SECURITY_ADVISORY.md)

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
