"""SQLite schema + helpers for the Pi gateway."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "edgehealth.db"


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_device TEXT NOT NULL,
                ts_received TEXT NOT NULL,
                metric TEXT NOT NULL,
                value REAL NOT NULL,
                source TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts_device);
            CREATE INDEX IF NOT EXISTS idx_readings_metric ON readings(metric);

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                severity TEXT NOT NULL,
                kind TEXT NOT NULL,
                metric TEXT,
                value REAL,
                message TEXT,
                latency_ms REAL
            );
            CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);

            CREATE TABLE IF NOT EXISTS summaries (
                date TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                pushed_at TEXT
            );
            """
        )


if __name__ == "__main__":
    init()
    print(f"DB initialized at {DB_PATH}")
