"""The 4.8 wipe: sessions split into before and after, by version, else by date."""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402


def ses(patch, day):
    return {"patch": patch, "start": datetime(2026, 1, day, 12)}


class WipeSplitTests(unittest.TestCase):
    def test_by_version(self):
        a, b = sc_stats.split_by_wipe([ses("4.7", 1), ses("4.8", 5), ses("4.10", 9), ses("4.1", 2)], "4.8")
        self.assertEqual([s["patch"] for s in a], ["4.8", "4.10"])
        self.assertEqual([s["patch"] for s in b], ["4.7", "4.1"])

    def test_unversioned_by_date(self):
        a, b = sc_stats.split_by_wipe([ses("4.7", 1), ses("scp-inventory", 3), ses("4.8", 5), ses("unknown", 7)], "4.8")
        self.assertEqual([s["patch"] for s in a], ["4.8", "unknown"])
        self.assertEqual([s["patch"] for s in b], ["4.7", "scp-inventory"])

    def test_no_play_after_the_wipe(self):
        self.assertEqual(sc_stats.split_by_wipe([ses("4.7", 1), ses("4.5", 2)], "4.8"), ([], []))

    def test_version_compare(self):
        self.assertTrue(sc_stats._ver_at_least("4.10", "4.8"))
        self.assertFalse(sc_stats._ver_at_least("4.7", "4.8"))
        self.assertIsNone(sc_stats._ver_at_least("scp-inventory", "4.8"))

    def test_no_eras_without_play_before(self):
        self.assertEqual(sc_stats.build_eras([]), {})


if __name__ == "__main__":
    unittest.main()
