"""Simulated bedside monitors: one Patient per node, a reading every 2 s.

Each vital = slow level + jitter + occasional spikes:

* slow level: random walk gently pulled back to the patient's baseline.
  How far and how fast it wanders is an assumption (see TYPICAL_SPREAD).
* jitter and spikes: fitted from real VitalDB data, see monitor/model.py.

On top of that we inject events:

* clinical: SpO2 drop (with compensatory heart-rate rise) and arrhythmia
  (fast, irregular heart rate) - these should raise alerts;
* artefacts: short SpO2 dips (sensor motion) and dropouts (probe off) -
  these should be filtered out, not alarmed on.

Run it through the pipeline:  python -m monitor.run
"""
import random
from dataclasses import dataclass

from monitor.config import REAL_DATA_COLUMNS, REAL_DATA_CSV, SAMPLE_INTERVAL_S
from monitor.model import fit_from_csv

# Each patient's resting baseline is drawn from inside the normal range.
BASELINE_RANGES = {"hr": (65, 85), "spo2": (97, 99), "sbp": (100, 112)}

# Assumption: a stable patient's slow level wanders about this far from
# baseline (1 SD) and takes about DRIFT_TIME_S to wander back. The real
# recording can't tell us this: its trends come from the surgery.
TYPICAL_SPREAD = {"hr": 4.0, "spo2": 1.0, "sbp": 4.0}
DRIFT_TIME_S = 300

# Per patient, mean time between events. Counted in samples, so --interval
# only changes playback speed, not the simulated patient.
CLINICAL_EVENT_EVERY_S = 180
ARTIFACT_EVERY_S = 30


@dataclass
class Event:
    kind: str
    start_tick: int
    length: int          # in samples
    size: float = 0.0    # depth of a drop or dip
    vital: str = None    # affected vital, for artefacts


def envelope(t, length, ramp):
    """0..1 trapezoid: rise over `ramp` samples, hold, fall over `ramp`."""
    return min(1.0, (t + 1) / ramp, (length - t) / ramp)


class Patient:
    def __init__(self, patient_id, models, rng):
        self.id = patient_id
        self.models = models
        self.rng = rng
        self.baseline = {v: rng.uniform(*BASELINE_RANGES[v]) for v in models}
        self.level = dict(self.baseline)
        # For x += -pull*x + N(0, sd), the level's variance settles at
        # sd^2 / (1 - (1 - pull)^2); pick sd so that equals spread^2.
        self.pull = SAMPLE_INTERVAL_S / DRIFT_TIME_S
        self.drift_sd = {v: TYPICAL_SPREAD[v] * (1 - (1 - self.pull) ** 2)
                         ** 0.5 for v in models}
        self.p_clinical = SAMPLE_INTERVAL_S / CLINICAL_EVENT_EVERY_S
        self.p_artifact = SAMPLE_INTERVAL_S / ARTIFACT_EVERY_S
        self.tick = 0
        self.event = None

    def step(self):
        """Advance one sample. Returns (reading, event started now or None)."""
        rng = self.rng
        reading = {}
        for vital, model in self.models.items():
            gap = self.baseline[vital] - self.level[vital]
            self.level[vital] += (self.pull * gap
                                  + rng.gauss(0, self.drift_sd[vital]))
            # jitter is drawn fresh each sample, so it never accumulates
            reading[vital] = self.level[vital] + rng.gauss(0, model.jitter_sd)
            if model.spike_sizes and rng.random() < model.spike_rate:
                reading[vital] += rng.choice(model.spike_sizes)  # a real one

        started = None
        if self.event is None:
            self.event = started = self._maybe_start_event()

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
            if rng.random() < 0.7:
                # The real case has no SpO2 spikes, but motion-induced SpO2
                # dips are the classic false alarm; the filter must see them.
                return Event("spo2_dip", tick, rng.randint(1, 3),
                             size=rng.uniform(10, 20), vital="spo2")
            vital = rng.choice(list(self.models))
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
        elif event.kind == "spo2_dip":
            reading["spo2"] -= event.size
        elif event.kind == "dropout":
            reading[event.vital] = None

    @staticmethod
    def _as_monitor_output(reading):
        """Monitors show whole numbers and SpO2 cannot exceed 100 %."""
        out = {v: None if x is None else round(x) for v, x in reading.items()}
        if out["spo2"] is not None:
            out["spo2"] = min(out["spo2"], 100)
        return out


def make_patients(count, seed):
    models = fit_from_csv(REAL_DATA_CSV, REAL_DATA_COLUMNS)
    rng = random.Random(seed)
    return [Patient(f"P{i}", models, rng) for i in range(1, count + 1)]
