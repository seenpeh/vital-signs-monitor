"""Alert & triage engine: a NEWS2 risk level per patient.

NEWS2 (Royal College of Physicians, 2017) gives each vital 0-3 points and
adds them up. We score the three vitals we monitor, on the edge-processed
values, so a glitch of up to 3 samples never reaches the score:

    points        3      2        1       0        1        2       3
    HR          <=40     -      41-50   51-90    91-110  111-130  >=131
    SpO2        <=91   92-93    94-95   >=96
    SBP         <=90   91-100  101-110  111-219                    >=220

NEWS2 has no rhythm parameter, so one rule is added: an irregular rhythm
while HR scores points (arrhythmia) makes HR score 3. "Irregular" uses the
raw HR: the median jump between samples over the last 20 s (normal: <= 1
BPM, arrhythmia: ~10); the filter would smooth the irregularity away.

Level, from NEWS2's clinical risk bands:

    normal      total 0
    low         total 1-4
    low-medium  one vital scores 3       \
    medium      total 5-6                 > alarm: the nurse is called
    high        total 7 or more          /

The level rises at once: the filter has already held the change for 8 s.
It falls only with two guards, so an alarm doesn't flicker off:

* hysteresis, per vital: points go up as soon as a band is entered, but
  come down only once the value is MARGIN clear of the band edge (HR scores
  1 from 91 BPM, and is back to 0 only at 85 or below);
* a time window (complex event processing): the level steps down only to
  a level that held for all of the last CALM_SAMPLES (30 s), so glitches
  that show normal values cannot end an alarm.

shortcut: a burst of motion glitches that gets through the filter (up to
~20 s) raises a false alarm; a rise window of 24 s would reject them all
in our runs, at the cost of alarms about 22 s later.
"""
import statistics
from collections import deque

LEVELS = ("normal", "low", "low-medium", "medium", "high")  # by severity
ALARM_LEVELS = LEVELS[2:]

# (lowest value of the band, points), ascending; below the first band: 3
BANDS = {
    "hr": ((41, 1), (51, 0), (91, 1), (111, 2), (131, 3)),
    "spo2": ((92, 2), (94, 1), (96, 0)),
    "sbp": ((91, 2), (101, 1), (111, 0), (220, 3)),
}
MARGIN = {"hr": 5, "spo2": 1, "sbp": 5}  # hysteresis, in each vital's unit
CALM_SAMPLES = 15     # 30 s a lower level must hold to step down
IRREGULAR_STEP = 3    # BPM, median jump between raw samples
RHYTHM_WINDOW = 11    # raw HR samples = 10 jumps (20 s)

NAMES = {"hr": "HR", "spo2": "SpO2", "sbp": "SBP"}


def points(vital, value):
    """NEWS2 points of one value, without hysteresis."""
    return next((p for low, p in reversed(BANDS[vital]) if value >= low), 3)


def held_points(vital, value, before):
    """Points with hysteresis: a better band counts only MARGIN inside it."""
    if value is None:  # probe off: no news, keep the last score
        return before
    now = points(vital, value)
    if now >= before:
        return now
    # the worst score within MARGIN of the value: bands are U-shaped, so
    # it is at one of the two ends
    m = MARGIN[vital]
    return min(before, max(points(vital, value - m),
                           points(vital, value + m)))


def irregular(raw_hr):
    present = [hr for hr in raw_hr if hr is not None]
    if len(present) < RHYTHM_WINDOW:
        return False
    jumps = [abs(b - a) for a, b in zip(present, present[1:])]
    return statistics.median(jumps) > IRREGULAR_STEP


def level_for(scores):
    total = sum(scores.values())
    if total >= 7:
        return "high"
    if total >= 5:
        return "medium"
    if 3 in scores.values():
        return "low-medium"
    return "low" if total else "normal"


class Triage:
    """Keeps one patient's level and reports when it changes."""

    def __init__(self):
        self.level = "normal"
        self.scores = dict.fromkeys(BANDS, 0)  # with hysteresis
        self.raw_hr = deque(maxlen=RHYTHM_WINDOW)
        self.recent = deque(maxlen=CALM_SAMPLES)  # instant level ranks

    def assess(self, raw, p):
        """Return (new level, reason) if the level changed, else None."""
        self.raw_hr.append(raw["hr"])
        for vital in BANDS:
            self.scores[vital] = held_points(vital, p[vital],
                                             self.scores[vital])
        scores = dict(self.scores)
        arrhythmia = scores["hr"] > 0 and irregular(self.raw_hr)
        if arrhythmia:
            scores["hr"] = 3
        self.recent.append(LEVELS.index(level_for(scores)))
        rank = LEVELS.index(self.level)
        if self.recent[-1] > rank:  # worse: at once
            rank = self.recent[-1]
        elif (len(self.recent) == CALM_SAMPLES
              and max(self.recent) < rank):  # better for the whole 30 s
            rank = max(self.recent)
        else:
            return None
        self.level = LEVELS[rank]
        return self.level, reason(p, scores, arrhythmia)


def reason(p, scores, arrhythmia):
    """E.g. 'NEWS2 5: SpO2 86 (+3), HR 104 (+1), SBP 108 (+1)'."""
    parts = [f"{NAMES[v]} {p[v]:.0f}"
             + (" irregular" if v == "hr" and arrhythmia else "")
             + f" (+{s})"
             for v, s in sorted(scores.items(), key=lambda vs: -vs[1])
             if s and p[v] is not None]
    total = f"NEWS2 {sum(scores.values())}"
    return f"{total}: {', '.join(parts)}" if parts else total


CRISES = ("spo2_drop", "arrhythmia", "worsening")
GRACE_S = 40  # an alarm this long after a crisis ends still belongs to it


def alarms(alerts):
    """Alerts that raise a patient's level to, or within, the alarm levels.

    A step down (high -> medium) is not a new alarm.
    """
    out, last = [], {}
    for a in sorted(alerts, key=lambda a: a["ts"]):
        before = LEVELS.index(last.get(a["patient_id"], "normal"))
        if (a["level"] in ALARM_LEVELS
                and LEVELS.index(a["level"]) > before):
            out.append(a)
        last[a["patient_id"]] = a["level"]
    return out


def score(events, alerts, now):
    """Grade alarms against the simulator's ground truth.

    A crisis is caught by an alarm between its start and GRACE_S after its
    end, or covered if the monitor was already alarming when it started.
    An alarm outside every crisis is false.
    """
    raised = alarms(alerts)
    crises = [e for e in events if e["kind"] in CRISES]
    used, delays = set(), []
    out = {"crises": len(crises), "caught": 0, "covered": 0, "missed": 0,
           "pending": 0}
    for c in crises:
        until = (c["end_ts"] if c["end_ts"] is not None else now) + GRACE_S
        hits = [i for i, a in enumerate(raised) if a["patient_id"] ==
                c["patient_id"] and c["start_ts"] <= a["ts"] <= until]
        used.update(hits)
        before = [a for a in alerts if a["patient_id"] == c["patient_id"]
                  and a["ts"] < c["start_ts"]]
        # already alarming: covered, even if it re-alarms later
        if before and max(before, key=lambda a: a["ts"])["level"] in \
                ALARM_LEVELS:
            out["covered"] += 1
        elif hits:
            out["caught"] += 1
            delays.append(raised[hits[0]]["ts"] - c["start_ts"])
        elif c["end_ts"] is None or now <= until:
            out["pending"] += 1
        else:
            out["missed"] += 1
    out["false_alarms"] = len(raised) - len(used)
    out["median_delay_s"] = statistics.median(delays) if delays else None
    out["artefacts"] = sum(e["kind"] not in CRISES for e in events)
    return out
