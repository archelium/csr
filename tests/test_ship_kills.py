"""Ship kills: <Vehicle Destroyed> lines (4.10.3+), one event per vehicle."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

DAY = "2026-11-13"
ME = "archelium"
MY_ID = "204821712526"


def line(hms, body):
    return f"<{DAY}T{hms}Z> {body}\n"


LOG_START = line("10:00:00.000", "Log started")
LOGIN = (line("10:00:01.000", f"[Notice] <Legacy login response> Handle[{ME}] login ok")
         + line("10:00:02.000", f"[Notice] <Inventory Mgmt Request Queued> Queued Request[1] for '{ME}' [{MY_ID}] Source"))


def vd(hms, vid, cls, frm, to, killer=ME, kid=MY_ID, owner="0", reason="Combat"):
    return line(hms, f"[Notice] <Vehicle Destroyed> Vehicle '{cls}_{vid}' [{vid}] class '{cls}' "
                     f"(owner player [{owner}], driver [0]) destroyed: reason [{reason}] "
                     f"level [{frm}] -> [{to}] flags [4066] killer '{killer}' [{kid}] "
                     f"authority [0] [Team_CGP4][Vehicle]")


def scan(text, login=LOGIN):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "Game.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(LOG_START + login + text + line("11:00:00.000", "end"))
        return sc_stats.scan_log(p)


def summary(*sessions):
    agg = sc_stats.blank_patch()
    for s in sessions:
        sc_stats.fold(agg, s)
    return sc_stats._ship_kills_summary(agg)


GLAD = "AEGS_Gladius_PU_AI_CRIM"


class ScanLogTests(unittest.TestCase):
    def test_kill_by_you(self):
        sk = scan(vd("10:10:00.000", "1", GLAD, 0, 2))["ship_kills"]
        self.assertEqual(sk["kills"], {GLAD: 1})
        self.assertEqual((sk["crippled"], sk["player_ships"], sk["lost"]), ({}, 0, []))
        self.assertEqual(sk["by_reason"], {"Combat": 1})

    def test_by_reason_counts_kills_only(self):
        text = (vd("10:10:00.000", "1", GLAD, 0, 1)
                + vd("10:11:00.000", "2", "MISC_Fury", 0, 2, killer="foe", kid="42", owner=MY_ID, reason="Collision"))
        self.assertEqual(scan(text)["ship_kills"]["by_reason"], {})

    def test_collision_and_self_destruct_kills_by_you_count(self):
        text = (vd("10:10:00.000", "1", GLAD, 0, 2, reason="Collision")
                + vd("10:11:00.000", "2", GLAD, 0, 2, reason="SelfDestruct"))
        sk = scan(text)["ship_kills"]
        self.assertEqual(sk["kills"], {GLAD: 2})
        self.assertEqual(sk["by_reason"], {"Collision": 1, "SelfDestruct": 1})

    def test_crippled_only(self):
        sk = scan(vd("10:10:00.000", "1", GLAD, 0, 1))["ship_kills"]
        self.assertEqual((sk["kills"], sk["crippled"]), ({}, {GLAD: 1}))

    def test_two_lines_one_vehicle_count_once(self):
        sk = scan(vd("10:10:00.000", "1", GLAD, 0, 1) + vd("10:10:30.000", "1", GLAD, 1, 2))["ship_kills"]
        self.assertEqual((sk["kills"], sk["crippled"]), ({GLAD: 1}, {}))

    def test_kill_by_another_player_not_counted(self):
        text = vd("10:10:00.000", "1", GLAD, 0, 2, killer="somebody", kid="999")
        self.assertIsNone(scan(text)["ship_kills"])

    def test_player_owned_victim(self):
        sk = scan(vd("10:10:00.000", "1", "RSI_Aurora_Mk2", 0, 2, owner="555"))["ship_kills"]
        self.assertEqual((sk["kills"], sk["player_ships"]), ({"RSI_Aurora_Mk2": 1}, 1))

    def test_own_ship_lost(self):
        text = vd("10:10:00.000", "1", "MISC_Fury", 0, 2, killer="foe", kid="42", owner=MY_ID, reason="Collision")
        sk = scan(text)["ship_kills"]
        self.assertEqual(sk["kills"], {})
        self.assertEqual(sk["lost"], [{"cls": "MISC_Fury", "reason": "Collision", "killer": "foe"}])

    def test_no_vehicle_lines(self):
        self.assertIsNone(scan("")["ship_kills"])


class SummaryTests(unittest.TestCase):
    def test_suffixes_resolve_to_the_base_hull(self):
        sc_stats.SHIPS.load(allow_fetch=False)
        for cls, name in (("AEGS_Gladius_PU_AI_CRIM", "Gladius"),
                          ("AEGS_Gladius_PU_AI_CRIM_IK", "Gladius"),
                          ("AEGS_Gladius_AI_Super_CRIM", "Gladius"),
                          ("AEGS_Vanguard_Harbinger_PU_AI_CRIM", "Vanguard Harbinger"),
                          ("MISC_Freelancer_MIS_PU_AI_CRIM", "Freelancer MIS"),
                          ("MISC_Freelancer_MIS_AI_Super_Crim", "Freelancer MIS"),
                          ("AEGS_Sabre_Firebird_PU_AI_CRIM", "Sabre Firebird")):
            self.assertEqual(sc_stats.vehicle_ship_name(cls), name, cls)

    def test_real_4103_victim_classes(self):
        sc_stats.SHIPS.load(allow_fetch=False)
        for cls, name in (
        ("AEGS_Gladius_AI_Super_CRIM", "Gladius"),
        ("AEGS_Gladius_PU_AI_CRIM", "Gladius"),
        ("AEGS_Gladius_PU_AI_CRIM_IK", "Gladius"),
        ("AEGS_Sabre_Firebird_PU_AI_CRIM", "Sabre Firebird"),
        ("AEGS_Vanguard_Harbinger_PU_AI_CRIM", "Vanguard Harbinger"),
        ("ANVL_Arrow", "Arrow"),
        ("CRUS_Intrepid", "Intrepid"),
        ("CRUS_Spirit_C1", "C1 Spirit"),
        ("CRUS_Starlifter_C2", "C2 Hercules"),
        ("DRAK_Corsair", "Corsair"),
        ("DRAK_Herald", "Herald"),
        ("GLSN_Basher", "Basher"),
        ("GLSN_Shiv", "Shiv"),
        ("GRIN_MTC", "MTC"),
        ("MISC_Freelancer_MIS_AI_Super_Crim", "Freelancer MIS"),
        ("MISC_Freelancer_MIS_PU_AI_CRIM", "Freelancer MIS"),
        ("MISC_Fury", "Fury"),
        ("MRAI_Guardian_MX", "Guardian MX"),
        ("MRAI_Guardian_MX_Collector_Military", "Guardian MX"),
        ("MRAI_Pulse", "Pulse"),
        ("ORIG_m80", "M80"),
        ("RSI_Aurora_Mk2", "Aurora Mk II"),
        ("RSI_Constellation_Mk5_Centaurus", "Constellation Mk V Centaurus"),
        ("RSI_Galaxy", "Galaxy"),
        ("RSI_Perseus", "Perseus"),
        ("RSI_Skylark", "Skylark"),
        ("RSI_Zeus_ES", "Zeus Mk II ES"),
        ):
            self.assertEqual(sc_stats.vehicle_ship_name(cls), name, cls)

    def test_old_session_folds_cleanly(self):
        s = scan(vd("10:10:00.000", "1", GLAD, 0, 2) + vd("10:11:00.000", "2", GLAD, 0, 1))
        old = dict(s)
        del old["ship_kills"]
        agg = sc_stats.blank_patch()
        sc_stats.fold(agg, old)
        self.assertEqual(sc_stats._ship_kills_summary(agg), {"empty": True})
        sm = summary(old, s)
        self.assertEqual((sm["kills"], sm["crippled"], sm["sessions"]), (1, 1, 1))
        self.assertEqual(sm["top"], [["Gladius", 1]])

    def test_hours_cover_every_session_on_a_build_that_logs_the_line(self):
        def sess(patch, build, text=""):
            s = dict(scan(text), patch=patch, build=build, dur=7200)
            return s
        kill = sess("4.10", "4.10.200.58746", vd("10:10:00.000", "1", GLAD, 0, 2))
        empty = sess("4.10", "4.10.200.58746")
        old = sess("4.2", "4.2.152.45836")
        sm = summary(kill, empty, old)
        self.assertEqual((sm["kills"], sm["hours"], sm["sessions"]), (1, 4.0, 1))
        self.assertEqual(sm["per10h"], 2.5)
        self.assertTrue(sc_stats.logs_vehicle_kills("4.10", "4.10.200.1"))
        self.assertFalse(sc_stats.logs_vehicle_kills("4.10", "4.10.100.1"))
        self.assertFalse(sc_stats.logs_vehicle_kills("4.9", "4.9.1.1"))
        self.assertTrue(sc_stats.logs_vehicle_kills("4.11", ""))
        self.assertTrue(sc_stats.logs_vehicle_kills(None, None))
        self.assertTrue(sc_stats.logs_vehicle_kills("", ""))
        self.assertFalse(sc_stats.logs_vehicle_kills("scp-crafting", "1.0.172.14271"))
        self.assertTrue(sc_stats.logs_vehicle_kills("scp-crafting", "4.10.200.58746"))

    def test_lost_is_dated_newest_first_and_capped(self):
        sc_stats.SHIPS.load(allow_fetch=False)
        sess = []
        for day in range(1, 13):
            s = scan(vd("10:10:00.000", str(day), "MISC_Fury", 0, 2, killer="foe", kid="42", owner=MY_ID))
            sess.append(dict(s, start=__import__("datetime").datetime(2026, 11, day, 10)))
        sm = summary(*sess)
        self.assertEqual((len(sm["lost"]), sm["lost_n"]), (10, 12))
        self.assertEqual([r["date"] for r in sm["lost"]][:2], ["2026-11-12", "2026-11-11"])

    def test_sessions_count_kills_only(self):
        sc_stats.SHIPS.load(allow_fetch=False)
        kill = scan(vd("10:10:00.000", "1", GLAD, 0, 2))
        crip = scan(vd("10:10:00.000", "2", GLAD, 0, 1))
        self.assertEqual(summary(kill, crip)["sessions"], 1)

    def test_lost_carries_names(self):
        sc_stats.SHIPS.load(allow_fetch=False)
        s = scan(vd("10:10:00.000", "1", "MISC_Fury", 0, 2, killer="foe", kid="42", owner=MY_ID, reason="Combat"))
        sm = summary(s)
        self.assertEqual(sm["lost"], [{"ship": "Fury", "reason": "Combat", "killer": "foe", "date": "2026-11-13"}])


if __name__ == "__main__":
    unittest.main()
