"""Simulated bedside monitors: one Patient per node, a reading every 2 s.

Each vital = slow level + jitter + occasional spikes:

* slow level: random walk gently pulled back to the patient's baseline.
  How far and how fast it wanders is an assumption (see TYPICAL_SPREAD).
* jitter and spikes: fitted from real VitalDB data, see monitor/model.py.

On top of that we inject events:

* clinical crises: SpO2 drop (with compensatory heart-rate rise) and
  arrhythmia (fast, irregular heart rate). They move the patient's level,
  because the body itself changes, and last until treated: a nurse reacts
  to the monitor's Critical alarm (closed loop, see alarm()), or notices on
  rounds if no alarm came. These should raise alerts;
* artefacts: short SpO2 dips (sensor motion) and dropouts (probe off).
  They only change the reading, as the patient is fine. These should be
  filtered out, not alarmed on.

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

# Crisis dynamics, as (onset, recovery) time constants in seconds: how fast
# the level moves toward the crisis, and back to baseline once treated.
# A stable patient's level drifts with DRIFT_TIME_S (300 s).
CRISIS_TIME_S = {
    "spo2_drop": (8, 15),   # hypoxia sets in over ~20 s; oxygen works fast
    # the rhythm flips within a few beats; rate control takes minutes in
    # reality, compressed here so the demo shows the recovery
    "arrhythmia": (2, 30),
}
ARRHYTHMIA_HR = (110, 150)   # rapid atrial fibrillation, BPM
NURSE_REACTION_S = (15, 45)  # from the Critical alarm to treatment
NURSE_ROUNDS_S = 180         # an unalarmed crisis is noticed by then
RECOVERED = 0.8              # a crisis is over 80 % of the way back


@dataclass
class Event:
    kind: str
    start_tick: int
    length: int = None       # artefacts: in samples (crises: until treated)
    size: float = 0.0        # artefacts: depth of a dip
    vital: str = None        # artefacts: affected vital
    shift: dict = None       # crisis: vital -> how far it moves the level
    treat_tick: int = None   # crisis: when treatment starts


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
        self.spikes = []  # vitals that got a real-data spike this step

    def step(self):
        """Advance one sample.

        Returns (reading, event started now or None, event ended now or None).
        """
        rng = self.rng
        started = ended = None
        self.spikes = []
        if self.event is None:
            self.event = started = self._maybe_start_event()
        crisis = self.event if self.event and self.event.shift else None
        if (crisis and crisis.treat_tick is None and self.tick
                - crisis.start_tick >= NURSE_ROUNDS_S / SAMPLE_INTERVAL_S):
            crisis.treat_tick = self.tick  # no alarm came: noticed on rounds

        reading = {}
        for vital, model in self.models.items():
            target, pull = self._target_and_pull(vital, crisis)
            gap = target - self.level[vital]
            drift = rng.gauss(0, self.drift_sd[vital])
            self.level[vital] += pull * gap + drift
            # jitter is drawn fresh each sample, so it never accumulates
            reading[vital] = self.level[vital] + rng.gauss(0, model.jitter_sd)
            if model.spike_sizes and rng.random() < model.spike_rate:
                reading[vital] += rng.choice(model.spike_sizes)  # a real one
                self.spikes.append(vital)

        if self.event:
            self._apply(self.event, reading)
            if self._is_over(self.event):
                ended, self.event = self.event, None

        self.tick += 1
        return self._as_monitor_output(reading), started, ended

    def alarm(self):
        """The monitor is in Critical: a nurse comes and starts treatment."""
        crisis = self.event
        if crisis and crisis.shift and crisis.treat_tick is None:
            delay_s = self.rng.uniform(*NURSE_REACTION_S)
            crisis.treat_tick = self.tick + round(delay_s / SAMPLE_INTERVAL_S)

    def _treated(self, crisis):
        return crisis.treat_tick is not None and self.tick >= crisis.treat_tick

    def _target_and_pull(self, vital, crisis):
        """Where the level heads, and how fast (share of the gap per step)."""
        if not crisis or vital not in crisis.shift:
            return self.baseline[vital], self.pull
        onset_s, recovery_s = CRISIS_TIME_S[crisis.kind]
        if self._treated(crisis):
            return self.baseline[vital], SAMPLE_INTERVAL_S / recovery_s
        return (self.baseline[vital] + crisis.shift[vital],
                SAMPLE_INTERVAL_S / onset_s)

    def _is_over(self, event):
        if not event.shift:
            return self.tick - event.start_tick + 1 >= event.length
        return self._treated(event) and all(
            abs(self.level[v] - self.baseline[v]) <= (1 - RECOVERED) * abs(d)
            for v, d in event.shift.items())

    def _maybe_start_event(self):
        # shortcut: one event at a time per patient, so artefacts never overlap
        # a crisis; allow overlap if the filter needs testing against that.
        rng, tick = self.rng, self.tick
        roll = rng.random()
        if roll < self.p_clinical:
            if rng.random() < 0.5:
                # 10-15 points deep (98 % -> ~85 %); the heart compensates
                return Event("spo2_drop", tick,
                             shift={"spo2": -rng.uniform(10, 15),
                                    "hr": rng.uniform(15, 30)})
            hr = rng.uniform(*ARRHYTHMIA_HR)
            return Event("arrhythmia", tick,
                         shift={"hr": hr - self.baseline["hr"]})
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
        """Beat-to-beat and sensor effects, on top of the level."""
        if event.kind == "arrhythmia":
            reading["hr"] += self.rng.gauss(0, 12)  # irregular beats
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
