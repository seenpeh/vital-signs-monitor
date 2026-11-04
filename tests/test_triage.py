import unittest

from monitor.processing import EdgeProcessor
from monitor.run import tick
from monitor.simulator import make_patients
from monitor.triage import (CALM_SAMPLES as W, CRISES, GRACE_S, Triage,
                            alarms, held_points, level_for, points, score)


def values(hr=75, spo2=98, sbp=125):
    return {"hr": hr, "spo2": spo2, "sbp": sbp}


def feed(triage, samples, raw_hr=None):
    """Feed processed samples; return the list of level changes."""
    changes = []
    for i, p in enumerate(samples):
        hr = raw_hr[i] if raw_hr else p["hr"]
        change = triage.assess({"hr": hr}, p)
        if change:
            changes.append((i, change[0]))
    return changes


def settled(**vitals):
    """A Triage that has seen a full window of these values."""
    t = Triage()
    feed(t, [values(**vitals)] * W)
    return t


class ScoringTest(unittest.TestCase):
    def test_news2_bands(self):
        cases = {"hr": [(40, 3), (41, 1), (50, 1), (51, 0), (90, 0), (91, 1),
                        (110, 1), (111, 2), (130, 2), (131, 3)],
                 "spo2": [(91, 3), (92, 2), (93, 2), (94, 1), (95, 1),
                          (96, 0), (100, 0)],
                 "sbp": [(90, 3), (91, 2), (100, 2), (101, 1), (110, 1),
                         (111, 0), (219, 0), (220, 3)]}
        for vital, pairs in cases.items():
            for value, expected in pairs:
                self.assertEqual(points(vital, value), expected,
                                 (vital, value))

    def test_hysteresis_scores_up_at_once_and_down_past_a_margin(self):
        self.assertEqual(held_points("hr", 91, 0), 1)
        self.assertEqual(held_points("hr", 88, 1), 1)  # back in 51-90, close
        self.assertEqual(held_points("hr", 85, 1), 0)  # 5 BPM clear
        self.assertEqual(held_points("hr", 128, 3), 3)
        self.assertEqual(held_points("hr", 125, 3), 2)
        self.assertEqual(held_points("spo2", 96, 1), 1)
        self.assertEqual(held_points("spo2", 97, 1), 0)
        self.assertEqual(held_points("spo2", None, 3), 3)  # probe off

    def test_levels(self):
        def level(hr, spo2, sbp):
            return level_for({"hr": hr, "spo2": spo2, "sbp": sbp})

        self.assertEqual(level(0, 0, 0), "normal")
        self.assertEqual(level(1, 2, 1), "low")
        self.assertEqual(level(0, 3, 0), "low-medium")  # a single 3
        self.assertEqual(level(1, 3, 1), "medium")
        self.assertEqual(level(2, 2, 2), "medium")
        self.assertEqual(level(3, 3, 1), "high")


class RuleTest(unittest.TestCase):
    def test_level_rises_at_once(self):
        changes = feed(settled(), [values(spo2=88)] * W)
        self.assertEqual(changes, [(0, "low-medium")])

    def test_short_red_score_alarms_then_calms_after_30_s(self):
        # like a burst of motion glitches that got through the filter
        samples = [values(spo2=88)] + [values()] * W
        self.assertEqual(feed(settled(), samples),
                         [(0, "low-medium"), (W, "normal")])

    def test_glitches_showing_normal_values_do_not_end_an_alarm(self):
        t = settled(spo2=88)
        self.assertEqual(t.level, "low-medium")
        samples = [values()] * 4 + [values(spo2=88)] * W
        self.assertEqual(feed(t, samples), [])

    def test_level_falls_after_30_s(self):
        changes = feed(settled(spo2=88), [values()] * W)
        self.assertEqual(changes, [(W - 1, "normal")])

    def test_value_near_the_edge_keeps_its_points(self):
        t = settled(hr=95)
        self.assertEqual(t.level, "low")
        self.assertEqual(feed(t, [values(hr=88)] * 2 * W), [])
        self.assertEqual(feed(t, [values(hr=84)] * W), [(W - 1, "normal")])

    def test_flicker_on_a_limit_gives_one_alert(self):
        flicker = [values(spo2=95), values(spo2=96)] * 20
        self.assertEqual(feed(Triage(), flicker), [(0, "low")])

    def test_totals(self):
        self.assertEqual(settled(hr=95, sbp=105).level, "low")
        self.assertEqual(settled(spo2=88, hr=115, sbp=105).level, "medium")
        self.assertEqual(settled(spo2=88, hr=135, sbp=105).level, "high")

    def test_probe_off_keeps_the_alarm(self):
        t = settled(spo2=88)
        self.assertEqual(feed(t, [values(spo2=None)] * 2 * W), [])

    def test_arrhythmia_needs_irregular_beats(self):
        fast = [values(hr=115)] * 30
        irregular = [115 + (12 if i % 2 else -12) for i in range(30)]
        # HR 115 scores 2; from 11 raw samples (10 jumps) on, the rhythm is
        # irregular and HR scores 3
        self.assertEqual(feed(Triage(), fast, irregular),
                         [(0, "low"), (10, "low-medium")])
        steady = feed(Triage(), fast)  # fast but regular: exercise, fever
        self.assertEqual(steady, [(0, "low")])


def simulate(seed, ticks=5400, patients=3):
    """Run the whole closed loop like monitor.run; return events, alerts."""
    ps = make_patients(patients, seed)
    procs = {p.id: EdgeProcessor() for p in ps}
    triages = {p.id: Triage() for p in ps}
    events, alerts = [], []
    for i in range(ticks):
        _, _, started, changes, ended, _ = tick(ps, procs, triages, i * 2.0, 2.0)
        events += started
        alerts += changes
        for e in ended:  # like storage.save: close the patient's open events
            for open_event in events:
                if (open_event["patient_id"] == e["patient_id"]
                        and open_event["end_ts"] is None):
                    open_event["end_ts"] = e["end_ts"]
    return events, alerts


class GroundTruthTest(unittest.TestCase):
    """Score the whole closed loop on 9 simulated patient-hours."""

    def test_crises_caught_and_false_alarms_only_from_artefacts(self):
        events, alerts = simulate(seed=1)
        s = score(events, alerts, now=5400 * 2.0)
        done = s["caught"] + s["covered"] + s["missed"]
        self.assertGreater(s["crises"], 50)
        self.assertGreaterEqual((s["caught"] + s["covered"]) / done, 0.95)
        self.assertIn("worsening", {e["kind"] for e in events})
        # the level rises at once, so a glitch burst that gets through the
        # filter raises a false alarm; a stable patient's drift never does
        for a in alarms(alerts):
            mine = [e for e in events if e["patient_id"] == a["patient_id"]]
            in_crisis = any(
                e["kind"] in CRISES and e["start_ts"] <= a["ts"] <=
                (e["end_ts"] if e["end_ts"] is not None else a["ts"])
                + GRACE_S for e in mine)
            glitch = any(e["kind"] in ("spo2_dip", "dropout")
                         and a["ts"] - 30 <= e["start_ts"] <= a["ts"]
                         for e in mine)
            self.assertTrue(in_crisis or glitch, a)


class ScoreTest(unittest.TestCase):
    def test_caught_covered_missed_pending_and_false(self):
        def crisis(pid, start, end):
            return {"patient_id": pid, "kind": "spo2_drop",
                    "start_ts": start, "end_ts": end}

        def alert(pid, ts, level):
            return {"patient_id": pid, "ts": ts, "level": level}

        events = [crisis("P1", 100, 200),   # caught 30 s in
                  crisis("P1", 205, 260),   # still alarming: covered
                  crisis("P2", 100, 150),   # no alert: missed
                  crisis("P3", 900, None),  # just started: pending
                  {"patient_id": "P2", "kind": "spike", "start_ts": 500,
                   "end_ts": 502}]
        alerts = [alert("P1", 130, "medium"),
                  alert("P2", 600, "low-medium"),  # no crisis: false
                  alert("P1", 700, "low-medium")]  # a step down: no alarm
        s = score(events, alerts, now=910)
        self.assertEqual((s["crises"], s["caught"], s["covered"],
                          s["missed"], s["pending"]), (4, 1, 1, 1, 1))
        self.assertEqual(s["false_alarms"], 1)
        self.assertEqual(s["median_delay_s"], 30)
        self.assertEqual(s["artefacts"], 1)

    def test_only_rises_into_alarm_levels_are_alarms(self):
        alerts = [{"patient_id": "P1", "ts": t, "level": level}
                  for t, level in [(1, "low"), (2, "low-medium"),
                                   (3, "high"), (4, "medium"), (5, "low"),
                                   (6, "medium")]]
        self.assertEqual([a["ts"] for a in alarms(alerts)], [2, 3, 6])


if __name__ == "__main__":
    unittest.main()
