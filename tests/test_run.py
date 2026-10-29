import tempfile
import time
import unittest
from pathlib import Path

from monitor import storage
from monitor.processing import EdgeProcessor
from monitor.run import WARMUP_SAMPLES, warm_up
from monitor.simulator import make_patients


class WarmUpTest(unittest.TestCase):
    def test_prefills_stable_history_in_the_past(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            patients = make_patients(3, seed=1)
            rates = [(p.p_clinical, p.p_artifact) for p in patients]
            processors = {p.id: EdgeProcessor() for p in patients}

            warm_up(patients, processors, 2.0, conn)

            rows = storage.processed_since(conn, 0)
            self.assertEqual(len(rows), 3 * WARMUP_SAMPLES)
            self.assertLess(max(r["ts"] for r in rows), time.time())
            self.assertEqual(storage.latest_events(conn), [])  # stable start
            last = rows[-1]
            self.assertIsNotNone(last["spo2"])        # filter is full
            self.assertIsNotNone(last["spo2_trend"])  # stats are ready
            # events resume once live
            self.assertEqual([(p.p_clinical, p.p_artifact)
                              for p in patients], rates)
            conn.close()


if __name__ == "__main__":
    unittest.main()
