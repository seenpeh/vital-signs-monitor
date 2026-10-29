import unittest

from monitor.processing import EdgeProcessor, rolling_median, window_stats
from monitor.simulator import make_patients


def run_filter(values):
    """Feed SpO2 values through a processor; return the cleaned series."""
    proc = EdgeProcessor()
    return [proc.process({"hr": 70, "spo2": v, "sbp": 110})["spo2"]
            for v in values]


class FilterTest(unittest.TestCase):
    def test_hides_glitch_up_to_3_samples(self):
        clean = run_filter([97] * 10 + [80, 80, 80] + [97] * 10)
        self.assertEqual(min(c for c in clean if c is not None), 97)

    def test_no_value_until_window_is_full(self):
        # a 3-sample glitch at start-up would be the majority of 4 readings
        clean = run_filter([80, 80, 80, 97, 97, 97, 97])
        self.assertEqual(clean[:6], [None] * 6)
        self.assertEqual(clean[6], 97)

    def test_accepts_real_drop_after_4_samples(self):
        clean = run_filter([97] * 10 + [85] * 10)
        self.assertEqual(clean[12], 97)  # 3 low samples: not believed yet
        self.assertEqual(clean[13], 85)  # 4th low sample: accepted

    def test_missing_values(self):
        self.assertEqual(rolling_median([97, None, 96, None, 98, 97, 97]),
                         97)
        # probe off for 5 of 7 samples: too few real readings to trust
        self.assertIsNone(rolling_median([97, None, None, None, None, None,
                                          96]))


class WindowStatsTest(unittest.TestCase):
    def test_rising_line(self):
        # +1 per sample = +30 per minute at 2 s per sample
        mean, sd, trend = window_stats([100 + i for i in range(30)])
        self.assertAlmostEqual(mean, 114.5)
        self.assertAlmostEqual(trend, 30.0)
        self.assertGreater(sd, 0)

    def test_flat_and_too_short(self):
        self.assertEqual(window_stats([97] * 30 + [None]), (97, 0, 0))
        # under a minute of data: no trend yet
        self.assertEqual(window_stats([97, 98]), (97.5, 0.7071067811865476,
                                                  None))
        self.assertEqual(window_stats([97]), (97, None, None))
        self.assertEqual(window_stats([None]), (None, None, None))


class GroundTruthTest(unittest.TestCase):
    """Run hours of simulated data and score the filter on SpO2 < 90 %.

    Bursts of motion dips (4+ glitches within 14 s) do get through the
    median as short false lows; step 3 must reject them by persistence
    and heart rate. This test pins down the limit step 3 relies on.
    """

    def test_false_lows_short_and_real_drops_kept(self):
        drops, longest_false = [], 0
        for patient in make_patients(3, seed=1):
            proc = EdgeProcessor()
            drop, left, run = None, 0, 0
            for _ in range(5400):  # 3 hours
                reading, started = patient.step()
                clean = proc.process(reading)["spo2"]
                if started and started.kind == "spo2_drop":
                    drop = {"seen": False}
                    drops.append(drop)
                    left = started.length + 4  # +4: filter recovery time
                low = clean is not None and clean < 90
                if left > 0:
                    drop["seen"] = drop["seen"] or low
                    left -= 1
                    run = 0
                else:
                    run = run + 1 if low else 0
                    longest_false = max(longest_false, run)
        self.assertLess(longest_false * 2, 20)  # seconds; step 3 waits 20 s
        self.assertGreater(len(drops), 20)
        self.assertTrue(all(d["seen"] for d in drops))  # every drop caught


if __name__ == "__main__":
    unittest.main()
