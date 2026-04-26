"""
Alert dispatcher: writes to DB, pushes to ntfy.sh.

Buzzer hook is stubbed (no buzzer in this build).
"""

import os
import time
from datetime import datetime

import requests

from db import connect

NTFY_TOPIC = os.environ.get("EDGEHEALTH_NTFY_TOPIC", "edgehealth-nick-2026")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"


def fire(anom, latency_ms: float) -> None:
    """Persist alert + push to ntfy. Non-blocking on push failure."""
    ts = datetime.utcnow().isoformat()
    with connect() as conn:
        conn.execute(
            """INSERT INTO alerts
               (ts, severity, kind, metric, value, message, latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ts, anom.severity, anom.kind, anom.metric,
             anom.value, anom.message, latency_ms),
        )

    try:
        requests.post(
            NTFY_URL,
            data=anom.message.encode("utf-8"),
            headers={
                "Title": f"EdgeHealth {anom.severity.upper()}",
                "Priority": "5" if anom.severity == "critical" else "3",
                "Tags": "warning,heart" if anom.metric == "hr" else "warning",
            },
            timeout=3,
        )
    except requests.RequestException as e:
        print(f"[alerter] ntfy push failed: {e}")
    print(f"[ALERT] {anom.severity} {anom.kind} {anom.value} ({latency_ms:.1f}ms)")
