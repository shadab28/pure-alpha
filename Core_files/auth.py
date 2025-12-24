"""Kite authentication and post-login data bootstrap.

Responsibilities:
    - Load API credentials (env or interactive prompt).
    - Validate existing access token (token.txt) and short-circuit if valid.
    - Obtain new access token via interactive or provided request token.
    - Bootstrap local CSV data (holdings, per-symbol histories, universe).

Public CLI:
        python auth.py [--request-token TOKEN] [--api-key KEY] [--api-secret SECRET]
                                    [--save-env] [--no-interactive]
new algo

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


# Developer convenience: load .env from this folder if present (ensure .env is gitignored)
BASE_DIR = os.path.dirname(__file__)
load_dotenv(os.path.join(BASE_DIR, ".env"))


TOKEN_FILE = os.path.join(BASE_DIR, "token.txt")


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


def obtain_access_token_interactive(api_key: str, api_secret: str) -> str:
    kite = KiteConnect(api_key=api_key)
    print("Login URL:", kite.login_url())
    request_token = input("Enter Request Token here: ")
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data.get("access_token")
    if not access_token:
        raise SystemExit("Failed to obtain access token from Kite. Check request token and API keys.")
    return access_token

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

    # Save the validated token to Core_files/token.txt
    write_token_file(access_token, TOKEN_FILE)
    print(f"Saved access token to {TOKEN_FILE}")


if __name__ == "__main__":
    main()