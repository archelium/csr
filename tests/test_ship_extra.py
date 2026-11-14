"""Ships the RSI matrix does not list: the SHIP_EXTRA table."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_names  # noqa: E402


def index(extra_ships=()):
    """Bundled matrix, minus any Marauder/Hyun record, plus `extra_ships`."""
    with open(os.path.join(sc_names.SCRIPT_DIR, "sc_ship_matrix.json"), encoding="utf-8") as fh:
        raw = json.load(fh)
    ships = [s for s in raw["ships"] if s["name"].strip() not in ("Marauder", "Hyun", "Hyūn")]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"fetched": "2026-10-16", "ships": ships + list(extra_ships)}, fh)
        idx = sc_names.ShipIndex(cache=p)
        assert idx.load(allow_fetch=False)
    return idx


class ShipExtraTests(unittest.TestCase):
    def test_marauder(self):
        name, man, rec = index().display("DRAK_Marauder")
        self.assertEqual((name, man), ("Marauder", "Drake Interplanetary"))
        self.assertEqual(rec["focus"], "Medium Fighter")

    def test_hyun(self):
        name, man, rec = index().display("GAMA_Hyun")
        self.assertEqual((name, man), ("Hyūn", "Gatac Manufacture"))
        self.assertEqual((rec["code"], rec["type"], rec["focus"]),
                         ("GAMA", "Industrial", "Mining / Salvage"))

    def test_crew_channel_name(self):
        for n in ("Gatac Hyun", "Gatac Hyūn"):
            self.assertEqual(index().match_display(n)["name"], "Hyūn", n)

    def test_matrix_record_wins(self):
        rec = {"name": "Marauder", "man": "Drake Interplanetary", "code": "DRAK",
               "url": "u", "size": "Medium", "type": "Combat", "focus": "Heavy Fighter",
               "img_small": "", "img_large": ""}
        got = index([rec]).display("DRAK_Marauder")[2]
        self.assertEqual(got["focus"], "Heavy Fighter")
        self.assertEqual(got["size"], "Medium")

    def test_matrix_longer_name_wins(self):
        rec = {"name": "Marauder Mk I", "man": "Drake Interplanetary", "code": "DRAK",
               "url": "u", "size": "Medium", "type": "Combat", "focus": "Heavy Fighter",
               "img_small": "", "img_large": ""}
        got = index([rec]).display("DRAK_Marauder")
        self.assertEqual(got[0], "Marauder Mk I")
        self.assertEqual(got[2]["focus"], "Heavy Fighter")

    def test_suffixed_classes_stay_unmatched(self):
        idx = index()
        for cls in ("DRAK_Marauder_ShipATCDataManager", "GAMA_Hyun_ShipATCDataManager"):
            self.assertIsNone(idx.match(cls), cls)


if __name__ == "__main__":
    unittest.main()
