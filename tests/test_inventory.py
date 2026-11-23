"""Home inventory: partial loads are flagged and skipped for the headline."""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

T0 = datetime(2026, 11, 1, 12, 0)
HOME = "3170699229"
CAP = 5_000_000_000            # 5,000 SCU in micro-SCU


def reads(*scu, step=1):
    return [(T0 + timedelta(days=i * step), u) for i, u in enumerate(scu)]


def agg(*scu, step=1):
    rows = []
    for i, u in enumerate(scu):
        t = T0 + timedelta(days=i * step)
        inv = {"locs": {HOME: [u * 1_000_000, CAP, "Stanton4_NewBabbage"]}, "sorts": {}, "budget": [], "rewards": 0}
        rows.append([t.strftime("%Y-%m-%d"), t.isoformat(), inv])
    return {"inv": rows}


class PartialFlagTests(unittest.TestCase):
    def test_dip_between_two_full_loads(self):
        self.assertEqual(sc_stats.inv_partial_flags(reads(6800, 50, 6830)), [False, True, False])

    def test_newest_dip_judged_on_the_week_before(self):
        self.assertEqual(sc_stats.inv_partial_flags(reads(6800, 6835, 50)), [False, False, True])

    def test_clear_out_that_stays_low_is_real(self):
        self.assertEqual(sc_stats.inv_partial_flags(reads(1587, 241, 242, 248)), [False, False, False, False])

    def test_week_apart_is_not_compared(self):
        self.assertEqual(sc_stats.inv_partial_flags(reads(6800, 50, step=9)), [False, False])

    def test_small_dip_is_not_partial(self):
        self.assertEqual(sc_stats.inv_partial_flags(reads(700, 500, 710)), [False, False, False])

    def test_empty_inventory_is_not_partial(self):
        self.assertEqual(sc_stats.inv_partial_flags(reads(0, 0, 0)), [False, False, False])


class SummaryTests(unittest.TestCase):
    def test_headline_skips_the_partial_load(self):
        h = sc_stats._inventory_summary(agg(6812, 6835, 50))["home"]
        self.assertEqual((h["used"], h["pct"]), (6835, 136.7))
        self.assertEqual(h["latest"]["used"], 50)
        self.assertEqual(h["partial"][:2], [1, 3])
        self.assertEqual([r[3] for r in h["series"]], [0, 0, 1])

    def test_no_partial_no_latest(self):
        h = sc_stats._inventory_summary(agg(600, 610, 620))["home"]
        self.assertIsNone(h["latest"])
        self.assertEqual(h["partial"], [0, 3, None])
        self.assertEqual(h["used"], 620)

    def test_months_ignore_partial_loads(self):
        h = sc_stats._inventory_summary(agg(6812, 50, 6835))["home"]
        self.assertEqual(h["months"], [["2026-11", 136.7]])


if __name__ == "__main__":
    unittest.main()
