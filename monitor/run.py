"""Live pipeline: simulated sensor -> edge processor -> storage.

Every interval, each patient's monitor produces a raw reading, its edge
processor cleans it and updates the 5-minute stats, and both are saved.

Run:  python -m monitor.run --patients 3 --seed 42
"""
import argparse
import time

from monitor import storage
from monitor.config import REAL_DATA_CSV, SAMPLE_INTERVAL_S
from monitor.processing import EdgeProcessor
from monitor.simulator import make_patients


def run(patients, interval_s, conn):
    """Process one reading per patient every `interval_s` s until Ctrl+C."""
    processors = {p.id: EdgeProcessor() for p in patients}
    next_tick = time.monotonic()
    while True:
        now = time.time()
        readings, processed, events = [], [], []
        for p in patients:
            reading, event = p.step()
            readings.append({"patient_id": p.id, "ts": now, **reading})
            processed.append({"patient_id": p.id, "ts": now,
                              **processors[p.id].process(reading)})
            if event:
                events.append({"patient_id": p.id, "kind": event.kind,
                               "vital": event.vital, "start_ts": now,
                               "end_ts": now + event.length * interval_s})
        storage.save(conn, readings, processed, events)
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
    print(f"Monitoring {len(patients)} patients, Ctrl+C to stop.",
          flush=True)
    try:
        run(patients, args.interval, conn)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
