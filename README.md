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
