from datetime import datetime


def insert_ohlcv(conn, instrument_token, ts, open_, high, low, close, volume):
    if not conn:
        print("DB not available. Skipping insert_ohlcv")
        return
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ohlcv (instrument_token, ts, open, high, low, close, volume) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (instrument_token, ts) DO NOTHING",
        (instrument_token, ts, open_, high, low, close, volume),
    )
    conn.commit()
    cur.close()


def fetch_recent(conn, instrument_token, limit=100):
    if not conn:
        return []
    cur = conn.cursor()
    cur.execute("SELECT ts, open, high, low, close, volume FROM ohlcv WHERE instrument_token=%s ORDER BY ts DESC LIMIT %s", (instrument_token, limit))
    rows = cur.fetchall()
    cur.close()
    return rows
