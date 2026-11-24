"""Telemetry index, retention, session matching, career folding."""
import json
import os
import sys
import tempfile
import threading
import time
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

    def rec(self, started, steps, channel="LIVE", groups=None):
        """Write a real recording named after its local start time."""
        os.makedirs(os.path.join(self.root, channel), exist_ok=True)
        path = os.path.join(self.root, channel, started.strftime("%Y%m%d-%H%M%S") + ".jsonl.gz")
        header = {"v": 1, "channel": channel, "build": "1",
                  "started": started.isoformat(timespec="seconds")}
        w = tlm.RecordingWriter(path, header, "full",
                                tlm.DEFAULT_GROUPS if groups is None else groups)
        for t, flat in steps:
            w.add(t, flat, [])
        w.close()
        old = time.time() - 60          # finished a while ago, not a file being written
        os.utime(path, (old, old))
        return path


W2 = [(0, {WALLET: 100}), (5, {WALLET: 200})]


class IndexTests(IndexBase):
    def test_refresh_and_version_bump(self):
        a = self.rec(datetime(2026, 11, 24, 19, 0, 0), W2)
        self.rec(datetime(2026, 11, 24, 20, 0, 0), W2)
        idx = st.refresh_index(self.root)
        self.assertEqual(len(idx), 2)
        self.assertEqual(len(st.load_index(self.root)), 2)
        v = st.SUMMARY_VERSION
        with mock.patch.object(st, "SUMMARY_VERSION", v + 1):
            idx = st.refresh_index(self.root)
        self.assertEqual({e["v"] for e in idx.values()}, {v + 1})
        os.remove(a)
        with mock.patch.object(st, "SUMMARY_VERSION", v + 2):
            idx = st.refresh_index(self.root)
        self.assertEqual(idx[os.path.basename(a)]["v"], v + 1)
        self.assertEqual(idx["20261124-200000.jsonl.gz"]["v"], v + 2)

    def test_refresh_skips_recording_file(self):
        p = self.rec(datetime(2026, 11, 24, 19, 0, 0), W2)
        self.assertEqual(st.refresh_index(self.root, {os.path.basename(p)}), {})

    def test_retention_keeps_summaries(self):
        now = datetime(2026, 11, 24, 12, 0, 0)
        self.rec(now - timedelta(days=40), W2)
        new = self.rec(now - timedelta(days=10), W2)
        st.refresh_index(self.root)
        self.assertEqual(st.apply_retention(self.root, 30, now), 1)
        self.assertEqual(st.recording_files(self.root), [new])
        self.assertEqual(len(st.load_index(self.root)), 2)
        self.assertEqual(st.apply_retention(self.root, None, now), 0)

    def test_retention_indexes_before_delete(self):
        now = datetime(2026, 11, 24, 12, 0, 0)
        old = self.rec(now - timedelta(days=40), W2)
        self.assertEqual(st.load_index(self.root), {})
        self.assertEqual(st.apply_retention(self.root, 30, now), 1)
        self.assertFalse(os.path.exists(old))
        self.assertIn(os.path.basename(old), st.load_index(self.root))

    def test_retention_keeps_unreadable_file(self):
        now = datetime(2026, 11, 24, 12, 0, 0)
        bad = os.path.join(self.root, "LIVE", (now - timedelta(days=40)).strftime("%Y%m%d-%H%M%S")
                           + ".jsonl.gz")
        os.makedirs(os.path.dirname(bad))
        with open(bad, "wb") as f:
            f.write(b"not gzip")
        self.assertEqual(st.apply_retention(self.root, 30, now), 0)
        self.assertTrue(os.path.exists(bad))

    def test_size_cap_deletes_oldest_first(self):
        base = datetime(2026, 11, 20, 12, 0, 0)
        a = self.rec(base, W2)
        b = self.rec(base + timedelta(days=1), W2)
        c = self.rec(base + timedelta(days=2), W2)
        size = os.path.getsize(a)
        self.assertEqual(st.apply_size_cap(self.root, None), 0)
        self.assertEqual(st.apply_size_cap(self.root, size * 3), 0)
        self.assertEqual(st.apply_size_cap(self.root, size * 2), 1)
        self.assertEqual(st.recording_files(self.root), sorted([b, c]))
        self.assertIn(os.path.basename(a), st.load_index(self.root))

    def test_size_cap_skips_open_recording(self):
        base = datetime(2026, 11, 20, 12, 0, 0)
        a = self.rec(base, W2)
        b = self.rec(base + timedelta(days=1), W2)
        self.assertEqual(st.apply_size_cap(self.root, 1, skip={os.path.basename(a)}), 1)
        self.assertEqual(st.recording_files(self.root), [a])
        self.assertFalse(os.path.exists(b))

    def test_delete_all_indexes_first(self):
        p = self.rec(datetime(2026, 11, 24, 19, 0, 0), W2)
        self.assertEqual(st.delete_all_recordings(self.root), 1)
        self.assertFalse(os.path.exists(p))
        self.assertEqual(st.load_index(self.root)[os.path.basename(p)]["sum"]["uec_end"], 200)

    def test_refresh_threads(self):
        self.rec(datetime(2026, 11, 24, 19, 0, 0), W2)
        self.rec(datetime(2026, 11, 24, 20, 0, 0), W2)
        names = ["20261124-190000.jsonl.gz", "20261124-200000.jsonl.gz"]
        for _ in range(5):
            if os.path.exists(os.path.join(self.root, "index.json")):
                os.remove(os.path.join(self.root, "index.json"))
            ts = [threading.Thread(target=st.refresh_index, args=(self.root, {n}))
                  for n in reversed(names)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(sorted(st.load_index(self.root)), sorted(names))

    def test_empty_recording_indexed_once(self):
        p = self.rec(datetime(2026, 11, 24, 19, 0, 0), [])
        idx = st.refresh_index(self.root)
        e = idx[os.path.basename(p)]
        self.assertTrue(e["empty"])
        self.assertEqual(e["v"], st.SUMMARY_VERSION)
        with mock.patch.object(st, "summarize", side_effect=AssertionError):
            st.refresh_index(self.root)
        s = {"start": datetime(2026, 11, 24, 19, 0, 0), "dur": 600.0}
        m, un = st.match([s], idx, datetime(2026, 12, 1))
        self.assertEqual((m, un), ({}, []))

    def test_refresh_survives_bad_file(self):
        bad = os.path.join(self.root, "LIVE", "20261124-180000.jsonl.gz")
        os.makedirs(os.path.dirname(bad))
        with open(bad, "wb") as f:
            f.write(b"not gzip")
        self.rec(datetime(2026, 11, 24, 19, 0, 0), W2)
        self.assertEqual(list(st.refresh_index(self.root)), ["20261124-190000.jsonl.gz"])

    def test_recent_file_skipped(self):
        p = self.rec(datetime(2026, 11, 24, 19, 0, 0), W2)
        now = time.time()
        os.utime(p, (now - 2, now - 2))
        self.assertEqual(st.refresh_index(self.root), {})
        self.assertEqual(st.delete_all_recordings(self.root), 0)
        self.assertEqual(st.apply_retention(self.root, 30, datetime(2027, 6, 1)), 0)
        self.assertTrue(os.path.exists(p))
        os.utime(p, (now - 11, now - 11))
        self.assertEqual(list(st.refresh_index(self.root)), [os.path.basename(p)])
        self.assertEqual(st.apply_retention(self.root, 30, datetime(2027, 6, 1)), 1)

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

    def test_open_session_same_start_one_match(self):
        self.rec(datetime(2026, 11, 24, 21, 50, 0), W2)
        now = datetime(2026, 11, 24, 21, 30, 0)
        a = {"start": datetime(2026, 11, 24, 19, 0, 0), "dur": 9000.0 - 20}
        b = {"start": datetime(2026, 11, 24, 19, 0, 0), "dur": 9000.0 - 10}
        m, un = st.match([a, b], self.idx(), now)
        self.assertEqual(sum(len(v) for v in m.values()), 1)
        self.assertEqual(un, [])

    def test_overlapping_entries_keep_longer(self):
        def e(name, started, ended, dist):
            return {"file": name, "channel": "LIVE", "started": started, "ended": ended,
                    "v": st.SUMMARY_VERSION, "sum": {"dist_m": dist}}
        idx = {"a": e("a", "2026-11-24T19:00:00", "2026-11-24T19:30:00", 100.0),
               "b": e("b", "2026-11-24T19:00:05", "2026-11-24T19:20:00", 50.0),
               "c": e("c", "2026-11-24T19:30:00", "2026-11-24T19:40:00", 7.0)}
        s = {"start": datetime(2026, 11, 24, 18, 59, 0), "dur": 3600.0}
        m, un = st.match([s], idx, self.NOW)
        self.assertEqual(sorted(x["file"] for x in m[id(s)]), ["a", "c"])
        self.assertEqual(un, [])
        idx["b"]["ended"] = "2026-11-24T19:35:00"      # now b is the longer one
        m, _ = st.match([s], idx, self.NOW)
        self.assertEqual(sorted(x["file"] for x in m[id(s)]), ["b"])
        att, _ = st.attach([s], idx, self.NOW)
        self.assertEqual(att[0]["_tlm"]["dist_m"], 50.0)

    def test_attach_skips_bad_entry(self):
        good = {"file": "g", "channel": "LIVE", "started": "2026-11-24T19:00:00",
                "ended": "2026-11-24T19:10:00", "v": 1, "sum": {"dist_m": 5.0, "warn": {"pwr": 1}}}
        bad = {"file": "b", "channel": "LIVE", "started": "2026-11-24T19:20:00",
               "ended": "2026-11-24T19:30:00", "v": 1, "sum": {"dist_m": "x", "warn": 3}}
        s = {"start": datetime(2026, 11, 24, 18, 59, 0), "dur": 3600.0}
        other = {"start": datetime(2026, 11, 25, 18, 59, 0), "dur": 3600.0}
        good2 = dict(good, file="g2", started="2026-11-25T19:00:00", ended="2026-11-25T19:10:00")
        att, _ = st.attach([s, other], {"g": good, "b": bad, "g2": good2}, self.NOW)
        self.assertEqual(att[0]["_tlm"]["dist_m"], 5.0)
        self.assertEqual(att[0]["_tlm"]["warn"], {"pwr": 1})
        self.assertEqual(att[1]["_tlm"]["dist_m"], 5.0)

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
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = IndexBase()
        base.root = tmp.name
        t_b = datetime(2026, 11, 24, 21, 0, 0)
        t_a = datetime(2026, 11, 24, 19, 0, 0)
        # B: old summary version, file expired, only the wallet group
        base.rec(t_b, W2, groups=["wallet"])
        st.refresh_index(tmp.name)
        for p in st.recording_files(tmp.name):
            os.remove(p)
        # A: recorded after a version bump, flight and wallet
        g = {"vehicle.state.flight.instruments.g_meter.current": 4.0,
             "vehicle.state.flight.instruments.g_meter.max": 10.0}
        base.rec(t_a, [(0, dict(g, **{SPEED: 50, WALLET: 5})), (5, dict(g, **{SPEED: 50, WALLET: 5}))],
                 groups=["flight", "wallet"])
        v = st.SUMMARY_VERSION
        with mock.patch.object(st, "SUMMARY_VERSION", v + 1):
            idx = st.refresh_index(tmp.name)
        self.assertEqual(sorted(e["v"] for e in idx.values()), [v, v + 1])
        sessions = [{"start": t_a, "dur": 600.0}, {"start": t_b, "dur": 600.0}]
        att, un = st.attach(sessions, idx, datetime(2026, 12, 1))
        self.assertEqual(un, [])
        acc = st.new_acc()
        for s in att:
            st.fold_tlm(acc, s.get("_tlm"))
        out = st.finalize_tlm(acc, 2)
        self.assertEqual(out["n_tlm"], 2)
        self.assertEqual(out["stats"]["max_g"], {"v": 4.0, "n": 1})
        self.assertEqual(out["stats"]["uec_end"]["n"], 2)

    def test_fold_basic(self):
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

    def test_ifcs_share_base(self):
        """IFCS shares divide by flight time of the sessions that had settings on."""
        acc = st.new_acc()
        st.fold_tlm(acc, {"t_flying": 100.0, "t_ifcs": {"ifcs_esp": 50.0}})
        st.fold_tlm(acc, {"t_flying": 300.0})
        s = st.finalize_tlm(acc, 2)["stats"]
        self.assertEqual(s["t_flying"]["v"], 400.0)
        self.assertEqual(s["t_flying_ifcs"], {"v": 100.0, "n": 1})

    def test_records_and_first_last(self):
        acc = st.new_acc()
        st.fold_tlm(acc, {"max_g": 3.0, "uec_end": 10})
        st.fold_tlm(acc, {"max_g": 5.5, "min_health": 20, "uec_start": 7})
        st.fold_tlm(acc, {"min_health": 10, "uec_start": 9})
        s = st.finalize_tlm(acc, 3)["stats"]
        self.assertEqual(s["max_g"]["v"], 5.5)
        self.assertEqual(s["min_health"], {"v": 10, "n": 2})
        self.assertEqual(s["uec_start"]["v"], 7)


class BuildTests(unittest.TestCase):
    """Career folding through the real page build."""

    def session(self, start, patch):
        import sc_stats
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Game.log")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("<2026-11-24T10:00:00.000Z> Log started\n"
                         "<2026-11-24T10:00:01.000Z> [Notice] <Legacy login response> "
                         "Handle[archelium] login ok\n"
                         "<2026-11-24T11:00:00.000Z> end\n")
            base = sc_stats.scan_log(p)
        return dict(base, start=start, dur=3600.0, patch=patch, channel="LIVE")

    def entry(self, started, sum_):
        return {"file": started.strftime("%Y%m%d-%H%M%S") + ".jsonl.gz", "channel": "LIVE",
                "started": started.isoformat(timespec="seconds"), "v": st.SUMMARY_VERSION,
                "sum": sum_}

    def test_build_account_folds_tlm(self):
        import copy
        import sc_stats
        t1 = datetime(2026, 11, 20, 19, 0, 0)
        t2 = datetime(2026, 11, 22, 19, 0, 0)
        t3 = datetime(2026, 11, 23, 19, 0, 0)
        # archive order is not time order: the later session comes first
        sessions = [self.session(t2, "4.3"), self.session(t1, "4.3"),
                    self.session(t3, "4.2")]
        before = copy.deepcopy(sessions)
        idx = {}
        for t, s in ((t1, {"max_g": 5.0, "dist_m": 100.0, "uec_start": 10, "uec_end": 50,
                           "uec_series": [[t1.isoformat(), 50]]}),
                     (t2, {"max_g": 9.0, "dist_m": 200.0, "uec_start": 50, "uec_end": 70,
                           "uec_series": [[t2.isoformat(), 70]]}),
                     (datetime(2026, 11, 1, 12, 0, 0), {"max_g": 1.0})):
            e = self.entry(t, s)
            idx[e["file"]] = e
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "telemetry")
            os.makedirs(root)
            st.save_index(root, idx)
            with mock.patch.object(sc_stats, "app_dir", return_value=d), \
                    mock.patch.object(sc_stats, "fetch_profile_cached", return_value=(None, [])):
                data = sc_stats.build_from_sessions(sessions)
        self.assertEqual(sessions, before)
        acct = data["ch"]["LIVE"]["acct"]["archelium"]
        tl = acct["career"]["tlm"]
        self.assertEqual((tl["n_sessions"], tl["n_tlm"]), (3, 2))
        self.assertEqual(tl["stats"]["max_g"], {"v": 9.0, "n": 2})
        self.assertEqual(tl["stats"]["dist_m"]["v"], 300.0)
        # first and last by session time, not by fold order
        self.assertEqual(tl["stats"]["uec_start"]["v"], 10)
        self.assertEqual(tl["stats"]["uec_end"]["v"], 70)
        self.assertEqual([p[1] for p in tl["wallet_series"]], [50, 70])
        self.assertEqual(acct["patches"]["4.2"]["tlm"]["n_tlm"], 0)
        self.assertEqual(acct["patches"]["4.3"]["tlm"]["n_tlm"], 2)
        self.assertEqual([e["file"] for e in sc_stats._TLM_UNMATCHED],
                         ["20261101-120000.jsonl.gz"])
        json.dumps(data, default=str)


if __name__ == "__main__":
    unittest.main()
