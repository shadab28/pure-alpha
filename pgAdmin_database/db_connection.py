"""PostgreSQL connection helper loading credentials from config.yaml.

Usage:
    from pgAdmin_database.db_connection import get_connection, pg_cursor

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
            print(cur.fetchone())

    Or using context manager:
        with pg_cursor() as (cur, conn):
            cur.execute('SELECT now()')
            print(cur.fetchone())

The YAML config at pgAdmin_database/config.yaml must have:
database:
  host: "127.0.0.1"
  port: 5432
  db_name: "Local Postgres"  # Actual database name (no spaces ideally)
  username: "postgres"
  password: "..."

If db_name contains spaces (e.g. "Local Postgres"), psycopg2 will treat that
as literal, which usually fails; rename to an actual database (e.g. 'postgres'
or 'kite'). This module will attempt to strip spaces but warns if they exist.
"""
from __future__ import annotations

import os
import yaml
import logging
from contextlib import contextmanager
from typing import Optional, Tuple, Any

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception as e:  # pragma: no cover - psycopg2 might not be installed in dev
    psycopg2 = None  # type: ignore
    logging.warning("psycopg2 not available: %s (DB features disabled)", e)

_CONFIG_CACHE = None

def _load_config():
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    cfg_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(f"Database config file missing: {cfg_path}")
    with open(cfg_path) as f:
        data = yaml.safe_load(f) or {}
    db = data.get('database') or {}
    _CONFIG_CACHE = db
    return db

def _normalize_db_name(name: str) -> str:
    if ' ' in name:
        logging.warning("Database name contains spaces '%s' - stripping for connection.", name)
        return name.replace(' ', '')
    return name

def get_connection() -> Optional[Any]:
    """Return a new psycopg2 connection or None if unavailable.

    Reads configuration from YAML. If psycopg2 missing or connection fails,
    returns None and logs the error.
    """
    if psycopg2 is None:
        return None
    cfg = _load_config()
    host = cfg.get('host') or cfg.get('hostname') or '127.0.0.1'
    port = int(cfg.get('port') or 5432)
    db_name = _normalize_db_name(str(cfg.get('db_name') or 'postgres'))
    user = cfg.get('username') or cfg.get('user') or os.getenv('PGUSER') or 'postgres'
    password = cfg.get('password') or os.getenv('PGPASSWORD')
    try:
        conn = psycopg2.connect(host=host, port=port, dbname=db_name, user=user, password=password)
        conn.autocommit = True
        return conn
    except Exception as e:
        logging.error("Failed to connect to Postgres %s:%s db=%s user=%s: %s", host, port, db_name, user, e)
        return None

@contextmanager
def pg_cursor(dict_rows: bool = False):
    """Context manager yielding (cursor, connection).

    Parameters:
        dict_rows: use RealDictCursor for dict results.
    """
    conn = get_connection()
    if conn is None:
        raise RuntimeError("No database connection (psycopg2 missing or connect failed)")
    cur_cls = RealDictCursor if dict_rows else None
    cur = conn.cursor(cursor_factory=cur_cls) if cur_cls else conn.cursor()
    try:
        yield cur, conn
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

def test_connection() -> bool:
    """Quick health check: returns True if SELECT 1 succeeds."""
    try:
        with pg_cursor() as (cur, _):
            cur.execute('SELECT 1')
            _ = cur.fetchone()
        return True
    except Exception as e:
        logging.warning("DB health check failed: %s", e)
        return False

if __name__ == '__main__':
    ok = test_connection()
    print("Database connectivity:", "OK" if ok else "FAILED")