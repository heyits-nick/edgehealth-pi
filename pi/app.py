"""
EdgeHealth Pi gateway — Flask ingest + status, with background scheduler.

Endpoints:
  POST /ingest    Phone relay posts batch of readings.
  GET  /status    Liveness + counts + resource snapshot.
  GET  /alerts    Recent alerts (debug / dashboard).
  POST /detect    Force-run detection (eval harness).

Run:
  python app.py
or via systemd (see edgehealth.service).
"""

from __future__ import annotations

import os
import time
from datetime import datetime

import psutil
from flask import Flask, jsonify, request

import db
import scheduler
from detector import Detector

app = Flask(__name__)
db.init()
_sched = scheduler.start()
_proc = psutil.Process(os.getpid())
_started_at = time.time()


@app.route("/status")
def status():
    with db.connect() as conn:
        n_readings = conn.execute(
            "SELECT COUNT(*) AS c FROM readings WHERE ts_device > datetime('now','-1 day')"
        ).fetchone()["c"]
        n_alerts = conn.execute(
            "SELECT COUNT(*) AS c FROM alerts WHERE ts > datetime('now','-1 day')"
        ).fetchone()["c"]
    return jsonify({
        "ok": True,
        "uptime_sec": int(time.time() - _started_at),
        "readings_24h": n_readings,
        "alerts_24h": n_alerts,
        "cpu_percent": _proc.cpu_percent(interval=0.1),
        "rss_mb": round(_proc.memory_info().rss / 1024 / 1024, 1),
    })


@app.route("/ingest", methods=["POST"])
def ingest():
    """Accept JSON: {"readings": [{"ts": "...", "metric": "hr", "value": 72}, ...]}"""
    payload = request.get_json(silent=True)
    if not payload or "readings" not in payload:
        return jsonify({"error": "missing readings"}), 400

    received_at = datetime.utcnow().isoformat()
    source = payload.get("source", "android")
    rows = []
    for r in payload["readings"]:
        try:
            rows.append((r["ts"], received_at, r["metric"], float(r["value"]), source))
        except (KeyError, ValueError, TypeError):
            return jsonify({"error": "bad reading", "row": r}), 400

    with db.connect() as conn:
        conn.executemany(
            """INSERT INTO readings (ts_device, ts_received, metric, value, source)
               VALUES (?, ?, ?, ?, ?)""",
            rows,
        )
    return jsonify({"ok": True, "ingested": len(rows), "received_at": received_at})


@app.route("/alerts")
def alerts():
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT 50"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/detect", methods=["POST"])
def force_detect():
    """Eval-harness hook: run detector immediately."""
    scheduler.detect_job()
    return jsonify({"ok": True})


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
