"""group_crash_rows: the crash list, with repeats of one bug folded into loops."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sc_stats import group_crash_rows, enrich_crash  # noqa: E402


def crash(at, digest=None, **kw):
    c = {"exception": "EXCEPTION_ACCESS_VIOLATION", "at": at}
    if digest:
        c["digest"] = digest
    c.update(kw)
    return (at[:10], c)


SHIPS = {"ANVL_Asgard": "Asgard", "RSI_Polaris": "Polaris"}


def ship_name(cls):
    return SHIPS.get(cls)


class CrashRowTests(unittest.TestCase):
    def test_same_digest_within_30_min_is_one_loop(self):
        rows = group_crash_rows([crash("2026-03-05T16:27:00", "71a6"), crash("2026-03-05T16:28:00", "71a6"),
                                 crash("2026-03-05T16:42:00", "71a6")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["loop"], ["2026-03-05T16:27:00", "2026-03-05T16:28:00", "2026-03-05T16:42:00"])

    def test_gap_is_measured_from_the_previous_crash_not_the_first(self):
        # 16:27 -> 16:55 -> 17:23: each step is under 30 min, the whole span is not
        rows = group_crash_rows([crash("2026-03-05T16:27:00", "71a6"), crash("2026-03-05T16:55:00", "71a6"),
                                 crash("2026-03-05T17:23:00", "71a6")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["loop"]), 3)

    def test_gap_over_30_min_splits_the_loop(self):
        rows = group_crash_rows([crash("2026-03-05T16:27:00", "71a6"), crash("2026-03-05T17:00:00", "71a6")])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("loop" not in r for r in rows))

    def test_exactly_30_min_still_loops(self):
        rows = group_crash_rows([crash("2026-03-05T16:00:00", "71a6"), crash("2026-03-05T16:30:00", "71a6")])
        self.assertEqual(len(rows), 1)

    def test_different_digests_never_share_a_loop(self):
        rows = group_crash_rows([crash("2026-03-05T16:27:00", "71a6"), crash("2026-03-05T16:28:00", "bbbb")])
        self.assertEqual(len(rows), 2)

    def test_interleaved_digests_loop_independently(self):
        rows = group_crash_rows([crash("2026-03-05T16:00:00", "aaaa"), crash("2026-03-05T16:05:00", "bbbb"),
                                 crash("2026-03-05T16:10:00", "aaaa")])
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]["loop"]), 2)

    def test_unknown_digest_or_missing_at_never_loops(self):
        rows = group_crash_rows([crash("2026-03-05T16:27:00"), crash("2026-03-05T16:28:00")])
        self.assertEqual(len(rows), 2)
        rows = group_crash_rows([("2026-03-05", {"exception": "X", "digest": "71a6"}),
                                 ("2026-03-05", {"exception": "X", "digest": "71a6"})])
        self.assertEqual(len(rows), 2)

    def test_newest_first_by_loop_end(self):
        # the loop starts at 10:00 but ends 10:40, so it sorts above the lone 10:30 crash
        rows = group_crash_rows([crash("2026-03-05T10:00:00", "aaaa"), crash("2026-03-05T10:30:00", "bbbb"),
                                 crash("2026-03-05T10:20:00", "aaaa"), crash("2026-03-05T10:40:00", "aaaa"),
                                 crash("2026-03-05T12:00:00", "cccc")])
        self.assertEqual([r["digest"] for r in rows], ["cccc", "aaaa", "bbbb"])
        self.assertEqual(rows[1]["loop"][-1], "2026-03-05T10:40:00")

    def test_loop_row_takes_fields_from_the_earliest_crash(self):
        rows = group_crash_rows([crash("2026-03-05T16:27:00", "71a6", phase="game", into_s=900, ws_mb=100),
                                 crash("2026-03-05T16:28:00", "71a6", phase="loading", into_s=20, ws_mb=999)])
        self.assertEqual(rows[0]["at"], "2026-03-05T16:27:00")
        self.assertEqual(rows[0]["phase"], "game")
        self.assertEqual(rows[0]["into_s"], 900)
        self.assertEqual(rows[0]["ws_mb"], 100)

    def test_seen_and_first_count_the_digest(self):
        rows = group_crash_rows([crash("2026-03-05T16:27:00", "71a6"), crash("2026-07-19T09:00:00", "71a6")])
        self.assertEqual([r["seen"] for r in rows], [2, 2])
        self.assertEqual([r["first"] for r in rows], ["2026-03-05", "2026-03-05"])

    def test_no_digest_means_seen_one_and_no_first(self):
        rows = group_crash_rows([crash("2026-03-05T16:27:00")])
        self.assertEqual(rows[0]["seen"], 1)
        self.assertNotIn("first", rows[0])

    def test_old_archive_crash_keeps_working(self):
        rows = group_crash_rows([("2026-03-05", {"exception": "EXCEPTION_ACCESS_VIOLATION"})])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["exception"], "EXCEPTION_ACCESS_VIOLATION")
        for k in ("phase", "into_s", "where", "ship", "last_err", "loop", "at"):
            self.assertNotIn(k, rows[0])

    def test_exception_defaults_to_unknown(self):
        rows = group_crash_rows([("2026-03-05", {})])
        self.assertEqual(rows[0]["exception"], "UNKNOWN")

    def test_ship_names_resolved_through_callback(self):
        rows = group_crash_rows([crash("2026-03-05T16:27:00", "71a6", ship_cls="ANVL_Asgard",
                                       last_err=[{"tag": "x", "n": 2, "cls": "ANVL_Asgard"}])], ship_name)
        self.assertEqual(rows[0]["ship"], "Asgard")
        self.assertEqual(rows[0]["last_err"][0]["ship"], "Asgard")

    def test_attach_target_is_tried_before_class(self):
        c = crash("2026-03-05T16:27:00", last_err=[
            {"tag": "a", "n": 1, "cls": "ANVL_Asgard", "to": "RSI_Polaris"},
            {"tag": "b", "n": 1, "cls": "NOPE_Thing", "to": "NOPE_Other"},
            {"tag": "c", "n": 1, "cls": "ANVL_Asgard", "to": "NOPE_Other"}])
        errs = group_crash_rows([c], ship_name)[0]["last_err"]
        self.assertEqual(errs[0]["ship"], "Polaris")
        self.assertNotIn("ship", errs[1])
        self.assertEqual(errs[2]["ship"], "Asgard")

    def test_no_callback_or_unknown_ship_adds_no_ship(self):
        c = crash("2026-03-05T16:27:00", ship_cls="ANVL_Asgard")
        self.assertNotIn("ship", group_crash_rows([c])[0])
        self.assertNotIn("ship", group_crash_rows([crash("2026-03-05T16:27:00", ship_cls="NOPE_X")], ship_name)[0])

    def test_archived_crash_dicts_are_not_mutated(self):
        c = crash("2026-03-05T16:27:00", ship_cls="ANVL_Asgard", last_err=[{"tag": "x", "n": 1, "cls": "ANVL_Asgard"}])
        group_crash_rows([c], ship_name)
        self.assertNotIn("ship", c[1])
        self.assertNotIn("ship", c[1]["last_err"][0])


class EnrichTests(unittest.TestCase):
    def test_enrich_keeps_every_key_and_adds_ships(self):
        src = {"exception": "X", "peak_commit_mb": 5, "ship_cls": "ANVL_Asgard",
               "last_err": [{"tag": "x", "n": 1, "to": "RSI_Polaris"}]}
        out = enrich_crash(src, ship_name)
        self.assertEqual(out["peak_commit_mb"], 5)
        self.assertEqual(out["ship"], "Asgard")
        self.assertEqual(out["last_err"][0]["ship"], "Polaris")
        self.assertNotIn("ship", src)


if __name__ == "__main__":
    unittest.main()
