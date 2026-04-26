"""
Background jobs:
  - every 5 min: pull recent readings, run detector, emit alerts
  - daily at 23:55: compute summary, sign w/ HMAC, POST to cloud
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import requests
from apscheduler.schedulers.background import BackgroundScheduler

from alerter import fire
from db import connect
from detector import Detector

CLOUD_URL = os.environ.get("EDGEHEALTH_CLOUD_URL",
                           "https://edgehealth-cloud.onrender.com").rstrip("/")
HMAC_SECRET = os.environ.get("EDGEHEALTH_HMAC", "dev-secret-change-me").encode()

_detector = Detector()


def detect_job() -> None:
    """Pull last 10 HR readings, build features, run thresholds + IForest."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT ts_device, ts_received, value FROM readings
               WHERE metric='hr' ORDER BY id DESC LIMIT 10"""
        ).fetchall()
        if not rows:
            return
        rows = list(reversed(rows))  # ascending time

        latest = rows[-1]
        latest_ts = latest["ts_device"]

        # Threshold check on latest value
        anom = _detector.check_thresholds("hr", latest["value"])
        if anom:
            t_recv = datetime.fromisoformat(latest["ts_received"])
            latency = (datetime.utcnow() - t_recv).total_seconds() * 1000
            fire(anom, latency)
            return

        # IForest on rolling window
        if _detector.model is not None and len(rows) >= 5:
            hrs = np.array([r["value"] for r in rows], dtype=float)
            dhr = float(hrs[-1] - hrs[-2])
            now = datetime.utcnow()
            features = np.array([[hrs[-1], dhr, 0.0, now.hour]])
            anom = _detector.check_iforest(features)
            if anom:
                t_recv = datetime.fromisoformat(latest["ts_received"])
                latency = (datetime.utcnow() - t_recv).total_seconds() * 1000
                fire(anom, latency)


def daily_summary_job() -> None:
    """Aggregate yesterday's readings, POST signed summary to cloud."""
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)
    start = datetime.combine(yesterday, datetime.min.time()).isoformat()
    end = datetime.combine(today, datetime.min.time()).isoformat()

    with connect() as conn:
        hrs = [r["value"] for r in conn.execute(
            "SELECT value FROM readings WHERE metric='hr' AND ts_device>=? AND ts_device<?",
            (start, end)).fetchall()]
        spo2s = [r["value"] for r in conn.execute(
            "SELECT value FROM readings WHERE metric='spo2' AND ts_device>=? AND ts_device<?",
            (start, end)).fetchall()]
        steps = sum(r["value"] for r in conn.execute(
            "SELECT value FROM readings WHERE metric='steps' AND ts_device>=? AND ts_device<?",
            (start, end)).fetchall())
        anomaly_count = conn.execute(
            "SELECT COUNT(*) AS c FROM alerts WHERE ts>=? AND ts<?",
            (start, end)).fetchone()["c"]

    summary = {
        "date": yesterday.isoformat(),
        "hr_min": min(hrs) if hrs else None,
        "hr_mean": sum(hrs) / len(hrs) if hrs else None,
        "hr_max": max(hrs) if hrs else None,
        "spo2_min": min(spo2s) if spo2s else None,
        "spo2_mean": sum(spo2s) / len(spo2s) if spo2s else None,
        "steps": int(steps),
        "sleep_min": None,
        "anomaly_count": anomaly_count,
    }

    body = json.dumps(summary, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(HMAC_SECRET, body, hashlib.sha256).hexdigest()

    try:
        r = requests.post(
            f"{CLOUD_URL}/summary",
            data=body,
            headers={"Content-Type": "application/json",
                     "X-EdgeHealth-Sig": sig},
            timeout=10,
        )
        ok = r.ok
        print(f"[summary] pushed {summary['date']} -> {r.status_code}")
    except requests.RequestException as e:
        ok = False
        print(f"[summary] push failed: {e}")

    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO summaries (date, payload, pushed_at) VALUES (?,?,?)",
            (summary["date"], body.decode(),
             datetime.utcnow().isoformat() if ok else None),
        )


def start() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(detect_job, "interval", minutes=5, id="detect")
    sched.add_job(daily_summary_job, "cron", hour=23, minute=55, id="summary")
    sched.start()
    return sched
