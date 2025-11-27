from flask import Flask, jsonify, send_file, request, Response, stream_with_context
import json
from pathlib import Path
from threading import Lock
import queue
from pgAdmin_database import db, ohlcv_utils

app = Flask(__name__)

# In-memory latest quotes and subscriber queues for SSE
live_state = {"meta": {}, "ticks": {}}
subscribers = []
sub_lock = Lock()


@app.route('/live_quotes')
def live_quotes():
    p = Path('live_quotes.json')
    if not p.exists():
        return jsonify(live_state), 200
    return send_file(str(p), mimetype='application/json')


@app.route('/candles')
def candles():
    token = request.args.get('token')
    if not token:
        return jsonify({"error": "provide token as query param"}), 400
    conn = db.connect()
    rows = ohlcv_utils.fetch_recent(conn, int(token), limit=100)
    out = []
    for r in rows:
        out.append({"ts": str(r[0]), "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]})
    return jsonify(out)


@app.route('/ingest', methods=['POST'])
def ingest():
    """Receive live quotes from orchestrator and broadcast to SSE clients."""
    data = request.get_json(force=True)
    if not data:
        return ("", 400)

    # update live_state
    try:
        live_state.update(data)
    except Exception:
        live_state['ticks'] = data.get('ticks', {})

    # persist to file for backwards compatibility
    try:
        Path('live_quotes.json').write_text(json.dumps(live_state, default=str))
    except Exception:
        pass

    # broadcast to subscribers
    with sub_lock:
        for q in list(subscribers):
            try:
                q.put_nowait(json.dumps(live_state, default=str))
            except Exception:
                pass

    return ("", 204)


@app.route('/sse')
def sse():
    """Server-Sent Events endpoint streaming live quotes to the browser."""

    def gen(q: queue.Queue):
        try:
            # send initial state
            yield f"data: {json.dumps(live_state, default=str)}\n\n"
            while True:
                item = q.get()
                yield f"data: {item}\n\n"
        except GeneratorExit:
            return

    q = queue.Queue()
    with sub_lock:
        subscribers.append(q)

    return Response(stream_with_context(gen(q)), mimetype='text/event-stream')


if __name__ == '__main__':
    app.run(debug=True, port=5000)
