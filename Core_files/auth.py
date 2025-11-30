#!/usr/bin/env python3
"""Kite Connect auth helper.

Reads environment variables from `Core_files/.env` and performs a Kite Connect
login flow to obtain an access token and save it to `token.txt`.

Usage: python Core_files/auth.py
"""
import json
import os
import sys
import webbrowser
from datetime import datetime

try:
    from kiteconnect import KiteConnect
except Exception:
    KiteConnect = None


def load_env(path):
    """Minimal .env loader that returns a dict of KEY=VALUE.

    Ignores blank lines and lines starting with #. Also tolerates files
    that are fenced with triple-backticks (```) like the repo's `.env`.
    """
    env = {}
    if not os.path.exists(path):
        return env

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            if line.startswith('```'):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def save_token(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def main():
    base = os.path.dirname(__file__)
    env_path = os.path.join(base, ".env")
    env = load_env(env_path)

    api_key = env.get("KITE_API_KEY")
    api_secret = env.get("KITE_API_SECRET")

    if not api_key:
        print("Missing KITE_API_KEY in Core_files/.env")
        sys.exit(1)

    if KiteConnect is None:
        print("Error: kiteconnect package not available. Install with `pip install kiteconnect`.")
        sys.exit(1)

    kite = KiteConnect(api_key=api_key)

    token_file = os.path.join(base, "token.txt")

    # Always obtain a fresh token — ignore any KITE_ACCESS_TOKEN in .env
    print("Starting fresh authentication (ignoring any KITE_ACCESS_TOKEN in .env).")

    # If token.txt already exists, warn but continue (will overwrite)
    if os.path.exists(token_file):
        print(f"{token_file} already exists and will be overwritten with a fresh session.")

    login_url = kite.login_url()
    print("Open this URL in a browser to login and authorize the app:")
    print(login_url)
    try:
        webbrowser.open_new_tab(login_url)
    except Exception:
        pass

    # Attempt two automatic capture methods in parallel:
    # 1) threaded local HTTP server that reads request_token from redirect query
    # 2) clipboard polling (macOS pbpaste) to detect the redirect URL that contains request_token
    request_token = None
    try:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.parse import urlparse, parse_qs
        import threading
        import time
        import re
        import subprocess

        token_box = {"token": None}
        token_event = threading.Event()

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                qs = urlparse(self.path).query
                params = parse_qs(qs)
                if 'request_token' in params:
                    token_box['token'] = params['request_token'][0]
                    token_event.set()
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    # Provide a small page that attempts to copy the query to clipboard
                    self.wfile.write(b"<html><body><h3>Authentication complete.</h3><p>You can close this window.</p>"
                                     b"<script>try{navigator.clipboard.writeText(window.location.search);}catch(e){};</script></body></html>")
                else:
                    # reply with a helpful message
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b"<html><body><p>No request_token found in URL.</p></body></html>")

        server = None
        for port in (8080, 8765, 8000):
            try:
                server = HTTPServer(('localhost', port), _Handler)
                redirect_port = port
                break
            except OSError:
                server = None

        def serve_loop(srv, evt):
            # serve until token_event is set or timeout
            srv.timeout = 1
            end = time.time() + 180
            while (not evt.is_set()) and time.time() < end:
                try:
                    srv.handle_request()
                except Exception:
                    pass

        if server:
            print(f"Listening on http://localhost:{redirect_port} to capture the request_token.")
            print("Make sure this redirect URL is configured in the Kite developer app settings.")
            server_thread = threading.Thread(target=serve_loop, args=(server, token_event), daemon=True)
            server_thread.start()

        # Poll clipboard (macOS) for the request_token parameter in any copied URL
        pat = re.compile(r'request_token=([A-Za-z0-9]+)')
        clipboard_token = None
        start = time.time()
        while time.time() - start < 180 and not token_event.is_set():
            try:
                p = subprocess.run(['pbpaste'], capture_output=True, text=True)
                clip = p.stdout or ''
                m = pat.search(clip)
                if m:
                    clipboard_token = m.group(1)
                    token_box['token'] = clipboard_token
                    token_event.set()
                    break
            except Exception:
                pass
            time.sleep(1)

        # if token captured by either mechanism, use it
        if token_box.get('token'):
            request_token = token_box['token']
    except Exception:
        request_token = None

    if not request_token:
        print("")
        print("Automatic capture failed or timed out. Paste request_token from redirect URL below.")
        request_token = input("Enter request_token: ").strip()

    if not request_token:
        print("No request_token provided — aborting.")
        sys.exit(1)

    try:
        session = kite.generate_session(request_token, api_secret)
    except Exception as e:
        print("Failed to generate session:", e)
        sys.exit(1)

    # store the full session response
    session["saved_at"] = datetime.now().isoformat()
    save_token(token_file, session)
    print("Saved Kite session to", token_file)

    # Update Core_files/.env with fresh access_token and refresh_token if present
    access = session.get('access_token')
    refresh = session.get('refresh_token')
    if access:
        # rewrite .env: preserve other keys, update/add tokens
        env['KITE_ACCESS_TOKEN'] = access
        if refresh:
            env['KITE_REFRESH_TOKEN'] = refresh

        # write back .env (simple rewrite)
        try:
            with open(env_path, 'w', encoding='utf-8') as f:
                f.write('''''' )
                # write key=value lines
                for k, v in env.items():
                    f.write(f"{k}={v}\n")
            print("Updated Core_files/.env with new KITE_ACCESS_TOKEN")
        except Exception as e:
            print("Failed to update .env:", e)


if __name__ == "__main__":
    main()
