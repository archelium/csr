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


QUIT = ("[Notice] <SystemQuit> CSystem::Quit invoked with - cause=30016, reason=User closed the "
        "application, exitCode=0 [Team_Unknown][System]")
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

    def test_fold_collects_own_deaths(self):
        s = self.scan(stamped("11:30:00", attach(*BODY, "Body_ItemPort"))
                      + corpse_list("11:59:36", BODY, GEAR)
                      + stamped("12:02:35", QUIT))
        agg = sc_stats.blank_patch()
        sc_stats.fold(agg, s)
        self.assertEqual(agg["own_deaths"],
                         [("2026-10-18T11:59:36", False, "deaths.log", "2026-10-18T12:02:35")])

    def test_fold_no_end_unless_quit(self):
        # the live Game.log has no exit yet: "then quit 40 s later" would be made up
        s = self.scan(stamped("11:30:00", attach(*BODY, "Body_ItemPort"))
                      + corpse_list("11:59:36", BODY, GEAR)
                      + stamped("12:02:35", "[Notice] still playing"), name="Game.log")
        self.assertNotEqual(s["outcome"], "clean")
        agg = sc_stats.blank_patch()
        sc_stats.fold(agg, s)
        self.assertIsNone(agg["own_deaths"][0][3])

    def test_naked_deaths_each_count(self):
        # head, hair and mobiGlas keep their ids from life to life; only gear changes
        parts = [("PU_Protos_Head", "869680109050"), ("MobiGlas", "869680109047")]
        s = self.scan(stamped("11:30:00", attach(*BODY, "Body_ItemPort"))
                      + corpse_list("12:00:00", BODY, parts)
                      + corpse_list("12:05:00", BODY, parts)
                      + corpse_list("12:10:00", BODY, parts + GEAR)
                      + corpse_list("12:10:20", BODY, parts + GEAR))
        self.assertEqual([t for t, _a in s["own_deaths"]],
                         ["2026-10-18T12:00:00", "2026-10-18T12:05:00", "2026-10-18T12:10:00"])
        self.assertEqual(s["gear_lost"]["sizes"], [3])

    def test_no_body_attach_falls_back_to_gear(self):
        worn = "".join(stamped("11:30:00", attach(c, i, "Armor_Torso")) for c, i in GEAR)
        s = self.scan(worn + corpse_list("12:00:00", BODY, GEAR))
        self.assertEqual(s["own_deaths"], [["2026-10-18T12:00:00", False]])
        s = self.scan(corpse_list("12:00:00", BODY, GEAR), name="unworn.log")
        self.assertEqual(s["own_deaths"], [])


def at(sec, arrived=False, key="a.log", end=None):
    """A folded death `sec` seconds after 18-Oct 18:00:00 local."""
    t = sc_stats.datetime(2026, 10, 18, 18, 0, 0) + sc_stats.timedelta(seconds=sec)
    return (t.isoformat(timespec="seconds"), arrived, key, end)


class DeathloopSummaryTests(unittest.TestCase):
    summ = staticmethod(sc_stats._deathloop_summary)

    def test_rejoin_loop(self):
        v = self.summ([("2026-10-18T18:59:36", False, "a.log", "2026-10-18T19:02:35"),
                       ("2026-10-18T19:04:22", True, "b.log", "2026-10-18T19:04:54")])
        self.assertEqual((v["n"], v["arrived"], v["sessions"], v["quick_n"]), (1, 1, 2, 0))
        lp = v["recent"][0]
        self.assertEqual((lp["start"], lp["at"]), ("2026-10-18T18:59:36", "2026-10-18T19:04:22"))
        self.assertEqual((lp["n"], lp["arrived"], lp["rejoins"], lp["gaps"], lp["after_s"]),
                         (2, 1, 1, [286], 32))

    def test_chain_breaks_past_600(self):
        v = self.summ([at(0, key="a.log"), at(601, True, key="b.log")])
        lp = v["recent"][0]
        self.assertEqual((lp["n"], lp["rejoins"], lp["gaps"]), (1, 0, []))

    def test_two_arrivals_one_loop(self):
        v = self.summ([at(0), at(300, True), at(500, True)])
        self.assertEqual(v["n"], 1)
        self.assertEqual((v["recent"][0]["n"], v["recent"][0]["arrived"]), (3, 2))

    def test_quick_run(self):
        v = self.summ([at(0), at(55), at(110)])
        self.assertEqual((v["n"], v["quick_n"]), (0, 1))
        self.assertEqual(v["quick"][0], {"start": at(0)[0], "at": at(110)[0], "n": 3})
        self.assertIsNone(self.summ([at(0), at(55)]))
        self.assertIsNone(self.summ([at(0), at(55), at(116)]))

    def test_quick_merge(self):
        first = [at(0), at(55), at(110)]
        between = [at(400), at(500)]
        second = [at(110 + 590 + k * 55) for k in range(3)]
        v = self.summ(first + between + second)
        self.assertEqual(v["quick_n"], 1)
        self.assertEqual(v["quick"][0]["n"], 8)
        apart = [at(110 + 610 + k * 55) for k in range(3)]
        self.assertEqual(self.summ(first + apart)["quick_n"], 2)
        other = [at(110 + 590 + k * 55, key="b.log") for k in range(3)]
        self.assertEqual(self.summ(first + other)["quick_n"], 2)

    def test_quick_inside_loop_excluded(self):
        v = self.summ([at(0), at(50), at(100, True)])
        self.assertEqual((v["n"], v["quick_n"]), (1, 0))

    def test_loop_without_session_end(self):
        v = self.summ([at(0, key="a.log"), at(286, True, key="b.log", end=None)])
        self.assertIsNone(v["recent"][0]["after_s"])

    def test_lone_deaths_give_none(self):
        self.assertIsNone(self.summ([at(0), at(3600)]))
        self.assertIsNone(self.summ([]))

    def test_newest_first_capped_at_8(self):
        v = self.summ([at(k * 3600, True) for k in range(10)])
        self.assertEqual(v["n"], 10)
        self.assertEqual(len(v["recent"]), 8)
        self.assertEqual(v["recent"][0]["at"], at(9 * 3600)[0])


class WatcherDeathTests(unittest.TestCase):
    def setUp(self):
        sc_stats._WATCH["dead"].clear()
        sc_stats._WATCH["events"].clear()

    def loops(self):
        return [e for e in sc_stats._WATCH["events"] if e["kind"] == "loop"]

    def test_test_chunk_loop_raises(self):
        self.assertEqual(sc_stats.fire_test_event("loop"), 1)
        self.assertEqual(self.loops()[0]["title"].split(" — ")[0], "Arrived dead")

    def test_benign_still_silent(self):
        self.assertEqual(sc_stats.fire_test_event("benign"), 0)

    def test_split_across_chunks(self):
        sc_stats._scan_chunk(corpse_list("12:04:22.009", BODY, GEAR), "LIVE")
        self.assertEqual(self.loops(), [])
        sc_stats._scan_chunk(stamped("12:04:22.040", attach(*BODY, "Body_ItemPort"))
                             + stamped("12:04:22.137", SPAWN), "LIVE")
        self.assertEqual(len(self.loops()), 1)
        self.assertEqual(self.loops()[0]["title"], "Arrived dead — LIVE")

    def test_each_arrival_alerts_once(self):
        def arrive(hms):
            sc_stats._scan_chunk(stamped(hms, attach(*BODY, "Body_ItemPort"))
                                 + corpse_list(hms, BODY, GEAR) + stamped(hms, SPAWN), "LIVE")
        arrive("12:00:00")
        sc_stats._scan_chunk(stamped("12:00:40", "[Notice] something else"), "LIVE")
        self.assertEqual(len(self.loops()), 1)
        for e in sc_stats._WATCH["events"]:
            e["ts"] -= 120             # past the 60 s de-dup, as five real minutes would be
        arrive("12:05:00")
        got = self.loops()
        self.assertEqual(len(got), 2)
        self.assertNotIn("deaths in the last", got[0]["detail"])
        self.assertIn("2 deaths in the last 5 min", got[1]["detail"])

    def test_stranger_after_yours_live(self):
        # arriving into a busy instance: another body's list lands right after yours
        sc_stats._scan_chunk(corpse_list("12:04:22.009", BODY, GEAR)
                             + corpse_list("12:04:22.012", STRANGER, GEAR)
                             + stamped("12:04:22.040", attach(*BODY, "Body_ItemPort"))
                             + stamped("12:04:22.137", SPAWN), "LIVE")
        self.assertEqual(len(self.loops()), 1)

    def test_read_split_mid_line(self):
        # watch_tick hands over whatever bytes were appended, which can end mid-line
        text = (corpse_list("12:04:22.009", BODY, GEAR)
                + stamped("12:04:22.040", attach(*BODY, "Body_ItemPort"))
                + stamped("12:04:22.137", SPAWN)).encode()
        cut = text.index(b"Spawned!") + 3
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "Game.log")
            with mock.patch.dict(sc_stats._CHANNEL_DIRS, {"LIVE": d}, clear=True),                     mock.patch.object(sc_stats, "crashes_dir", return_value=None):
                with open(path, "wb") as fh:
                    fh.write(text[:cut])
                sc_stats._WATCH["tails"][path] = 0
                sc_stats.watch_tick()
                with open(path, "ab") as fh:
                    fh.write(text[cut:])
                sc_stats.watch_tick()
                sc_stats._WATCH["tails"].pop(path, None)
        self.assertEqual(len(self.loops()), 1)

    def test_no_body_known_no_alert(self):
        sc_stats._scan_chunk(corpse_list("12:04:22.009", BODY, GEAR)
                             + stamped("12:04:22.100", SPAWN), "LIVE")
        sc_stats._scan_chunk(stamped("12:05:00", "[Notice] something else"), "LIVE")
        self.assertEqual(self.loops(), [])


if __name__ == "__main__":
    unittest.main()
