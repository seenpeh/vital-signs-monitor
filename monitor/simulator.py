"""Simulated bedside monitors: one Patient per node, a reading every 2 s.

Each vital is a mean-reverting random walk around the patient's baseline.
Its step behaviour (how often it holds, how big a move is) is fitted from
real VitalDB data, see monitor/model.py. On top of that we inject:

* clinical events: SpO2 drop (with compensatory heart-rate rise) and
  arrhythmia (fast, irregular heart rate) - these should raise alerts;
* sensor artefacts: one-sample spikes (motion) and short dropouts
  (probe off) - these should be filtered out, not alarmed on.

Run:  python -m monitor.simulator --patients 3 --seed 42
"""
import argparse
import random
import time
from dataclasses import dataclass

from monitor import storage
from monitor.config import REAL_DATA_COLUMNS, REAL_DATA_CSV, SAMPLE_INTERVAL_S
from monitor.model import fit_from_csv

# Each patient's resting baseline is drawn from inside the normal range.
BASELINE_RANGES = {"hr": (65, 85), "spo2": (97, 99), "sbp": (100, 112)}
# How far a stable patient typically wanders from baseline (1 SD).
TYPICAL_SPREAD = {"hr": 4.0, "spo2": 1.0, "sbp": 4.0}

CLINICAL_EVENT_EVERY_S = 180  # mean time between clinical events, per patient
ARTIFACT_EVERY_S = 30         # mean time between sensor artefacts, per patient


@dataclass
class Event:
    kind: str
    start_tick: int
    length: int          # in samples
    size: float = 0.0    # depth of drop / height of spike
    vital: str = None    # affected vital, for artefacts


def reversion_for(model, spread):
    """Pull towards baseline per move so the walk's SD settles at `spread`.

    For x += -r*x + noise, the stationary variance is sd^2 / (1 - (1-r)^2).
    """
    ratio = (model.step_sd / spread) ** 2
    if ratio >= 1:
        return 1.0  # one step already exceeds the spread: always pull fully
    return max(1 - (1 - ratio) ** 0.5, 0.01)


def envelope(t, length, ramp):
    """0..1 trapezoid: rise over `ramp` samples, hold, fall over `ramp`."""
    return min(1.0, (t + 1) / ramp, (length - t) / ramp)


class Patient:
    def __init__(self, patient_id, models, rng, interval_s=SAMPLE_INTERVAL_S):
        self.id = patient_id
        self.models = models
        self.rng = rng
        self.baseline = {v: rng.uniform(*BASELINE_RANGES[v]) for v in models}
        self.level = dict(self.baseline)
        self.reversion = {v: reversion_for(m, TYPICAL_SPREAD[v])
                          for v, m in models.items()}
        self.p_clinical = interval_s / CLINICAL_EVENT_EVERY_S
        self.p_artifact = interval_s / ARTIFACT_EVERY_S
        self.tick = 0
        self.event = None

    def step(self):
        """Advance one sample. Returns (reading, event started now or None)."""
        for vital, model in self.models.items():
            if self.rng.random() >= model.hold_prob:
                gap = self.baseline[vital] - self.level[vital]
                self.level[vital] += (self.reversion[vital] * gap
                                      + self.rng.gauss(0, model.step_sd))

        started = None
        if self.event is None:
            self.event = started = self._maybe_start_event()

        reading = dict(self.level)
        if self.event:
            self._apply(self.event, reading)
            if self.tick - self.event.start_tick + 1 >= self.event.length:
                self.event = None

        self.tick += 1
        return self._as_monitor_output(reading), started

    def _maybe_start_event(self):
        # shortcut: one event at a time per patient, so artefacts never overlap
        # a crisis; allow overlap if the filter needs testing against that.
        rng, tick = self.rng, self.tick
        roll = rng.random()
        if roll < self.p_clinical:
            if rng.random() < 0.5:
                # 60-120 s desaturation, 10-15 points deep (98 % -> ~85 %)
                return Event("spo2_drop", tick, rng.randint(30, 60),
                             size=rng.uniform(10, 15))
            return Event("arrhythmia", tick, rng.randint(15, 45))  # 30-90 s
        if roll < self.p_clinical + self.p_artifact:
            vital = rng.choice(list(self.models))
            if rng.random() < 0.7:
                sign = rng.choice([-1, 1])
                size = {"hr": sign * rng.uniform(30, 50),
                        "spo2": -rng.uniform(10, 20),
                        "sbp": sign * rng.uniform(30, 60)}[vital]
                return Event("spike", tick, 1, size=size, vital=vital)
            return Event("dropout", tick, rng.randint(2, 5), vital=vital)
        return None

    def _apply(self, event, reading):
        t = self.tick - event.start_tick
        if event.kind == "spo2_drop":
            depth = envelope(t, event.length, ramp=8)
            reading["spo2"] -= depth * event.size
            reading["hr"] += depth * 25  # the heart compensates for hypoxia
        elif event.kind == "arrhythmia":
            reading["hr"] += 35 + self.rng.gauss(0, 12)
        elif event.kind == "spike":
            reading[event.vital] += event.size
        elif event.kind == "dropout":
            reading[event.vital] = None

    @staticmethod
    def _as_monitor_output(reading):
        """Monitors show whole numbers and SpO2 cannot exceed 100 %."""
        out = {v: None if x is None else round(x) for v, x in reading.items()}
        if out["spo2"] is not None:
            out["spo2"] = min(out["spo2"], 100)
        return out


def make_patients(count, seed, interval_s=SAMPLE_INTERVAL_S):
    models = fit_from_csv(REAL_DATA_CSV, REAL_DATA_COLUMNS)
    rng = random.Random(seed)
    return [Patient(f"P{i}", models, rng, interval_s)
            for i in range(1, count + 1)]


def run(patients, interval_s, conn):
    """Emit one reading per patient every `interval_s` seconds until Ctrl+C."""
    next_tick = time.monotonic()
    while True:
        now = time.time()
        readings, events = [], []
        for p in patients:
            reading, event = p.step()
            readings.append({"patient_id": p.id, "ts": now, **reading})
            if event:
                events.append({"patient_id": p.id, "kind": event.kind,
                               "vital": event.vital, "start_ts": now,
                               "end_ts": now + event.length * interval_s})
        storage.save(conn, readings, events)
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
                        help="seconds between readings")
    parser.add_argument("--seed", type=int, default=42,
                        help="same seed -> same readings and events")
    args = parser.parse_args()

    try:
        patients = make_patients(args.patients, args.seed, args.interval)
    except FileNotFoundError:
        parser.exit(1, f"Real data not found: {REAL_DATA_CSV}\n")

    conn = storage.connect()
    storage.reset(conn)  # each run starts a fresh simulated session
    for vital, m in patients[0].models.items():
        print(f"fitted {vital}: hold_prob={m.hold_prob:.2f} "
              f"step_sd={m.step_sd:.2f}")
    print(f"Simulating {len(patients)} patients, Ctrl+C to stop.")
    try:
        run(patients, args.interval, conn)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
