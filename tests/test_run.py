import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from monitor import storage
from monitor.processing import EdgeProcessor
from monitor.run import JUMP_SAMPLES, WARMUP_SAMPLES, run, tick, warm_up
from monitor.simulator import Event, make_patients
from monitor.triage import Triage


class WarmUpTest(unittest.TestCase):
    def test_prefills_stable_history_in_the_past(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            patients = make_patients(3, seed=1)
            rates = [(p.p_clinical, p.p_artifact) for p in patients]
            processors = {p.id: EdgeProcessor() for p in patients}

            triages = {p.id: Triage() for p in patients}
            warm_up(patients, processors, triages, 2.0, conn)

            rows = storage.processed_since(conn, 0)
            self.assertEqual(len(rows), 3 * WARMUP_SAMPLES)
            self.assertLess(max(r["ts"] for r in rows), time.time())
            # stable start: only the real-data spikes, nothing injected
            self.assertEqual({e["kind"] for e in storage.latest_events(conn)},
                             {"spike"})
            last = rows[-1]
            self.assertIsNotNone(last["spo2"])        # filter is full
            self.assertIsNotNone(last["spo2_trend"])  # stats are ready
            # events resume once live
            self.assertEqual([(p.p_clinical, p.p_artifact)
                              for p in patients], rates)
            conn.close()


class EventLogTest(unittest.TestCase):
    def test_event_is_open_until_it_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            patients = make_patients(1, seed=1)
            p = patients[0]
            p.p_clinical = p.p_artifact = 0
            p.event = Event("dropout", p.tick, length=2, vital="sbp")
            storage.save(conn, [], [], [{"patient_id": p.id,
                                         "kind": "dropout", "vital": "sbp",
                                         "start_ts": 100.0, "end_ts": None}],
                         [])
            args = ({p.id: EdgeProcessor()}, {p.id: Triage()})
            storage.save(conn, *tick(patients, *args, 100.0, 2.0))
            self.assertIsNone(storage.latest_events(conn)[0]["end_ts"])
            storage.save(conn, *tick(patients, *args, 102.0, 2.0))
            self.assertEqual(storage.latest_events(conn)[0]["end_ts"], 104.0)
            conn.close()


class PauseTest(unittest.TestCase):
    def test_pause_flag_survives_reconnect_and_reset_clears_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            conn = storage.connect(path)
            self.assertFalse(storage.is_paused(conn))
            storage.set_paused(conn, True)
            conn.close()
            conn = storage.connect(path)  # schema re-run keeps the flag
            self.assertTrue(storage.is_paused(conn))
            storage.reset(conn)
            self.assertFalse(storage.is_paused(conn))
            conn.close()


class JumpTest(unittest.TestCase):
    def test_jump_runs_an_hour_and_stamps_treatments(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = storage.connect(Path(tmp) / "test.db")
            patients = make_patients(2, seed=3)
            args = ({p.id: EdgeProcessor() for p in patients},
                    {p.id: Triage() for p in patients})
            storage.request_jump(conn)
            with patch("monitor.run.time.sleep",
                       side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    run(patients, *args, 2.0, conn)
            self.assertFalse(storage.take_jump(conn))  # done once
            ts = [r["ts"] for r in storage.readings_since(conn, 0)]
            self.assertEqual(len(ts), 2 * JUMP_SAMPLES)
            self.assertGreater(max(ts), time.time() + 3500)
            crises = [e for e in storage.latest_events(conn, limit=-1)
                      if e["kind"] in ("spo2_drop", "arrhythmia")
                      and e["end_ts"] is not None]
            self.assertTrue(crises)
            for e in crises:  # ended crises were all treated, in between
                self.assertLessEqual(e["start_ts"], e["treat_ts"])
                self.assertLess(e["treat_ts"], e["end_ts"])
            conn.close()


if __name__ == "__main__":
    unittest.main()
