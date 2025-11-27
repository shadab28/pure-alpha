"""Kite authentication and post-login data bootstrap.

Responsibilities:
    - Load API credentials (env or interactive prompt).
    - Validate existing access token (token.txt) and short-circuit if valid.
    - Obtain new access token via interactive or provided request token.
    - Bootstrap local CSV data (holdings, per-symbol histories, universe).

Public CLI:
        python auth.py [--request-token TOKEN] [--api-key KEY] [--api-secret SECRET]
                                    [--save-env] [--no-interactive]

Key functions:
    obtain_access_token_interactive, validate_token, bootstrap_data
"""
from __future__ import annotations

import os
import csv
import yaml  # for reading param.yaml to resolve universe symbols without writing CSV
import argparse
import getpass
import logging
from typing import Tuple, Optional
from kiteconnect import KiteConnect
try:
    from pgAdmin_database import persist
except Exception:
    persist = None  # type: ignore


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader: parse KEY=VALUE lines and set in os.environ if absent."""
    if not os.path.exists(path):
        return
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            # Do not overwrite existing env vars
            os.environ.setdefault(k, v)


# Developer convenience: load .env if present (but ensure .env is gitignored)
load_dotenv()


TOKEN_FILE = "token.txt"


def read_token_file(path: str = TOKEN_FILE) -> Optional[str]:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def write_token_file(token: str, path: str = TOKEN_FILE) -> None:
    with open(path, "w") as f:
        f.write(token)


def prompt_and_save_env(interactive: bool = True, save_default: bool = False) -> Tuple[str, str]:
    """Ask for API key/secret and optionally save to .env.

    Returns (api_key, api_secret)
    """
    if not interactive:
        raise RuntimeError("Interactive input required but disabled.")

    print("KITE credentials not found in environment.")
    try:
        api_key = input("Enter KITE_API_KEY: ").strip()
        api_secret = getpass.getpass("Enter KITE_API_SECRET (input hidden): ").strip()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit("Input cancelled.")

    if not api_key or not api_secret:
        raise SystemExit("Both API key and secret are required.")

    save = "y" if save_default else input("Save these to local .env for future runs? [y/N]: ").strip().lower()
    if save == "y":
        with open(".env", "w") as f:
            f.write(f"KITE_API_KEY={api_key}\n")
            f.write(f"KITE_API_SECRET={api_secret}\n")
        print("Wrote credentials to .env (ensure .env is in .gitignore).")

    return api_key, api_secret


def get_env_credentials() -> Tuple[Optional[str], Optional[str]]:
    api_key = os.getenv("KITE_API_KEY")
    api_secret = os.getenv("KITE_API_SECRET")

    # Fallback: read cred.ini if env vars are missing
    if not api_key or not api_secret:
        cred_path = "cred.ini"
        if os.path.exists(cred_path):
            try:
                with open(cred_path, "r") as cf:
                    for line in cf:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if k == "KITE_API_KEY" and not api_key:
                            api_key = v
                        if k == "KITE_API_SECRET" and not api_secret:
                            api_secret = v
            except Exception:
                # ignore parse errors and keep env values
                pass

    return api_key, api_secret


def validate_token(api_key: str, access_token: str, timeout_seconds: int = 10) -> bool:
    """Return True if the token appears valid (smoke test using profile())."""
    kite = KiteConnect(api_key=api_key)
    try:
        kite.set_access_token(access_token)
        # simple smoke call
        kite.profile()
        return True
    except Exception:
        return False


def fetch_and_save_holdings(api_key: str, access_token: str, out_path: str = 'Csvs/holdings_snapshot.csv') -> bool:
    """Fetch holdings from KiteConnect and write a CSV snapshot.

    This is a best-effort helper used during authentication flows so the repo has
    a local copy of holdings even when the separate fetch_holdings script is not run.
    """
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    try:
        holdings = kite.holdings()
    except Exception as e:
        print('Warning: failed to fetch holdings:', e)
        return False

    try:
        if persist is not None:
            try:
                persist.ensure_tables()
                persist.upsert_holdings(holdings)
            except Exception as e:
                print('Warning: holdings DB upsert failed:', e)
        return True
    except Exception as e:
        print('Warning: holdings persistence failed:', e)
        return False


def fetch_and_save_holdings_200d(api_key: str, access_token: str, out_path: str = 'Csvs/holdings_200d.csv', days: int = 200) -> bool:
    """For each holding, fetch the last `days` daily historical closes and save the most recent close
    from ~days ago (i.e. the close roughly 200 trading days ago) into a CSV.

    The CSV columns will be: instrument_token,tradingsymbol,close_200d,date_200d

    This is best-effort: if historical data is unavailable for a symbol, an empty row will be written
    with the tradingsymbol and instrument_token.
    """
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    try:
        holdings = kite.holdings()
    except Exception as e:
        print('Warning: failed to fetch holdings for 200d fetch:', e)
        return False

    try:
        rows_for_db = []
        for h in holdings:
            token = h.get('instrument_token')
            symbol = h.get('tradingsymbol')
            close_val = None
            vol_val = None
            close_date = None
            if token is not None:
                try:
                    from datetime import datetime, timedelta, timezone
                    to_dt = datetime.now(timezone.utc)
                    from_dt = to_dt - timedelta(days=int(days * 1.5) + 10)
                    candles = None
                    try:
                        itoken = int(token)
                        candles = kite.historical_data(itoken, from_dt, to_dt, 'day')
                    except Exception:
                        candles = None
                    if candles and len(candles) >= days:
                        c = candles[-days]
                    elif candles and len(candles) > 0:
                        c = candles[0]
                    else:
                        c = None
                    if c:
                        close_val = c.get('close')
                        vol_val = c.get('volume')
                        close_date = c.get('date') or c.get('timestamp')
                except Exception as e:
                    print(f'Warning: failed historical for {symbol} ({token}):', e)
            rows_for_db.append({
                'instrument_token': token,
                'tradingsymbol': symbol,
                'close_200d': close_val,
                'volume_200d': vol_val,
                'date_200d': close_date,
            })
        if persist is not None:
            try:
                persist.ensure_tables()
                persist.upsert_holdings_200d(rows_for_db)
            except Exception as e:
                print('Warning: holdings_200d DB upsert failed:', e)
        return True
    except Exception as e:
        print('Warning: holdings 200d persistence failed:', e)
        return False


def fetch_and_save_holdings_per_symbol(api_key: str, access_token: str, days: int = 200, out_dir: str = 'Csvs/csv_stock') -> bool:
    """For each holding, fetch the last `days` daily closes and write a per-symbol CSV
    at `out_dir/{TRADINGSYMBOL}.csv` with header: date,close (oldest first, newest last).

    Returns True if at least one file was written.
    """
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    try:
        holdings = kite.holdings()
    except Exception as e:
        print('Warning: failed to fetch holdings for per-symbol 200d fetch:', e)
        return False

    any_written = False

    ohlcv_rows = []
    for h in holdings:
        token = h.get('instrument_token')
        symbol = h.get('tradingsymbol')
        exch = h.get('exchange') or 'NSE'
        if not symbol:
            continue

        try:
            from datetime import datetime, timezone, timedelta

            to_dt = datetime.now(timezone.utc)
            from_dt = to_dt - timedelta(days=int(days * 1.5) + 10)

            candles = None
            try:
                itoken = int(token)
                candles = kite.historical_data(itoken, from_dt, to_dt, "day")
            except Exception as e:
                    # fallback: try symbol:exchange using instruments list later if needed
                    # Detect invalid token error and short-circuit further fetches
                    msg = str(e)
                    if 'Incorrect `api_key` or `access_token`' in msg:
                        print('Warning: kite API rejected credentials; aborting per-symbol historical fetches.')
                        return any_written
                    print(f'Warning: historical_data failed for {symbol} ({token}):', e)
                    candles = None

            if not candles:
                print(f"Warning: no historical candles for {symbol} ({token})")
                continue

            # Keep only the last `days` candles (most recent last)
            selected = candles[-days:] if len(candles) >= days else candles
            # accumulate for DB
            for c in selected:
                date = c.get('date') or c.get('timestamp')
                ohlcv_rows.append({
                    'symbol': symbol,
                    'interval': '1d',
                    'open': c.get('open'),
                    'high': c.get('high'),
                    'low': c.get('low'),
                    'close': c.get('close'),
                    'volume': c.get('volume'),
                    'ts': date,
                })

            any_written = True
            # CSV output removed
        except Exception as e:
            print(f'Warning: failed processing per-symbol candles for {symbol}:', e)

    # DB bulk insert
    if any_written and ohlcv_rows:
        try:
            from pgAdmin_database.ohlcv_utils import add_many_ohlcv
            add_many_ohlcv(ohlcv_rows)
        except Exception as e:
            print('Warning: OHLCV DB bulk insert (holdings) failed:', e)
    return any_written


def _load_universe_symbols_from_params(param_path: str = 'param.yaml') -> list[str]:
    """Resolve universe symbols directly from param.yaml + tools/NiftySymbol.py.

    This replaces prior CSV-based universe resolution so we avoid writing/reading
    Csvs/universe_from_params.csv. Returns an empty list on any failure.
    """
    symbols: list[str] = []
    try:
        if not os.path.exists(param_path):
            return []
        with open(param_path, 'r') as f:
            params = yaml.safe_load(f) or {}
        uni_name = params.get('universe_list')
        if not uni_name:
            return []
        try:
            from tools import NiftySymbol as ns  # type: ignore
        except Exception:
            try:
                import NiftySymbol as ns  # type: ignore
            except Exception:
                return []
        sym_obj = getattr(ns, uni_name, None)
        if isinstance(sym_obj, (list, tuple)):
            symbols = [s for s in sym_obj if isinstance(s, str) and s.strip()]
    except Exception:
        return []
    return symbols


def fetch_and_save_universe_200d(api_key: str, access_token: str, days: int = 200) -> bool:
    """Fetch last ~`days` daily candles for each universe symbol (defined in param.yaml).

    Previously this wrote per-symbol CSVs; now it only performs fetches (best-effort) so that
    downstream logic relying on recent retrieval side-effects can proceed. No CSVs are written.
    Returns True if at least one symbol had data fetched.
    """
    symbols = _load_universe_symbols_from_params()
    if not symbols:
        print('Warning: no universe symbols resolved from param.yaml; skipping universe 200d fetch')
        return False

    # Use kite to fetch historical for each symbol
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    any_written = False

    for symbol in symbols:
        try:
            from datetime import datetime, timedelta, timezone

            to_dt = datetime.now(timezone.utc)
            from_dt = to_dt - timedelta(days=int(days * 1.5) + 10)

            candles = None
            # Try to resolve instrument token from instruments.csv for symbol
            token = None
            try:
                # read instruments.csv and find matching tradingsymbol
                inst_path = os.path.join('Csvs', 'instruments.csv')
                if os.path.exists(inst_path):
                    with open(inst_path, newline='') as inf:
                        ir = csv.reader(inf)
                        hdr = next(ir, None)
                        # find index of tradingsymbol and instrument_token
                        ts_idx = None
                        it_idx = None
                        if hdr:
                            for i, h in enumerate(hdr):
                                if h == 'tradingsymbol':
                                    ts_idx = i
                                if h == 'instrument_token':
                                    it_idx = i
                        for r in ir:
                            if ts_idx is not None and r[ts_idx] == symbol:
                                try:
                                    token = int(r[it_idx]) if it_idx is not None and r[it_idx] else None
                                except Exception:
                                    token = None
                                break
            except Exception:
                token = None

            if token is not None:
                try:
                    candles = kite.historical_data(token, from_dt, to_dt, 'day')
                except Exception as e:
                    msg = str(e)
                    if 'Incorrect `api_key` or `access_token`' in msg:
                        print('Warning: kite API rejected credentials; aborting universe historical fetches.')
                        return any_written
                    print(f'Warning: historical_data by token failed for {symbol}:', e)
                    candles = None

            if not candles:
                # No token or failed by token; try using symbol directly (may require exchange suffix)
                try:
                    candles = kite.historical_data(symbol, from_dt, to_dt, 'day')
                except Exception as e:
                    msg = str(e)
                    if 'Incorrect `api_key` or `access_token`' in msg:
                        print('Warning: kite API rejected credentials; aborting universe historical fetches.')
                        return any_written
                    print(f'Warning: historical_data by symbol failed for {symbol}:', e)
                    candles = None

            if not candles:
                print(f'Warning: no historical candles for universe symbol {symbol}')
                continue

            selected = candles[-days:] if len(candles) >= days else candles

            any_written = True
            # CSV output removed
        except Exception as e:
            print(f'Warning: failed processing universe symbol {symbol}:', e)

    return any_written


def fetch_and_save_universe_per_symbol(api_key: str, access_token: str, days: int = 200) -> bool:
    """Fetch last `days` daily candles for each universe symbol (from param.yaml) and bulk insert
    into Postgres OHLCV table. No CSVs are written.

    Returns True if data for at least one symbol was inserted.
    """
    symbols = _load_universe_symbols_from_params()
    if not symbols:
        print('Warning: no universe symbols resolved from param.yaml; skipping universe per-symbol fetch')
        return False

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    any_written = False

    ohlcv_rows = []
    for symbol in symbols:
        try:
            from datetime import datetime, timezone, timedelta

            to_dt = datetime.now(timezone.utc)
            from_dt = to_dt - timedelta(days=int(days * 1.5) + 10)

            candles = None
            # Try to resolve instrument token from instruments.csv for symbol
            token = None
            try:
                inst_path = os.path.join('Csvs', 'instruments.csv')
                if os.path.exists(inst_path):
                    with open(inst_path, newline='') as inf:
                        ir = csv.reader(inf)
                        hdr = next(ir, None)
                        ts_idx = None
                        it_idx = None
                        if hdr:
                            for i, h in enumerate(hdr):
                                if h == 'tradingsymbol':
                                    ts_idx = i
                                if h == 'instrument_token':
                                    it_idx = i
                        for r in ir:
                            if ts_idx is not None and r[ts_idx] == symbol:
                                try:
                                    token = int(r[it_idx]) if it_idx is not None and r[it_idx] else None
                                except Exception:
                                    token = None
                                break
            except Exception:
                token = None

            if token is not None:
                try:
                    candles = kite.historical_data(token, from_dt, to_dt, 'day')
                except Exception as e:
                    msg = str(e)
                    if 'Incorrect `api_key` or `access_token`' in msg:
                        print('Warning: kite API rejected credentials; aborting universe historical fetches.')
                        return any_written
                    print(f'Warning: historical_data by token failed for {symbol}:', e)
                    candles = None

            if not candles:
                # try using symbol directly
                try:
                    candles = kite.historical_data(symbol, from_dt, to_dt, 'day')
                except Exception as e:
                    msg = str(e)
                    if 'Incorrect `api_key` or `access_token`' in msg:
                        print('Warning: kite API rejected credentials; aborting universe historical fetches.')
                        return any_written
                    print(f'Warning: historical_data by symbol failed for {symbol}:', e)
                    candles = None

            if not candles:
                print(f'Warning: no historical candles for universe symbol {symbol}')
                continue

            selected = candles[-days:] if len(candles) >= days else candles
            for c in selected:
                date = c.get('date') or c.get('timestamp')
                ohlcv_rows.append({
                    'symbol': symbol,
                    'interval': '1d',
                    'open': c.get('open'),
                    'high': c.get('high'),
                    'low': c.get('low'),
                    'close': c.get('close'),
                    'volume': c.get('volume'),
                    'ts': date,
                })

            any_written = True
            # CSV output removed
        except Exception as e:
            print(f'Warning: failed processing universe symbol {symbol}:', e)

    if any_written and ohlcv_rows:
        try:
            from pgAdmin_database.ohlcv_utils import add_many_ohlcv
            add_many_ohlcv(ohlcv_rows)
        except Exception as e:
            print('Warning: OHLCV DB bulk insert (universe) failed:', e)
    return any_written


def fetch_and_save_universe_15m(api_key: str, access_token: str, bars: int = 200) -> bool:
    """Fetch last `bars` 15-minute candles for each universe symbol and bulk insert into Postgres.

    Time window: fetch ~7 days of 15m data (covers > 200 bars) then slice last `bars`.
    Uses same token resolution logic as daily functions. Best-effort.
    Returns True if at least one symbol produced rows.
    """
    symbols = _load_universe_symbols_from_params()
    if not symbols:
        print('Warning: no universe symbols resolved from param.yaml; skipping universe 15m fetch')
        return False
    # Only run this heavy 15m fetch during weekdays by default.
    from datetime import datetime
    if datetime.now().weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        print('Info: today is weekend; skipping universe 15m fetch by default')
        return False

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    any_written = False
    ohlcv_rows = []
    from datetime import datetime, timezone, timedelta
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=7)  # generous window to ensure >=200 bars
    for symbol in symbols:
        try:
            # resolve instrument token from instruments.csv
            token = None
            try:
                inst_path = os.path.join('Csvs', 'instruments.csv')
                if os.path.exists(inst_path):
                    with open(inst_path, newline='') as inf:
                        ir = csv.reader(inf)
                        hdr = next(ir, None)
                        ts_idx = it_idx = None
                        if hdr:
                            for i, h in enumerate(hdr):
                                if h == 'tradingsymbol': ts_idx = i
                                if h == 'instrument_token': it_idx = i
                        for r in ir:
                            if ts_idx is not None and r[ts_idx] == symbol:
                                try:
                                    token = int(r[it_idx]) if it_idx is not None and r[it_idx] else None
                                except Exception:
                                    token = None
                                break
            except Exception:
                token = None
            candles = None
            if token is not None:
                try:
                    candles = kite.historical_data(token, from_dt, to_dt, '15minute')
                except Exception as e:
                    msg = str(e)
                    if 'Incorrect `api_key` or `access_token`' in msg:
                        print('Warning: kite API rejected credentials; aborting universe 15m fetches.')
                        return any_written
                    print(f'Warning: 15m historical_data by token failed for {symbol}:', e)
                    candles = None
            if not candles:
                try:
                    candles = kite.historical_data(symbol, from_dt, to_dt, '15minute')
                except Exception as e:
                    msg = str(e)
                    if 'Incorrect `api_key` or `access_token`' in msg:
                        print('Warning: kite API rejected credentials; aborting universe 15m fetches.')
                        return any_written
                    print(f'Warning: 15m historical_data by symbol failed for {symbol}:', e)
                    candles = None
            if not candles:
                print(f'Warning: no 15m candles for universe symbol {symbol}')
                continue
            selected = candles[-bars:] if len(candles) >= bars else candles
            for c in selected:
                date = c.get('date') or c.get('timestamp')
                ohlcv_rows.append({
                    'symbol': symbol,
                    'interval': '15m',
                    'open': c.get('open'),
                    'high': c.get('high'),
                    'low': c.get('low'),
                    'close': c.get('close'),
                    'volume': c.get('volume'),
                    'ts': date,
                })
            any_written = True
        except Exception as e:
            print(f'Warning: failed processing 15m universe symbol {symbol}:', e)
    if any_written and ohlcv_rows:
        try:
            from pgAdmin_database.ohlcv_utils import add_many_ohlcv
            add_many_ohlcv(ohlcv_rows)
        except Exception as e:
            print('Warning: OHLCV DB bulk insert (universe 15m) failed:', e)
    return any_written


def save_universe_csv_names(*_args, **_kwargs) -> bool:  # legacy stub retained for compatibility
    """Deprecated: previously wrote a CSV listing universe per-symbol CSV filenames.
    Now returns False (no action) because universe data is DB-only."""
    return False


def update_universe_csv() -> bool:  # legacy stub to avoid breaking callers
    """Deprecated: universe CSV generation removed (DB-only persistence)."""
    return False

def obtain_access_token_interactive(api_key: str, api_secret: str) -> str:
    kite = KiteConnect(api_key=api_key)
    print("Login URL:", kite.login_url())
    request_token = input("Enter Request Token here: ")
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data.get("access_token")
    if not access_token:
        raise SystemExit("Failed to obtain access token from Kite. Check request token and API keys.")
    return access_token

def bootstrap_data(api_key: str, access_token: str, force_15m: bool = False) -> None:
    """Run all non-critical data fetches after obtaining/validating a token.

    Each step is best-effort; failures are logged at WARNING level.
    """
    try:
        fetch_and_save_holdings(api_key, access_token)
    except Exception as e:
        logging.warning('holdings snapshot failed: %s', e)
    try:
        fetch_and_save_holdings_200d(api_key, access_token)
    except Exception as e:
        logging.warning('holdings 200d snapshot failed: %s', e)
    try:
        fetch_and_save_holdings_per_symbol(api_key, access_token)
    except Exception as e:
        logging.warning('holdings per-symbol 200d failed: %s', e)
    try:
        fetch_and_save_universe_200d(api_key, access_token)
    except Exception as e:
        logging.warning('universe 200d snapshot failed: %s', e)
    try:
        fetch_and_save_universe_per_symbol(api_key, access_token)
    except Exception as e:
        logging.warning('universe per-symbol snapshot failed: %s', e)
    try:
        # allow forcing 15m fetch on weekends
        if force_15m:
            # bypass weekday check inside fetch function by calling directly
            fetch_and_save_universe_15m(api_key, access_token, bars=200)
        else:
            fetch_and_save_universe_15m(api_key, access_token)
    except Exception as e:
        logging.warning('universe 15m snapshot failed: %s', e)
    # universe CSV generation removed (DB-only)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Obtain and validate Kite access token")
    parser.add_argument("--request-token", help="Provide request token (non-interactive)")
    parser.add_argument("--save-env", action="store_true", help="Save provided API keys to .env without prompting")
    parser.add_argument("--api-key", help="KITE API key (override env)")
    parser.add_argument("--api-secret", help="KITE API secret (override env)")
    parser.add_argument("--no-interactive", action="store_true", help="Fail instead of prompting interactively")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--force-15m", action="store_true", help="Force fetching 15m candles even on weekends")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format='[%(levelname)s] %(message)s')

    api_key, api_secret = get_env_credentials()
    if args.api_key:
        api_key = args.api_key
    if args.api_secret:
        api_secret = args.api_secret

    if not api_key or not api_secret:
        if args.no_interactive:
            raise SystemExit("Missing API key/secret and interactive input disabled.")
        api_key, api_secret = prompt_and_save_env(interactive=not args.no_interactive, save_default=args.save_env)

    # If token exists, validate it first
    existing = read_token_file()
    if existing:
        if validate_token(api_key, existing):
            logging.info('Existing token valid. Bootstrapping data...')
            bootstrap_data(api_key, existing, force_15m=args.force_15m)
            return
        else:
            logging.info('Existing token invalid/expired. Attempting best-effort data refresh before new auth.')
            try:
                bootstrap_data(api_key, existing)
                logging.info('Best-effort refresh with invalid token complete.')
            except Exception as e:
                logging.debug('Refresh with invalid token failed: %s', e)
            # Continue to obtain new token

    # If request token provided via CLI, use non-interactive flow
    access_token = None
    if args.request_token:
        kite = KiteConnect(api_key=api_key)
        try:
            data = kite.generate_session(args.request_token, api_secret=api_secret)
            access_token = data.get("access_token")
        except Exception as e:
            raise SystemExit(f"Failed to generate session with provided request token: {e}")
    else:
        if args.no_interactive:
            raise SystemExit("No request token provided and interactive mode disabled.")
        access_token = obtain_access_token_interactive(api_key, api_secret)

    if not access_token:
        raise SystemExit("Failed to obtain access token.")

    if not validate_token(api_key, access_token):
        raise SystemExit("Obtained access token failed validation.")

    write_token_file(access_token)
    logging.info("Access token obtained and saved to token.txt. Bootstrapping data...")
    bootstrap_data(api_key, access_token, force_15m=args.force_15m)


if __name__ == "__main__":
    main()
