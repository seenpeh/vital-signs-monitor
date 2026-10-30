"""Live pipeline: simulated sensor -> edge processor -> triage -> storage.

Every interval, each patient's monitor produces a raw reading, its edge
processor cleans it and updates the 5-minute stats, and both are saved.
At start-up, 5 minutes of history are pre-filled (see warm_up).

Run:  python -m monitor.run --patients 3 --seed 42
"""
import argparse
import time

from monitor import storage
from monitor.config import REAL_DATA_CSV, SAMPLE_INTERVAL_S
from monitor.processing import STATS_WINDOW, EdgeProcessor
from monitor.simulator import make_patients
from monitor.triage import Triage

WARMUP_SAMPLES = STATS_WINDOW  # 5 minutes of history at start-up


def tick(patients, processors, triages, ts, interval_s):
    """One reading per patient at time `ts`; returns the rows to save."""
    readings, processed, events, alerts = [], [], [], []
    for p in patients:
        reading, event = p.step()
        clean = processors[p.id].process(reading)
        readings.append({"patient_id": p.id, "ts": ts, **reading})
        processed.append({"patient_id": p.id, "ts": ts, **clean})
        change = triages[p.id].assess(reading, clean)
        if change:
            alerts.append({"patient_id": p.id, "ts": ts,
                           "level": change[0], "reason": change[1]})
        if event:
            events.append({"patient_id": p.id, "kind": event.kind,
                           "vital": event.vital, "start_ts": ts,
                           "end_ts": ts + event.length * interval_s})
    return readings, processed, events, alerts


def warm_up(patients, processors, triages, interval_s, conn):
    """Pre-fill 5 minutes of history, as if monitoring had started earlier.

    Without it the dashboard, filter and 5-minute stats need 5 minutes to
    fill. Injected events are paused so every patient starts stable; the
    natural noise and real-data spikes still apply.
    """
    event_rates = [(p.p_clinical, p.p_artifact) for p in patients]
    for p in patients:
        p.p_clinical = p.p_artifact = 0
    start = time.time() - WARMUP_SAMPLES * interval_s
    rows = ([], [], [], [])
    for i in range(WARMUP_SAMPLES):
        new = tick(patients, processors, triages, start + i * interval_s,
                   interval_s)
        for all_rows, new_rows in zip(rows, new):
            all_rows.extend(new_rows)
    storage.save(conn, *rows)
    for p, (clinical, artifact) in zip(patients, event_rates):
        p.p_clinical, p.p_artifact = clinical, artifact


def run(patients, processors, triages, interval_s, conn):
    """Process one reading per patient every `interval_s` s until Ctrl+C."""
    next_tick = time.monotonic()
    while True:
        storage.save(conn, *tick(patients, processors, triages, time.time(),
                                 interval_s))
        next_tick += interval_s
        time.sleep(max(0.0, next_tick - time.monotonic()))


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patients", type=positive_int, default=3)
    parser.add_argument("--interval", type=float, default=SAMPLE_INTERVAL_S,
                        help="seconds between readings (playback speed)")
    parser.add_argument("--seed", type=int, default=42,
                        help="same seed -> same readings and events")
    args = parser.parse_args()

    try:
        patients = make_patients(args.patients, args.seed)
    except FileNotFoundError:
        parser.exit(1, f"Real data not found: {REAL_DATA_CSV}\n")

    conn = storage.connect()
    storage.reset(conn)  # each run starts a fresh simulated session
    processors = {p.id: EdgeProcessor() for p in patients}
    triages = {p.id: Triage() for p in patients}
    warm_up(patients, processors, triages, args.interval, conn)
    print(f"Monitoring {len(patients)} patients (5 min of history "
          "pre-filled), Ctrl+C to stop.", flush=True)
    try:
        run(patients, processors, triages, args.interval, conn)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
