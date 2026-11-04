import random
import unittest

from monitor.config import NORMAL_RANGES, SAMPLE_INTERVAL_S
from monitor.model import VitalModel, fit
from monitor.simulator import (NURSE_REACTION_S, NURSE_ROUNDS_S, Event,
                               Patient, make_patients)

MODELS = {"hr": VitalModel(0.0, 0.0005, (8.0, -8.0)),
          "spo2": VitalModel(0.1, 0.0, ()),
          "sbp": VitalModel(1.5, 0.01, (7.0, -7.0, -30.0))}


def drift_plus_jitter(n, drift_sd, jitter_sd, seed=1):
    rng = random.Random(seed)
    level, out = 100.0, []
    for _ in range(n):
        level += rng.gauss(0, drift_sd)
        out.append(level + rng.gauss(0, jitter_sd))
    return out


class FitTest(unittest.TestCase):
    def test_recovers_jitter(self):
        model = fit(drift_plus_jitter(20000, drift_sd=0.3, jitter_sd=1.5))
        self.assertAlmostEqual(model.jitter_sd, 1.5, delta=0.1)

    def test_spikes_are_separated_from_jitter(self):
        values = drift_plus_jitter(5000, drift_sd=0.3, jitter_sd=1.0)
        clean = fit(values)
        for i in range(100, 5000, 500):  # 10 spikes of +30
            values[i] += 30
        spiky = fit(values)
        self.assertAlmostEqual(spiky.jitter_sd, clean.jitter_sd, delta=0.05)
        self.assertEqual(len(spiky.spike_sizes) - len(clean.spike_sizes), 10)
        self.assertGreater(max(spiky.spike_sizes), 25)

    def test_missing_values_are_skipped(self):
        values = drift_plus_jitter(5000, drift_sd=0.3, jitter_sd=1.0)
        values[10:20] = [None] * 10
        self.assertAlmostEqual(fit(values).jitter_sd, 1.0, delta=0.15)


class PatientTest(unittest.TestCase):
    def quiet_patient(self):
        p = Patient("P1", MODELS, random.Random(0))
        p.p_clinical = p.p_artifact = 0  # no injected events
        return p

    def test_same_seed_same_readings(self):
        a, b = make_patients(3, seed=7), make_patients(3, seed=7)
        for pa, pb in zip(a, b):
            for _ in range(200):
                self.assertEqual(pa.step(), pb.step())

    def test_stable_patient_stays_mostly_in_normal_range(self):
        p = self.quiet_patient()
        readings = [p.step()[0] for _ in range(3000)]
        for vital, (low, high) in NORMAL_RANGES.items():
            inside = sum(low <= r[vital] <= high for r in readings)
            self.assertGreater(inside / len(readings), 0.95, vital)

    def crisis(self, p, kind, **shift):
        p.event = Event(kind, p.tick, shift=shift)

    def test_spo2_drop_moves_the_level_and_raises_hr(self):
        p = self.quiet_patient()
        normal_hr = p.step()[0]["hr"]
        self.crisis(p, "spo2_drop", spo2=-12, hr=20)
        mid = [p.step()[0] for _ in range(15)][-1]
        self.assertLess(mid["spo2"], 90)
        self.assertLess(p.level["spo2"], 90)  # the body changed, not the probe
        self.assertGreater(mid["hr"], normal_hr + 15)

    def test_untreated_crisis_lasts_until_rounds(self):
        p = self.quiet_patient()
        self.crisis(p, "spo2_drop", spo2=-12, hr=20)
        ends = [p.step()[2] for _ in range(200)]
        end = next(i for i, e in enumerate(ends) if e)
        rounds = NURSE_ROUNDS_S / SAMPLE_INTERVAL_S
        self.assertGreater(end, rounds)      # still low until noticed
        self.assertLess(end, rounds + 30)    # then recovers within a minute

    def test_alarm_starts_treatment_after_the_nurse_arrives(self):
        p = self.quiet_patient()
        self.crisis(p, "arrhythmia", hr=60)
        for _ in range(10):
            p.step()
        p.alarm()
        treat = p.event.treat_tick - p.tick
        low, high = (s / SAMPLE_INTERVAL_S for s in NURSE_REACTION_S)
        self.assertTrue(low <= treat <= high)
        p.alarm()  # a second alarm doesn't call the nurse again
        self.assertEqual(p.event.treat_tick - p.tick, treat)
        ends = [p.step()[2] for _ in range(100)]
        self.assertIsNotNone(next(e for e in ends if e))
        self.assertLess(abs(p.level["hr"] - p.baseline["hr"]), 13)

    def test_arrhythmia_is_fast_and_irregular(self):
        p = self.quiet_patient()
        self.crisis(p, "arrhythmia", hr=60)
        hr = [p.step()[0]["hr"] for _ in range(20)][5:]
        self.assertGreater(min(hr), 100)
        jumps = [abs(b - a) for a, b in zip(hr, hr[1:])]
        self.assertGreater(sorted(jumps)[len(jumps) // 2], 3)

    def test_dropout_gives_missing_value(self):
        p = self.quiet_patient()
        p.event = Event("dropout", p.tick, length=2, vital="sbp")
        self.assertIsNone(p.step()[0]["sbp"])
        self.assertIsNone(p.step()[0]["sbp"])
        self.assertIsNotNone(p.step()[0]["sbp"])  # event over

    def test_spo2_never_above_100(self):
        p = self.quiet_patient()
        p.baseline["spo2"] = p.level["spo2"] = 100
        self.assertTrue(all(p.step()[0]["spo2"] <= 100 for _ in range(500)))


if __name__ == "__main__":
    unittest.main()
