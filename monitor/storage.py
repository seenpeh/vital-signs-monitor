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

-- Edge-processed values: filtered reading + 5-minute window stats.
CREATE TABLE IF NOT EXISTS processed (
    patient_id TEXT NOT NULL,
    ts         REAL NOT NULL,
    -- per vital: filtered value, 5-min mean, SD, trend (units/min)
    hr REAL, hr_mean REAL, hr_sd REAL, hr_trend REAL,
    spo2 REAL, spo2_mean REAL, spo2_sd REAL, spo2_trend REAL,
    sbp REAL, sbp_mean REAL, sbp_sd REAL, sbp_trend REAL
);
CREATE INDEX IF NOT EXISTS processed_by_time ON processed (ts);

-- Triage: one row each time a patient's level changes.
CREATE TABLE IF NOT EXISTS alerts (
    patient_id TEXT NOT NULL,
    ts         REAL NOT NULL,
    level      TEXT NOT NULL,  -- normal, warning, critical
    reason     TEXT NOT NULL
);

-- Ground truth of what the simulator injected, for checking the detector.
CREATE TABLE IF NOT EXISTS events (
    patient_id TEXT NOT NULL,
    kind       TEXT NOT NULL,  -- spo2_drop, arrhythmia, spo2_dip, dropout
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
        conn.execute("DELETE FROM processed")
        conn.execute("DELETE FROM alerts")
        conn.execute("DELETE FROM events")


def save(conn, readings, processed, events, alerts):
    """Insert one tick of rows, new events and alerts atomically."""
    with conn:
        conn.executemany(
            "INSERT INTO readings VALUES (:patient_id, :ts, :hr, :spo2, :sbp)",
            readings)
        conn.executemany(
            "INSERT INTO processed VALUES (:patient_id, :ts, "
            ":hr, :hr_mean, :hr_sd, :hr_trend, "
            ":spo2, :spo2_mean, :spo2_sd, :spo2_trend, "
            ":sbp, :sbp_mean, :sbp_sd, :sbp_trend)",
            processed)
        conn.executemany(
            "INSERT INTO events VALUES "
            "(:patient_id, :kind, :vital, :start_ts, :end_ts)",
            events)
        conn.executemany(
            "INSERT INTO alerts VALUES (:patient_id, :ts, :level, :reason)",
            alerts)


def readings_since(conn, ts):
    rows = conn.execute(
        "SELECT * FROM readings WHERE ts >= ? ORDER BY ts", (ts,))
    return [dict(r) for r in rows]


def processed_since(conn, ts):
    rows = conn.execute(
        "SELECT * FROM processed WHERE ts >= ? ORDER BY ts", (ts,))
    return [dict(r) for r in rows]


def latest_alerts(conn, limit=20):
    rows = conn.execute(
        "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def current_levels(conn):
    """Latest level per patient that has had an alert."""
    rows = conn.execute(
        "SELECT patient_id, level FROM alerts a WHERE ts = "
        "(SELECT MAX(ts) FROM alerts WHERE patient_id = a.patient_id)")
    return {r["patient_id"]: r["level"] for r in rows}


def latest_events(conn, limit=20):
    rows = conn.execute(
        "SELECT * FROM events ORDER BY start_ts DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]
