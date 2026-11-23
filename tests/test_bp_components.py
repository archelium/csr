import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sc_stats  # noqa: E402


class ComponentAliasTests(unittest.TestCase):
    """The HUD names a crafted component by class, size and grade: Mil/3/A V880."""

    def test_every_class_prefix(self):
        for alias, cls in (("Mil/2/A FR-76", "Military"), ("Civ/1/C Bracer", "Civilian"),
                           ("Ind/2/B Snowpack", "Industrial"), ("Stl/1/B Veil", "Stealth"),
                           ("Sth/2/A Umbra", "Stealth"), ("Cmp/1/A ZeroRush", "Competition")):
            m = sc_stats._BP_COMP_B.match(alias)
            self.assertIsNotNone(m, alias)
            self.assertEqual(sc_stats._BP_CLASS[m.group(1).lower()], cls)

    def test_dash_form(self):
        m = sc_stats._BP_COMP_A.match('STH-2A "Umbra"')
        self.assertIsNotNone(m)
        self.assertEqual(sc_stats._BP_CLASS[m.group(1).lower()], "Stealth")


if __name__ == "__main__":
    unittest.main()
