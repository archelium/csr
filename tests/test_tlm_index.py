"""Telemetry index, retention, session matching, career folding."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_telemetry as tlm  # noqa: E402
import sc_tlm_stats as st  # noqa: E402

WALLET = "actor.state.uec_balance"
SPEED = "vehicle.state.flight.instruments.speed"


class IndexBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def rec(self, started, steps, channel="LIVE"):
        """Write a real recording named after its local start time."""
        os.makedirs(os.path.join(self.root, channel), exist_ok=True)
        path = os.path.join(self.root, channel, started.strftime("%Y%m%d-%H%M%S") + ".jsonl.gz")
        header = {"v": 1, "channel": channel, "build": "1",
                  "started": started.isoformat(timespec="seconds")}
        w = tlm.RecordingWriter(path, header, "full", tlm.DEFAULT_GROUPS)
        for t, flat in steps:
            w.add(t, flat, [])
        w.close()
        return path


W2 = [(0, {WALLET: 100}), (5, {WALLET: 200})]


class IndexTests(IndexBase):
    def test_refresh_and_version_bump(self):
        a = self.rec(datetime(2026, 11, 24, 19, 0, 0), W2)
        self.rec(datetime(2026, 11, 24, 20, 0, 0), W2)
        idx = st.refresh_index(self.root)
        self.assertEqual(len(idx), 2)
        self.assertEqual(len(st.load_index(self.root)), 2)
        with mock.patch.object(st, "SUMMARY_VERSION", 2):
            idx = st.refresh_index(self.root)
        self.assertEqual({e["v"] for e in idx.values()}, {2})
        os.remove(a)
        with mock.patch.object(st, "SUMMARY_VERSION", 3):
            idx = st.refresh_index(self.root)
        self.assertEqual(idx[os.path.basename(a)]["v"], 2)
        self.assertEqual(idx["20261124-200000.jsonl.gz"]["v"], 3)

    def test_refresh_skips_recording_file(self):
        p = self.rec(datetime(2026, 11, 24, 19, 0, 0), W2)
        self.assertEqual(st.refresh_index(self.root, {os.path.basename(p)}), {})

    def test_retention_keeps_summaries(self):
        now = datetime(2026, 11, 24, 12, 0, 0)
        self.rec(now - timedelta(days=40), W2)
        self.rec(now - timedelta(days=10), W2)
        st.refresh_index(self.root)
        self.assertEqual(st.apply_retention(self.root, 30, now), 1)
        self.assertEqual(len(st.recording_files(self.root)), 1)
        self.assertEqual(len(st.load_index(self.root)), 2)
        self.assertEqual(st.apply_retention(self.root, None, now), 0)

    def test_usage_and_delete_all(self):
        p = self.rec(datetime(2026, 11, 24, 19, 0, 0), W2)
        self.rec(datetime(2026, 11, 24, 20, 0, 0), W2)
        self.assertGreater(st.disk_usage(self.root), 0)
        self.assertEqual(st.delete_all_recordings(self.root, {os.path.basename(p)}), 1)
        self.assertEqual(st.recording_files(self.root), [p])


class MatchTests(IndexBase):
    NOW = datetime(2026, 12, 1)

    def idx(self):
        return st.refresh_index(self.root)

    def test_match_local_time(self):
        self.rec(datetime(2026, 11, 24, 19, 51, 40), W2)
        idx = self.idx()
        next(iter(idx.values()))["started_utc"] = "2026-11-24T10:00:00"
        s = {"start": datetime(2026, 11, 24, 19, 50, 16), "dur": 3600.0}
        m, un = st.match([s], idx, self.NOW)
        self.assertEqual(len(m[id(s)]), 1)
        self.assertEqual(un, [])

    def test_unmatched_cases(self):
        self.rec(datetime(2026, 11, 24, 19, 0, 0), W2, "PTU")
        idx = self.idx()
        for s in ({"start": datetime(2026, 11, 24, 18, 50), "dur": 3600.0},
                  {"start": datetime(2026, 11, 24, 17, 0), "dur": 600.0, "channel": "PTU"},
                  {"start": None, "dur": 100.0}):
            m, un = st.match([s], idx, self.NOW)
            self.assertEqual(m, {})
            self.assertEqual(len(un), 1)
        s = {"start": datetime(2026, 11, 24, 18, 50), "dur": 3600.0, "channel": "PTU"}
        self.assertEqual(len(st.match([s], idx, self.NOW)[0][id(s)]), 1)

    def test_match_restart_two_segments(self):
        self.rec(datetime(2026, 11, 24, 19, 0, 0), [(0, {SPEED: 100}), (10, {SPEED: 100})])
        self.rec(datetime(2026, 11, 24, 19, 10, 0), [(0, {SPEED: 100}), (10, {SPEED: 100})])
        s = {"start": datetime(2026, 11, 24, 18, 59, 0), "dur": 1800.0}
        att, un = st.attach([s], self.idx(), self.NOW)
        self.assertEqual(un, [])
        self.assertEqual(att[0]["_tlm"]["dist_m"], 2000.0)

    def test_match_csr_opened_late(self):
        self.rec(datetime(2026, 11, 24, 19, 50, 0), W2)
        s = {"start": datetime(2026, 11, 24, 19, 0, 0), "dur": 7200.0}
        self.assertEqual(len(st.match([s], self.idx(), self.NOW)[0][id(s)]), 1)

    def test_open_session(self):
        self.rec(datetime(2026, 11, 24, 21, 50, 0), W2)
        now = datetime(2026, 11, 24, 21, 30, 0)
        s = {"start": datetime(2026, 11, 24, 19, 0, 0), "dur": 9000.0 - 20}
        self.assertEqual(len(st.match([s], self.idx(), now)[0][id(s)]), 1)
        later = {"start": datetime(2026, 11, 24, 22, 0, 0), "dur": 100.0}
        m, un = st.match([s, later], self.idx(), now)
        self.assertEqual(m, {})
        self.assertEqual(len(un), 1)

    def test_attach_does_not_mutate(self):
        self.rec(datetime(2026, 11, 24, 19, 0, 0), W2)
        s = {"start": datetime(2026, 11, 24, 19, 0, 0), "dur": 600.0}
        other = {"start": datetime(2026, 11, 20), "dur": 60.0}
        out, _ = st.attach([s, other], self.idx(), self.NOW)
        self.assertNotIn("_tlm", s)
        self.assertIn("_tlm", out[0])
        self.assertIs(out[1], other)


class CareerTests(unittest.TestCase):
    def test_coverage_per_stat(self):
        acc = st.new_acc()
        st.fold_tlm(acc, {"max_g": 4.0, "uec_end": 500, "dist_m": 100.0,
                          "uec_series": [["2026-11-24T19:00:00", 5]], "warn": {"pwr": 1}})
        st.fold_tlm(acc, {"uec_end": 900, "dist_m": 50.0, "warn": {"pwr": 2, "wpn": 1}})
        st.fold_tlm(acc, None)
        out = st.finalize_tlm(acc, 3)
        self.assertEqual((out["n_sessions"], out["n_tlm"]), (3, 2))
        s = out["stats"]
        self.assertEqual(s["max_g"], {"v": 4.0, "n": 1})
        self.assertEqual(s["uec_end"], {"v": 900, "n": 2})
        self.assertEqual(s["dist_m"]["v"], 150.0)
        self.assertEqual(s["warn"]["v"], {"pwr": 3, "wpn": 1})
        self.assertEqual(out["wallet_series"], [["2026-11-24T19:00:00", 5]])
        json.dumps(out)

    def test_records_and_first_last(self):
        acc = st.new_acc()
        st.fold_tlm(acc, {"max_g": 3.0, "uec_end": 10})
        st.fold_tlm(acc, {"max_g": 5.5, "min_health": 20, "uec_start": 7})
        st.fold_tlm(acc, {"min_health": 10, "uec_start": 9})
        s = st.finalize_tlm(acc, 3)["stats"]
        self.assertEqual(s["max_g"]["v"], 5.5)
        self.assertEqual(s["min_health"], {"v": 10, "n": 2})
        self.assertEqual(s["uec_start"]["v"], 7)


if __name__ == "__main__":
    unittest.main()
