import unittest

from monitor.processing import EdgeProcessor
from monitor.simulator import make_patients
from monitor.triage import CALM_SAMPLES, Triage


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

    def test_low_spo2_with_hr_rise_critical_after_20_s(self):
        changes = feed(Triage(), [values(spo2=86, hr=95, hr_mean=80)] * 10)
        self.assertEqual(changes, [(0, "warning"), (9, "critical")])

    def test_prolonged_low_spo2_alone_critical_after_40_s(self):
        changes = feed(Triage(), [values(spo2=86)] * 20)
        self.assertEqual(changes, [(0, "warning"), (19, "critical")])

    def test_arrhythmia_needs_irregular_beats(self):
        fast = [values(hr=115, hr_mean=80)] * 25
        irregular = [115 + (12 if i % 2 else -12) for i in range(25)]
        self.assertIn((19, "critical"), feed(Triage(), fast, irregular))
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
    """Score the whole edge + triage chain on 9 simulated patient-hours."""

    def test_no_false_critical_and_crises_caught(self):
        crises, false_critical = [], 0
        for patient in make_patients(3, seed=1):
            proc, triage, crisis = EdgeProcessor(), Triage(), None
            for t in range(5400):  # 3 hours
                reading, started = patient.step()
                change = triage.assess(reading, proc.process(reading))
                if started and started.kind in ("spo2_drop", "arrhythmia"):
                    # +20 samples: filter and confirmation delay
                    crisis = {"until": t + started.length + 20,
                              "caught": False}
                    crises.append(crisis)
                if change and change[0] == "critical":
                    if crisis and t <= crisis["until"]:
                        crisis["caught"] = True
                    else:
                        false_critical += 1
        caught = sum(c["caught"] for c in crises)
        self.assertEqual(false_critical, 0)
        self.assertGreater(len(crises), 50)
        self.assertGreaterEqual(caught / len(crises), 0.95)


if __name__ == "__main__":
    unittest.main()
