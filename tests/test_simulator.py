import random
import unittest

from monitor.config import NORMAL_RANGES
from monitor.model import VitalModel, fit
from monitor.simulator import Event, Patient, make_patients

MODELS = {"hr": VitalModel(0.7, 1.5), "spo2": VitalModel(0.95, 1.5),
          "sbp": VitalModel(0.2, 3.0)}


class FitTest(unittest.TestCase):
    def test_hold_and_robust_step(self):
        # 2 holds, 2 moves of size 1, one 50-point artefact jump
        model = fit([80, 80, 80, 81, 82, 132])
        self.assertAlmostEqual(model.hold_prob, 2 / 5)
        self.assertAlmostEqual(model.step_sd, 1.4826)  # artefact ignored

    def test_missing_values_are_skipped(self):
        self.assertAlmostEqual(fit([80, None, 81, 81]).hold_prob, 1.0)


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

    def test_spo2_drop_goes_below_90_and_raises_hr(self):
        p = self.quiet_patient()
        normal_hr = p.step()[0]["hr"]
        p.event = Event("spo2_drop", p.tick, 30, size=12)
        mid = [p.step()[0] for _ in range(15)][-1]
        self.assertLess(mid["spo2"], 90)
        self.assertGreater(mid["hr"], normal_hr + 15)

    def test_dropout_gives_missing_value(self):
        p = self.quiet_patient()
        p.event = Event("dropout", p.tick, 2, vital="sbp")
        self.assertIsNone(p.step()[0]["sbp"])
        self.assertIsNone(p.step()[0]["sbp"])
        self.assertIsNotNone(p.step()[0]["sbp"])  # event over

    def test_spo2_never_above_100(self):
        p = self.quiet_patient()
        p.baseline["spo2"] = p.level["spo2"] = 100
        self.assertTrue(all(p.step()[0]["spo2"] <= 100 for _ in range(500)))


if __name__ == "__main__":
    unittest.main()
