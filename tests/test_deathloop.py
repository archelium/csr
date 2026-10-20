"""Deathloops: own deaths by body id, arriving dead, loops across sessions, the live alert."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

DAY = "2026-10-18"
SPAWN = "[CSessionManager::OnClientSpawned] Spawned!"
BODY = ("body_01_noMagicPocket", "869680109039")
FRONT_BODY = ("body_01_noMagicPocket", "200000000218")
STRANGER = ("body_01_noMagicPocket", "111111111111")


def stamped(hms, body, day=DAY):
    if "." not in hms:
        hms += ".000"
    return f"<{day}T{hms}Z> {body}\n"


def corpse(item_cls, item_id):
    return ("[Notice] <Adding non kept item [CSCActorCorpseUtils::PopulateItemPortForItemRecoveryEntitlement]> "
            f"Item '{item_cls}_{item_id} - Class({item_cls}) - Context(Streamable Runtime-spawned) - Socpak()', "
            "Recorded data is: Port Name 'x'")


def attach(item_cls, item_id, port):
    return (f"[Notice] <AttachmentReceived> Player[archelium] Attachment[{item_cls}_{item_id}, {item_cls}, "
            f"{item_id}] Status[persistent] Port[{port}] Elapsed[1.0] [Team_CoreGameplayFeatures][Inventory]")


def corpse_list(hms, body, gear):
    """A body's list: its body item first, then each (class, id) of gear."""
    return stamped(hms, corpse(*body)) + "".join(stamped(hms, corpse(c, i)) for c, i in gear)


GEAR = [("cds_undersuit_01_02_01", "885021851797"), ("cds_armor_heavy_core_01_01_01", "885021851798"),
        ("hdgw_rifle_ballistic_01_mag", "885021851799")]


class ScanLogDeathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        p = mock.patch.object(sc_stats, "_utc_offset_secs", return_value=0)
        p.start()
        self.addCleanup(p.stop)

    def scan(self, text, name="deaths.log"):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(stamped("11:00:00", "Log started on Sun Oct 18 11:00:00 2026") + text)
        return sc_stats.scan_log(path)

    def test_arrived_dead_rejoin(self):
        s = self.scan(corpse_list("12:04:22.009", BODY, GEAR)
                      + stamped("12:04:22.040", attach(*BODY, "Body_ItemPort"))
                      + stamped("12:04:22.137", SPAWN))
        self.assertEqual(s["own_deaths"], [["2026-10-18T12:04:22", True]])
        self.assertEqual(s["gear_lost"]["sizes"], [3])

    def test_normal_respawn_not_arrived(self):
        s = self.scan(stamped("11:30:00", attach(*BODY, "Body_ItemPort"))
                      + corpse_list("11:59:36", BODY, GEAR)
                      + stamped("11:59:54", SPAWN))
        self.assertEqual(s["own_deaths"], [["2026-10-18T11:59:36", False]])

    def test_frontend_spawn_ignored(self):
        s = self.scan(stamped("11:00:00", attach(*BODY, "Body_ItemPort"))
                      + stamped("12:03:09", attach(*FRONT_BODY, "Body_ItemPort"))
                      + stamped("12:03:09", SPAWN)
                      + corpse_list("12:03:10", BODY, GEAR))
        self.assertEqual(s["own_deaths"], [["2026-10-18T12:03:10", False]])

    def test_stranger_not_own(self):
        s = self.scan(stamped("11:30:00", attach(*BODY, "Body_ItemPort"))
                      + corpse_list("12:00:00", STRANGER, GEAR))
        self.assertEqual(s["own_deaths"], [])
        self.assertIsNone(s["gear_lost"])

    def test_two_bodies_same_second_split(self):
        other = [(f"ksar_gren_frag_01", f"7000000000{n}") for n in range(5)]
        s = self.scan(stamped("11:30:00", attach(*BODY, "Body_ItemPort"))
                      + corpse_list("12:00:00", BODY, GEAR[:2])
                      + corpse_list("12:00:00", STRANGER, other))
        self.assertEqual(len(s["own_deaths"]), 1)
        self.assertEqual(s["gear_lost"]["sizes"], [2])

    def test_no_body_attach_falls_back_to_gear(self):
        worn = "".join(stamped("11:30:00", attach(c, i, "Armor_Torso")) for c, i in GEAR)
        s = self.scan(worn + corpse_list("12:00:00", BODY, GEAR))
        self.assertEqual(s["own_deaths"], [["2026-10-18T12:00:00", False]])
        s = self.scan(corpse_list("12:00:00", BODY, GEAR), name="unworn.log")
        self.assertEqual(s["own_deaths"], [])


if __name__ == "__main__":
    unittest.main()
