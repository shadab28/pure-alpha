# Pure Alpha Trading System

## Quick Start

### 1. Setup Credentials
```bash
export KITE_API_KEY="your_api_key"
export KITE_API_SECRET="your_api_secret"
```

### 2. Generate Access Token
```bash
python3 Core_files/auth.py
# Creates Core_files/token.txt
```

### 3. Start the Application
```bash
python3 Webapp/main.py --port 5050
```

---

## Security & Documentation

| Category | Location |
|----------|----------|
| **Security Phases (1-3B)** | [`docs/SECURITY/`](docs/SECURITY/) |
| **Implementation Details** | [`docs/SECURITY/PHASE_3B_COMPLETION.md`](docs/SECURITY/PHASE_3B_COMPLETION.md) |
| **API Endpoints** | [`docs/SECURITY/SECURITY_GUIDE.md`](docs/SECURITY/SECURITY_GUIDE.md) |
| **UI Documentation** | [`docs/UI/`](docs/UI/) |
| **Reference Materials** | [`docs/REFERENCE/`](docs/REFERENCE/) |

---

## Security Status ✅

- ✅ **Phase 1:** Vulnerability fixes
- ✅ **Phase 2:** Rate limiting, XSS protection, health checks
- ✅ **Phase 3A:** Input validation, security headers, SQL audit
- ✅ **Phase 3B:** Authentication, CSRF protection, RBAC
- ⏳ **Phase 3C:** Data encryption (pending)

See [`docs/SECURITY/PHASE_3_PLAN.md`](docs/SECURITY/PHASE_3_PLAN.md) for roadmap.

---

## Important Notes

- **Never commit** `.env`, `token.txt`, or API keys (already in `.gitignore`)
- API credentials are environment-variable based (not hardcoded)
- For security details, see [`docs/SECURITY/`](docs/SECURITY/)

---

## File Structure

```
pure-alpha/
├── Webapp/              # Flask application
├── Core_files/          # Authentication, data fetching
├── codes/               # Jupyter notebooks, research
├── docs/
│   ├── SECURITY/        # Security phases and audits
│   ├── UI/              # UI and terminal customization
│   └── REFERENCE/       # Additional documentation
├── auth.py              # Authentication module
├── csrf_protection.py   # CSRF protection module
├── validation.py        # Input validation module
└── README.md            # This file
```
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
