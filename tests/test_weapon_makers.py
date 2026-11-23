"""Gun makers: the class prefix, and the Grey's Market guns whose class has none."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_names  # noqa: E402


class WeaponMakerTests(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(sc_names.weapon_maker_code("behr_rifle_ballistic_01"), "behr")
        self.assertEqual(sc_names.weapon_maker_code("utfl_crossbow_ballistic_01"), "utfl")

    def test_greys_market_guns(self):
        for cls in ("none_rifle_multi_01", "none_rifle_multi_01_tint01", "none_lmg_ballistic_01",
                    "none_smg_energy_01", "none_pistol_ballistic_01_black01", "none_special_ballistic_01"):
            code = sc_names.weapon_maker_code(cls)
            self.assertEqual(sc_names.WEAPON_MAN[code], "Grey's Market", cls)

    def test_unknown(self):
        self.assertEqual(sc_names.weapon_maker_code("none_shotgun_ballistic_01"), "")
        self.assertEqual(sc_names.weapon_maker_code("zzzz_rifle_01"), "")

    def test_amon_and_reese(self):
        self.assertEqual(sc_names.WEAPON_MAN["amrs"], "Amon & Reese Co.")


if __name__ == "__main__":
    unittest.main()
