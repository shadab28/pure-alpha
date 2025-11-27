import yaml
import os
import psycopg2
from pathlib import Path


def read_config():
    # Prefer package-local config if present, otherwise fallback to project root config.yaml
    pkg_cfg = Path(__file__).resolve().parent / "config.yaml"
    root_cfg = Path("config.yaml")
    cfg_path = pkg_cfg if pkg_cfg.exists() else root_cfg
    if not cfg_path.exists():
        return {}
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    dbcfg = raw.get("database", {}) if raw else {}
    return {
        "host": dbcfg.get("host", "127.0.0.1"),
        "port": dbcfg.get("port", 5432),
        "dbname": dbcfg.get("db_name", "postgres"),
        "user": dbcfg.get("username", "postgres"),
        "password": dbcfg.get("password", ""),
    }


def connect():
    cfg = read_config()
    try:
        conn = psycopg2.connect(host=cfg.get("host"), port=cfg.get("port"), dbname=cfg.get("dbname"), user=cfg.get("user"), password=cfg.get("password"))
        return conn
    except Exception as e:
        print("Warning: could not connect to Postgres: {}".format(e))
        return None


def ensure_tables(conn):
    """Create minimal tables for ohlcv and trades if connection is available."""
    if not conn:
        print("No DB connection: skipping table creation")
        return
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ohlcv (
            instrument_token bigint,
            ts timestamptz,
            open double precision,
            high double precision,
            low double precision,
            close double precision,
            volume bigint,
            PRIMARY KEY (instrument_token, ts)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id serial PRIMARY KEY,
            instrument_token bigint,
            ts timestamptz DEFAULT now(),
            side varchar(4),
            qty integer,
            price double precision
        )
        """
    )
    conn.commit()
    cur.close()
