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
    raw = json.load(open(os.path.join(sc_names.SCRIPT_DIR, "sc_ship_matrix.json"), encoding="utf-8"))
    ships = [s for s in raw["ships"] if s["name"].strip() not in ("Marauder", "Hyun")]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.json")
        json.dump({"fetched": "2026-10-16", "ships": ships + list(extra_ships)},
                  open(p, "w", encoding="utf-8"))
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
        self.assertEqual((name, man), ("Hyun", "Gatac Manufacture"))
        self.assertEqual(rec["code"], "GAMA")

    def test_crew_channel_name(self):
        self.assertEqual(index().match_display("Gatac Hyun")["name"], "Hyun")

    def test_matrix_record_wins(self):
        rec = {"name": "Marauder", "man": "Drake Interplanetary", "code": "DRAK",
               "url": "u", "size": "Medium", "type": "Combat", "focus": "Heavy Fighter",
               "img_small": "", "img_large": ""}
        got = index([rec]).display("DRAK_Marauder")[2]
        self.assertEqual(got["focus"], "Heavy Fighter")
        self.assertEqual(got["size"], "Medium")

    def test_suffixed_classes_stay_unmatched(self):
        idx = index()
        for cls in ("DRAK_Marauder_ShipATCDataManager", "GAMA_Hyun_ShipATCDataManager"):
            self.assertIsNone(idx.match(cls), cls)


if __name__ == "__main__":
    unittest.main()
