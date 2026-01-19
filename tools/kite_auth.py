#!/usr/bin/env python3
"""Helper to run Kite Connect auth flow interactively.

Usage: python3 tools/kite_auth.py

This script reads `KITE_API_KEY` and `KITE_API_SECRET` from .env (project root),
prints (and attempts to open) the Kite login URL, then asks you to paste the
full redirect URL you get after login. It extracts the request_token, exchanges
it for an access token, writes the token into `.env` (KITE_ACCESS_TOKEN) and
saves it in `Core_files/token.txt` for other scripts.

Do NOT commit `.env` after this if it contains real secrets.
"""
import os
import re
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / '.env'
TOKEN_FILE = ROOT / 'Core_files' / 'token.txt'


def read_env(path: Path):
    vals = {}
    if not path.exists():
        return vals
    with path.open('r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                vals[k.strip()] = v.strip()
    return vals


def write_env(path: Path, data: dict):
    # Preserve comments and order where possible; simple rewrite for now
    lines = []
    if path.exists():
        with path.open('r') as f:
            for line in f:
                if '=' in line and not line.strip().startswith('#'):
                    k = line.split('=', 1)[0].strip()
                    if k in data:
                        lines.append(f"{k}={data[k]}\n")
                        data.pop(k)
                        continue
                lines.append(line)
    # append any remaining
    for k, v in data.items():
        lines.append(f"{k}={v}\n")
    with path.open('w') as f:
        f.writelines(lines)


try:
    env = read_env(ENV_PATH)
    api_key = env.get('KITE_API_KEY') or os.getenv('KITE_API_KEY')
    api_secret = env.get('KITE_API_SECRET') or os.getenv('KITE_API_SECRET')
    if not api_key or not api_secret:
        print('KITE_API_KEY or KITE_API_SECRET not found in .env or environment.')
        sys.exit(1)

    # Import kiteconnect lazily
    try:
        from kiteconnect import KiteConnect
    except Exception as e:
        print('kiteconnect not available:', e)
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)
    login_url = kite.login_url()
    print('\nOpen this URL in your browser and complete login:\n')
    print(login_url)
    try:
        webbrowser.open(login_url)
    except Exception:
        pass

    redirected = input('\nAfter login, paste the full URL you were redirected to (the one containing request_token=),\nOR paste just the request_token value itself:\n')
    # Accept either the full redirect URL or a raw request_token pasted by the user.
    m = re.search(r'request_token=([^&]+)', redirected)
    if m:
        request_token = m.group(1)
    else:
        # If the user pasted only the token (alphanumeric), accept it directly.
        token_candidate = redirected.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{8,}", token_candidate):
            request_token = token_candidate
            print('Detected raw request_token; proceeding with exchange...')
        else:
            print('Could not find request_token in the pasted input. Aborting.')
            sys.exit(1)

    print('\nExchanging request_token for access token...')
    try:
        data = kite.generate_session(request_token, api_secret=api_secret)
    except Exception as e:
        print('generate_session failed:', e)
        sys.exit(1)

    access_token = data.get('access_token')
    if not access_token:
        print('No access_token returned:', data)
        sys.exit(1)

    print('\nAccess token obtained:')
    print(access_token)

    # Persist to .env and Core_files/token.txt
    write_env(ENV_PATH, {'KITE_ACCESS_TOKEN': access_token})
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TOKEN_FILE.open('w') as f:
        f.write(access_token)

    print(f'Wrote access token to {ENV_PATH} (KITE_ACCESS_TOKEN) and {TOKEN_FILE}')
    print('Do NOT commit .env with real credentials.')

except KeyboardInterrupt:
    print('\nInterrupted.')
    sys.exit(1)

