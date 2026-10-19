"""crash_breakdown: crashes split into yours-to-fix and Star Citizen bugs."""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402
from sc_stats import crash_breakdown  # noqa: E402

GPU = "STATUS_CRYENGINE_GPU_CRASH"
MEM = "STATUS_CRYENGINE_OUT_OF_SYSMEM"
AV = "EXCEPTION_ACCESS_VIOLATION"


def crash(exc, at, digest=None):
    c = {"exception": exc, "at": at}
    if digest:
        c["digest"] = digest
    return (at[:10], c)


class CrashBreakdownTests(unittest.TestCase):
    def test_split_by_actionable(self):
        b = crash_breakdown([crash(GPU, "2026-03-01T10:00:00"), crash(GPU, "2026-03-02T10:00:00"),
                             crash(AV, "2026-03-03T10:00:00"), crash(AV, "2026-03-04T10:00:00"),
                             crash(AV, "2026-03-05T10:00:00")], [])
        self.assertEqual(b["yours_n"], 2)
        self.assertEqual(b["game_n"], 3)
        self.assertEqual(b["yours"][0]["exc"], GPU)
        self.assertEqual(b["yours"][0]["n"], 2)
        self.assertEqual(b["yours"][0]["last"], "2026-03-02")
        self.assertEqual(b["yours_last"], "2026-03-02")

    def test_yours_sorted_by_count(self):
        b = crash_breakdown([crash(GPU, "2026-03-01T10:00:00"), crash(MEM, "2026-03-02T10:00:00"),
                             crash(MEM, "2026-03-03T10:00:00")], [])
        self.assertEqual([y["exc"] for y in b["yours"]], [MEM, GPU])

    def test_quiet_after_50_hours(self):
        play = [(datetime(2026, 4, 1 + i), 3600 * 10) for i in range(5)]
        b = crash_breakdown([crash(MEM, "2026-03-29T21:38:00")], play)
        self.assertTrue(b["yours"][0]["quiet"])
        self.assertTrue(b["yours_quiet"])

    def test_not_quiet_under_50_hours(self):
        play = [(datetime(2026, 4, 1 + i), 3600 * 10) for i in range(4)] + [(datetime(2026, 4, 9), 3600 * 9)]
        b = crash_breakdown([crash(MEM, "2026-03-29T21:38:00")], play)
        self.assertFalse(b["yours"][0]["quiet"])
        self.assertFalse(b["yours_quiet"])

    def test_play_before_the_crash_does_not_count(self):
        play = [(datetime(2026, 3, 1 + i), 3600 * 10) for i in range(10)]
        b = crash_breakdown([crash(MEM, "2026-03-29T21:38:00")], play)
        self.assertFalse(b["yours"][0]["quiet"])

    def test_quiet_is_per_type_and_yours_quiet_follows_latest(self):
        play = [(datetime(2026, 3, 10 + i), 3600 * 10) for i in range(6)]
        b = crash_breakdown([crash(GPU, "2026-03-01T10:00:00"), crash(MEM, "2026-03-20T10:00:00")], play)
        by = {y["exc"]: y for y in b["yours"]}
        self.assertTrue(by[GPU]["quiet"])
        self.assertFalse(by[MEM]["quiet"])
        self.assertEqual(b["yours_last"], "2026-03-20")
        self.assertFalse(b["yours_quiet"])

    def test_crash_without_at_uses_its_date_at_midnight(self):
        play = [(datetime(2026, 3, 29, 12), 3600 * 50)]
        b = crash_breakdown([("2026-03-29", {"exception": MEM})], play)
        self.assertTrue(b["yours"][0]["quiet"])
        self.assertEqual(b["yours"][0]["last"], "2026-03-29")

    def test_distinct_and_worst_digest(self):
        rows = [crash(AV, "2026-03-01T%02d:00:00" % i, "d%02d" % i + "0" * 6) for i in range(11)]
        rows += [crash(AV, "2026-03-02T%02d:00:00" % i, "ec6303d6aaaa") for i in range(5)]
        rows += [crash(AV, "2026-03-03T%02d:00:00" % i) for i in range(21)]   # no digest
        b = crash_breakdown(rows, [])
        g = b["game"][0]
        self.assertEqual(g["n"], 37)
        self.assertEqual(g["distinct"], 12)
        self.assertEqual(g["worst"], ["ec6303d6", 5])

    def test_worst_none_when_every_digest_is_unique(self):
        b = crash_breakdown([crash(AV, "2026-03-01T10:00:00", "aaaaaaaa11"),
                             crash(AV, "2026-03-02T10:00:00", "bbbbbbbb22")], [])
        self.assertEqual(b["game"][0]["distinct"], 2)
        self.assertIsNone(b["game"][0]["worst"])

    def test_unknown_goes_to_game_bugs(self):
        b = crash_breakdown([("2026-03-01", {"at": "2026-03-01T10:00:00"}),
                             crash("EXCEPTION_SOMETHING_NEW", "2026-03-02T10:00:00")], [])
        self.assertEqual(b["yours_n"], 0)
        self.assertEqual(b["game_n"], 2)
        self.assertEqual({g["exc"] for g in b["game"]}, {"UNKNOWN", "EXCEPTION_SOMETHING_NEW"})
        self.assertEqual(b["yours"], [])
        self.assertIsNone(b["yours_last"])
        self.assertFalse(b["yours_quiet"])

    def test_no_crashes_returns_none(self):
        self.assertIsNone(crash_breakdown([], []))


class FinalizeBreakdownTests(unittest.TestCase):
    def test_fold_records_play_and_finalize_exposes_breakdown(self):
        agg = sc_stats.blank_patch()
        self.assertEqual(agg["play"], [])
        agg["crashes"].append(("2026-03-29", {"exception": MEM, "at": "2026-03-29T21:38:00"}))
        agg["play"] = [(datetime(2026, 4, 1 + i), 3600 * 10) for i in range(5)]
        out = sc_stats.finalize(agg)
        self.assertTrue(out["crash_breakdown"]["yours_quiet"])
        self.assertIn("crash_causes", out)
        self.assertIn("crash_repeats", out)

    def test_finalize_without_crashes_has_no_breakdown(self):
        self.assertIsNone(sc_stats.finalize(sc_stats.blank_patch())["crash_breakdown"])


if __name__ == "__main__":
    unittest.main()
