"""Ship data for the Kestrel slate: one ship at a time, from the generated sc_slate_ships module."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_slate_ships  # noqa: E402


class ShipDataTests(unittest.TestCase):
    def test_perseus_turrets(self):
        p = sc_slate_ships.ship_payload("RSI_Perseus")
        top = next(t for t in p["turrets"] if t[0] == "hardpoint_turret_manned_top")
        self.assertEqual([g[0] for g in top[6]].count("RSI Medusa Cannon"), 2)
        self.assertEqual(len(top[6]), 4)
        rt = next(t for t in p["turrets"] if t[0] == "hardpoint_turret_remote_top")
        self.assertEqual(rt[7], "both")

    def test_speeds_and_shape(self):
        p = sc_slate_ships.ship_payload("aegs_gladius")
        self.assertEqual(len(p["speeds"]), 3)
        self.assertGreater(len(p["shape"][2]), 10)

    def test_apollo_tier_speeds(self):
        self.assertEqual(sc_slate_ships.ship_payload("RSI_Apollo_Medivac_Tier_2")["speeds"],
                         sc_slate_ships.ship_payload("RSI_Apollo_Medivac")["speeds"])

    def test_unknown(self):
        self.assertIsNone(sc_slate_ships.ship_payload("NOPE_Ship"))
        self.assertIsNone(sc_slate_ships.ship_payload(""))
        self.assertIsNone(sc_slate_ships.ship_payload(None))

    def test_payload_keys(self):
        p = sc_slate_ships.ship_payload("RSI_Perseus")
        for k in ("class", "name", "short", "seats", "turrets", "pilotGuns", "pilotTurrets",
                  "shape", "dims", "speeds", "sys", "missiles"):
            self.assertIn(k, p)

    def test_module_size(self):
        self.assertLess(len(sc_slate_ships.SHIPS_JSON), 1_200_000)


if __name__ == "__main__":
    unittest.main()
