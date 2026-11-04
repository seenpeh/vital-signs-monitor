import unittest

from monitor.processing import EdgeProcessor
from monitor.simulator import make_patients
from monitor.triage import CALM_SAMPLES, Triage, score


def values(hr=75, spo2=97, sbp=110, hr_mean=75):
    return {"hr": hr, "hr_mean": hr_mean, "spo2": spo2, "sbp": sbp}


def feed(triage, samples, raw_hr=None):
    """Feed processed samples; return the list of level changes."""
    changes = []
    for i, p in enumerate(samples):
        hr = raw_hr[i] if raw_hr else p["hr"]
        change = triage.assess({"hr": hr}, p)
        if change:
            changes.append((i, change[0]))
    return changes


class RuleTest(unittest.TestCase):
    def test_out_of_range_is_an_immediate_warning(self):
        self.assertEqual(feed(Triage(), [values(hr=101)]), [(0, "warning")])

    def test_short_low_spo2_without_hr_rise_is_only_warning(self):
        # like a burst of motion glitches that got through the filter
        changes = feed(Triage(), [values(spo2=86)] * 18)
        self.assertEqual(changes, [(0, "warning")])

    def test_low_spo2_with_hr_rise_is_critical_at_once(self):
        changes = feed(Triage(), [values(spo2=86, hr=105)] * 10)
        self.assertEqual(changes, [(0, "critical")])

    def test_prolonged_low_spo2_alone_critical_after_40_s(self):
        changes = feed(Triage(), [values(spo2=86)] * 20)
        self.assertEqual(changes, [(0, "warning"), (19, "critical")])

    def test_arrhythmia_needs_irregular_beats(self):
        fast = [values(hr=115, hr_mean=80)] * 25
        irregular = [115 + (12 if i % 2 else -12) for i in range(25)]
        # 11 raw samples (10 jumps) are needed to judge the rhythm
        self.assertIn((10, "critical"), feed(Triage(), fast, irregular))
        steady = feed(Triage(), fast)  # fast but regular: exercise, fever
        self.assertEqual(steady, [(0, "warning")])

    def test_calms_down_only_after_30_s(self):
        t = Triage()
        feed(t, [values(hr=101)])
        changes = feed(t, [values()] * CALM_SAMPLES)
        self.assertEqual(changes, [(CALM_SAMPLES - 1, "normal")])

    def test_flicker_on_a_limit_gives_one_alert(self):
        flicker = [values(spo2=94), values(spo2=95)] * 20
        self.assertEqual(feed(Triage(), flicker), [(0, "warning")])


class GroundTruthTest(unittest.TestCase):
    """Score the whole closed loop on 9 simulated patient-hours."""

    def test_no_false_critical_and_crises_caught(self):
        crises, false_critical = [], 0
        for patient in make_patients(3, seed=1):
            proc, triage, crisis = EdgeProcessor(), Triage(), None
            for t in range(5400):  # 3 hours
                reading, started, ended = patient.step()
                change = triage.assess(reading, proc.process(reading))
                if triage.level == "critical":
                    patient.alarm()
                if started and started.shift:
                    # already alarming from the previous crisis: covered
                    crisis = {"until": None,
                              "caught": triage.level == "critical"}
                    crises.append(crisis)
                if ended and ended.shift:
                    crisis["until"] = t + 20  # filter and confirmation delay
                if change and change[0] == "critical":
                    if crisis and (crisis["until"] is None
                                   or t <= crisis["until"]):
                        crisis["caught"] = True
                    else:
                        false_critical += 1
        caught = sum(c["caught"] for c in crises)
        self.assertEqual(false_critical, 0)
        self.assertGreater(len(crises), 50)
        self.assertGreaterEqual(caught / len(crises), 0.95)


class ScoreTest(unittest.TestCase):
    def test_caught_covered_missed_pending_and_false(self):
        def crisis(pid, start, end):
            return {"patient_id": pid, "kind": "spo2_drop",
                    "start_ts": start, "end_ts": end}

        def alert(pid, ts, level):
            return {"patient_id": pid, "ts": ts, "level": level}

        events = [crisis("P1", 100, 200),   # caught 30 s in
                  crisis("P1", 205, 260),   # still Critical: covered
                  crisis("P2", 100, 150),   # no alert: missed
                  crisis("P3", 900, None),  # just started: pending
                  {"patient_id": "P2", "kind": "spike", "start_ts": 500,
                   "end_ts": 502}]
        alerts = [alert("P1", 130, "critical"),
                  alert("P2", 600, "critical")]  # no crisis: false
        s = score(events, alerts, now=910)
        self.assertEqual((s["crises"], s["caught"], s["covered"],
                          s["missed"], s["pending"]), (4, 1, 1, 1, 1))
        self.assertEqual(s["false_critical"], 1)
        self.assertEqual(s["median_delay_s"], 30)
        self.assertEqual(s["artefacts"], 1)


if __name__ == "__main__":
    unittest.main()
