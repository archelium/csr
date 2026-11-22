"""Item names for guns and magazines the SC-Wiki item index doesn't list yet."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_names  # noqa: E402
import sc_stats  # noqa: E402


class FakeIndex:
    """An item index that knows only the Salvo magazine."""
    idx = {"hdgw_pistol_ballistic_01_mag": "Salvo Frag Pistol Magazine (8 cap)"}

    def resolve(self, key):
        return None


class UnlistedItemTests(unittest.TestCase):
    def test_unlisted_magazine_is_named_after_its_gun(self):
        with mock.patch.object(sc_names, "ITEMS", FakeIndex()):
            self.assertEqual(sc_stats.purchase_display("hdgw_rifle_ballistic_01_mag"), "Arlington Magazine")

    def test_listed_magazine_keeps_its_index_name(self):
        with mock.patch.object(sc_names, "ITEMS", FakeIndex()):
            self.assertEqual(sc_stats.purchase_display("hdgw_pistol_ballistic_01_mag"),
                             "Salvo Frag Pistol Magazine (8 cap)")

    def test_magazine_without_any_index(self):
        with mock.patch.object(sc_names, "ITEMS", None):
            self.assertEqual(sc_stats.purchase_display("hdgw_rifle_ballistic_01_mag"), "Arlington Magazine")

    def test_unlisted_gun_uses_the_curated_name(self):
        with mock.patch.object(sc_names, "ITEMS", FakeIndex()):
            self.assertEqual(sc_stats.purchase_display("hdgw_rifle_ballistic_01"), "Arlington")


if __name__ == "__main__":
    unittest.main()
