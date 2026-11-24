"""xparrow-lite icons: picked by class from the cached lists, only where CSR has none."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_names  # noqa: E402


class XparrowTests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "sc_xparrow.json")
        json.dump({"fetched": "2026-11-24", "s": ["cool_aegs_s03_mercury_scitem"], "s_v": "1",
                   "f": ["behr_rifle_ballistic_01_mr01"], "f_v": "2",
                   "t": ["mrai_flightsuit_01_01_01", "cool_aegs_s03_mercury_scitem"]}, open(self.path, "w"))
        self.x = sc_names.XparrowIcons(self.path)
        self.assertTrue(self.x.load())

    def test_art_by_class_any_case(self):
        self.assertEqual(self.x.art("COOL_AEGS_S03_Mercury_SCItem"), ["x", "cool_aegs_s03_mercury_scitem", "s"])
        self.assertEqual(self.x.art("behr_rifle_ballistic_01_mr01"), ["x", "behr_rifle_ballistic_01_mr01", "f"])

    def test_armour_thumbnail(self):
        self.assertEqual(self.x.art("mrai_flightsuit_01_01_01"), ["x", "mrai_flightsuit_01_01_01", "t"])

    def test_icon_wins_over_thumbnail(self):
        self.assertEqual(self.x.art("cool_aegs_s03_mercury_scitem")[2], "s")

    def test_unknown_class(self):
        self.assertIsNone(self.x.art("fuel_nozzle_grin_nozzlefast"))
        self.assertIsNone(self.x.art(None))

    def test_payload(self):
        self.assertEqual(self.x.payload(), {"base": "https://xparrow-lite.pages.dev/", "v": {"s": "1", "f": "2"}})

    def test_empty_cache_has_no_payload(self):
        x = sc_names.XparrowIcons(os.path.join(tempfile.mkdtemp(), "none.json"))
        x._apply({})
        self.assertIsNone(x.payload())


if __name__ == "__main__":
    unittest.main()
