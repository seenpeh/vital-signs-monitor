"""SQLite storage: the simulator writes, the dashboard reads."""
import sqlite3

from monitor.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    patient_id TEXT NOT NULL,
    ts         REAL NOT NULL,  -- unix time, seconds
    hr         REAL,           -- NULL = no reading (e.g. probe off)
    spo2       REAL,
    sbp        REAL
);
CREATE INDEX IF NOT EXISTS readings_by_time ON readings (ts);

-- Ground truth of what the simulator injected, for checking the detector.
CREATE TABLE IF NOT EXISTS events (
    patient_id TEXT NOT NULL,
    kind       TEXT NOT NULL,  -- spo2_drop, arrhythmia, spike, dropout
    vital      TEXT,           -- affected vital, for sensor artefacts
    start_ts   REAL NOT NULL,
    end_ts     REAL NOT NULL
);
"""


def connect(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    # WAL lets the dashboard read while the simulator writes.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def reset(conn):
    with conn:
        conn.execute("DELETE FROM readings")
        conn.execute("DELETE FROM events")


def save(conn, readings, events):
    """Insert one tick of readings and any newly started events atomically."""
    with conn:
        conn.executemany(
            "INSERT INTO readings VALUES (:patient_id, :ts, :hr, :spo2, :sbp)",
            readings)
        conn.executemany(
            "INSERT INTO events VALUES "
            "(:patient_id, :kind, :vital, :start_ts, :end_ts)",
            events)


def readings_since(conn, ts):
    rows = conn.execute(
        "SELECT * FROM readings WHERE ts >= ? ORDER BY ts", (ts,))
    return [dict(r) for r in rows]


def latest_events(conn, limit=20):
    rows = conn.execute(
        "SELECT * FROM events ORDER BY start_ts DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]
