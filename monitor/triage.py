"""Alert & triage engine: Normal / Warning / Critical per patient.

Rules, on the edge-processed values:

* Warning:  any cleaned vital outside its normal range. The rolling median
            already ignores glitches, so no extra wait is needed.
* Critical: one of these holds:
    - SpO2 < 90 % and the heart rate has risen;
    - the heart rate has risen and beats irregularly;
    - SpO2 < 90 % on its own, for 40 s in a row (prolonged desaturation:
      catches drops whose HR rise is hidden by an earlier event).
  Only the last rule waits. Bursts of motion glitches pass the filter for
  up to about 26 s but do not raise the heart rate, so the first two rules
  need no wait, while SpO2 alone must outlast them.

"Heart rate has risen" means HR > 100 or HR at least HR_JUMP above the
patient's own 5-minute mean. "Irregular" uses the raw HR: the median jump
between samples over the last 20 s (normal: <= 1 BPM, arrhythmia: ~10).

Going up a level is immediate; going down needs the lower level to hold
for CALM_SAMPLES (30 s), so a value sitting on a limit cannot flood the
alert log.
"""
import statistics
from collections import deque

from monitor.config import NORMAL_RANGES

LEVELS = ("normal", "warning", "critical")  # in order of severity

CRITICAL_SPO2 = 90
HR_HIGH = 100
HR_JUMP = 10          # BPM above the 5-minute mean
IRREGULAR_STEP = 3    # BPM, median jump between raw samples
CONFIRM_SAMPLES = 1    # 2 s: an HR-confirmed crisis needs no wait
LONG_DESAT_SAMPLES = 20  # 40 s: low SpO2 alone, even without an HR rise
CALM_SAMPLES = 15     # 30 s
RHYTHM_WINDOW = 11    # raw HR samples = 10 jumps (20 s)

NAMES = {"hr": "HR", "spo2": "SpO2", "sbp": "SBP"}


def hr_risen(p):
    hr, mean = p["hr"], p["hr_mean"]
    return hr is not None and (hr > HR_HIGH or
                               (mean is not None and hr >= mean + HR_JUMP))


def irregular(raw_hr):
    present = [hr for hr in raw_hr if hr is not None]
    if len(present) < RHYTHM_WINDOW:
        return False
    jumps = [abs(b - a) for a, b in zip(present, present[1:])]
    return statistics.median(jumps) > IRREGULAR_STEP


def out_of_range(p):
    """Readable list of cleaned vitals outside their normal range."""
    found = []
    for vital, (low, high) in NORMAL_RANGES.items():
        if p[vital] is not None and not low <= p[vital] <= high:
            found.append(f"{NAMES[vital]} {p[vital]:.0f}")
    return found


class Triage:
    """Keeps one patient's level and reports when it changes."""

    def __init__(self):
        self.level = "normal"
        self.raw_hr = deque(maxlen=RHYTHM_WINDOW)
        self.runs = {}         # crisis condition -> samples in a row
        self.calm_run = 0      # samples in a row below the current level
        self.calm_peak = "normal"

    def assess(self, raw, p):
        """Return (new level, reason) if the level changed, else None."""
        self.raw_hr.append(raw["hr"])
        level, reason = self._instant_level(p)
        return self._settle(level, reason)

    def _instant_level(self, p):
        risen = hr_risen(p)
        low = p["spo2"] is not None and p["spo2"] < CRITICAL_SPO2
        # condition -> (holds now, samples it must hold in a row)
        crises = {
            "desaturation with HR rise": (low and risen, CONFIRM_SAMPLES),
            "prolonged desaturation": (low, LONG_DESAT_SAMPLES),
            "arrhythmia": (risen and irregular(self.raw_hr),
                           CONFIRM_SAMPLES),
        }
        for name, (holds, needed) in crises.items():
            self.runs[name] = self.runs.get(name, 0) + 1 if holds else 0
            if self.runs[name] >= needed:
                # HR may be "risen" (jump vs mean) while still <= 100
                shown = ", ".join(f"{NAMES[v]} {p[v]:.0f}"
                                  for v in ("hr", "spo2") if p[v] is not None)
                return "critical", f"{name}: {shown}"
        found = out_of_range(p)
        if found:  # also covers an unconfirmed crisis: it is out of range
            return "warning", ", ".join(found)
        return "normal", "back in normal range"

    def _settle(self, level, reason):
        rank = LEVELS.index
        if rank(level) > rank(self.level):
            self.level, self.calm_run = level, 0
            return level, reason
        if level == self.level:
            self.calm_run = 0
            return None
        # lower than now: wait until it has held for CALM_SAMPLES
        self.calm_peak = (level if self.calm_run == 0
                          else max(self.calm_peak, level, key=rank))
        self.calm_run += 1
        if self.calm_run < CALM_SAMPLES:
            return None
        self.level, self.calm_run = self.calm_peak, 0
        return self.level, reason if self.level == level else "improving"


CRISES = ("spo2_drop", "arrhythmia")
GRACE_S = 40  # a Critical this long after a crisis ends still belongs to it


def score(events, alerts, now):
    """Grade Critical alerts against the simulator's ground truth.

    A crisis is caught by a Critical alert between its start and GRACE_S
    after its end, or covered if the monitor was already Critical when it
    started. A Critical alert outside every crisis is false.
    """
    crit = sorted((a for a in alerts if a["level"] == "critical"),
                  key=lambda a: a["ts"])
    crises = [e for e in events if e["kind"] in CRISES]
    used, delays = set(), []
    out = {"crises": len(crises), "caught": 0, "covered": 0, "missed": 0,
           "pending": 0}
    for c in crises:
        until = (c["end_ts"] if c["end_ts"] is not None else now) + GRACE_S
        hits = [i for i, a in enumerate(crit) if a["patient_id"] ==
                c["patient_id"] and c["start_ts"] <= a["ts"] <= until]
        used.update(hits)
        before = [a for a in alerts if a["patient_id"] == c["patient_id"]
                  and a["ts"] < c["start_ts"]]
        if hits:
            out["caught"] += 1
            delays.append(crit[hits[0]]["ts"] - c["start_ts"])
        elif before and max(before, key=lambda a: a["ts"])["level"] == \
                "critical":
            out["covered"] += 1
        elif c["end_ts"] is None or now <= until:
            out["pending"] += 1
        else:
            out["missed"] += 1
    out["false_critical"] = len(crit) - len(used)
    out["median_delay_s"] = statistics.median(delays) if delays else None
    out["artefacts"] = sum(e["kind"] not in CRISES for e in events)
    return out
