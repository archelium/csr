"""Live Game.log state for the Kestrel slate: places, notices, contracts, kills, injuries, loadout."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_livelog  # noqa: E402
import sc_stats  # noqa: E402

TS = "<2026-11-24T20:00:00.000Z> "


def notif(text, n=1):
    return TS + f'[Notice] <SHUDEvent_OnNotification> Added notification "{text}" [{n}] to queue.'


def kill(cls="AEGS_Gladius_123456", owner="0", level_to="2", reason="Combat", killer="Pilot_X"):
    return (TS + f"[Notice] <Vehicle Destroyed> Vehicle '{cls}' [987654321] class '{cls.rsplit('_', 1)[0]}' "
            f"(owner player [{owner}], driver [0]) destroyed: reason [{reason}] level [1] -> [{level_to}] "
            f"at pos x y z killer '{killer}' [55]")


class PatternTests(unittest.TestCase):
    def test_patterns_match_sc_stats(self):
        for name in ("NOTIF_RE", "CONTRACT_NOTIF_RE", "JURIS_RE", "INJURY_RE", "VKILL_RE",
                     "BLUEPRINT_RE", "PLACE_RE", "HAUL_RE", "ATTACH_RE", "ATTACH_PORT_RE", "MISSION_RE",
                     "NOTIF_MISSION_RE"):
            self.assertEqual(getattr(sc_livelog, name).pattern, getattr(sc_stats, name).pattern, name)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.s = sc_livelog.LogState()

    def test_empty_state_keys(self):
        st = self.s.state()
        self.assertEqual(set(st), {"place", "jurisdiction", "armistice", "notices", "contracts", "kills",
                                   "injuries", "loadout", "blueprints", "seq"})
        self.assertEqual(st["notices"], [])
        self.assertEqual(st["blueprints"], 0)

    def test_contract_lifecycle(self):
        self.s.feed(notif("Contract Accepted: Bounty: Test Target"), 1)
        self.s.feed(notif("New Objective: Neutralize Target"), 2)
        c = self.s.state()["contracts"][0]
        self.assertEqual({k: c[k] for k in ("name", "status", "objective", "t")},
                         {"name": "Bounty: Test Target", "status": "active", "objective": "Neutralize Target", "t": 1})
        self.s.feed(notif("Contract Complete: Bounty: Test Target"), 3)
        self.assertEqual(self.s.state()["contracts"][0]["status"], "complete")
        self.s.feed(notif("Contract Failed: Other Job"), 4)
        self.assertEqual(self.s.state()["contracts"][1]["status"], "failed")

    def _mission_notif(self, text, mid, n=1, obj=""):
        return (TS + f'[Notice] <SHUDEvent_OnNotification> Added notification "{text}: " [{n}] to queue. '
                f'New queue size: 1, MissionId: [{mid}], ObjectiveId: [{obj}] [Team_CoreGameplayFeatures]')

    def _end(self, mid, kind):
        return (TS + f"[Notice] <EndMission> Ending mission for player. MissionId[{mid}] Player[p] PlayerId[1] "
                f"CompletionType[{kind}] Reason[Player left] [Team_MissionFeatures][Missions]")

    def test_abandoned_contract_closes_by_mission_id(self):
        a, b = "289f342b-73d1-4dcf-b515-d1b2e28a65d0", "0af0e08c-8a4e-4151-b154-9a745093a687"
        self.s.feed(self._mission_notif("Contract Accepted:  Combat Gauntlet - Scenario #6", b), 1)
        self.s.feed(self._mission_notif("Contract Accepted:  Combat Gauntlet - Scenario #4", a), 2)
        self.s.feed(self._mission_notif("New Objective: Go to a Hurston Weapons Testing Site above Aberdeen", b,
                                        obj="a9fc"), 3)
        self.s.feed(self._end(b, "Complete"), 4)
        self.s.feed(self._end(a, "Abandon"), 5)
        cs = {c["name"]: c for c in self.s.state()["contracts"]}
        self.assertEqual(cs["Combat Gauntlet - Scenario #6"]["status"], "complete")
        self.assertEqual(cs["Combat Gauntlet - Scenario #6"]["objective"],
                         "Go to a Hurston Weapons Testing Site above Aberdeen")
        self.assertEqual(cs["Combat Gauntlet - Scenario #4"]["status"], "abandoned")
        self.assertIsNone(cs["Combat Gauntlet - Scenario #4"]["objective"])

    def test_end_mission_kinds(self):
        for i, (kind, want) in enumerate((("Fail", "failed"), ("Failed", "failed"), ("Abandon", "abandoned"),
                                          ("Expired", "ended"))):
            mid = f"00000000-0000-0000-0000-00000000000{i}"
            self.s.feed(self._mission_notif(f"Contract Accepted: Job {i}", mid), 1)
            self.s.feed(self._end(mid, kind), 2)
            self.assertEqual(self.s.state()["contracts"][-1]["status"], want, kind)

    def test_markup_cleaned(self):
        self.s.feed(notif("Contract Accepted: <EM4>Help Protect Site [500 Rep] [BP]*</EM4>: "), 1)
        self.assertEqual(self.s.state()["contracts"][0]["name"], "Help Protect Site")

    def test_armistice_and_jurisdiction(self):
        self.s.feed(notif("Entering Armistice Zone - Combat Prohibited"), 1)
        self.assertTrue(self.s.state()["armistice"])
        self.s.feed(notif("Leaving Armistice Zone - Combat Allowed"), 2)
        self.assertFalse(self.s.state()["armistice"])
        self.s.feed(notif("Entered Crusader Industries Jurisdiction"), 3)
        self.assertEqual(self.s.state()["jurisdiction"], "Crusader Industries")

    def test_place_uses_name_function(self):
        s = sc_livelog.LogState(place_name=lambda code: "Port Name" if code == "Stanton_X" else None)
        s.feed(TS + "<RequestLocationInventory> Player[pilot] requested inventory for Location[Stanton_X]", 1)
        self.assertEqual(s.state()["place"], "Port Name")
        s.feed(TS + "<RequestLocationInventory> Player[pilot] requested inventory for Location[INVALID]", 2)
        self.assertEqual(s.state()["place"], "Port Name")

    def test_injury(self):
        self.s.feed(notif("Moderate Injury Detected - Left Leg - Tier 2 Injury"), 1)
        self.s.feed(notif("Minor Injury Detected - Right Arm - Tier 3 Injury"), 2)
        self.assertEqual(self.s.state()["injuries"], {"ll": "mod", "ra": "min"})

    def test_kill(self):
        self.s.feed(kill(), 5)
        k = self.s.state()["kills"][-1]
        self.assertEqual((k["cls"], k["level"], k["reason"], k["killer"], k["t"]),
                         ("AEGS_Gladius", 2, "Combat", "Pilot_X", 5))

    def test_kills_capped(self):
        for i in range(40):
            self.s.feed(kill(), i)
        self.assertEqual(len(self.s.state()["kills"]), 30)

    def test_attachment_loadout(self):
        self.s.feed(TS + "<AttachmentReceived> Player[pilot] Attachment[a, qrt_combat_medium_core_01, 123] "
                    "Status[persistent] Port[Armor_Torso]", 1)
        self.assertEqual(self.s.state()["loadout"], {"Armor_Torso": "qrt_combat_medium_core_01"})

    def _att(self, cls, iid, port):
        self.s.feed(TS + f"<AttachmentReceived> Player[pilot] Attachment[{cls}_{iid}, {cls}, {iid}] "
                    f"Status[persistent] Port[{port}] Elapsed[0.1]", 1)

    def test_item_moves_between_ports(self):
        self._att("none_rifle_multi_01", "939", "wep_stocked_3")
        self._att("none_rifle_multi_01", "939", "weapon_attach_hand_right")
        lo = self.s.state()["loadout"]
        self.assertEqual(lo.get("weapon_attach_hand_right"), "none_rifle_multi_01")
        self.assertNotIn("wep_stocked_3", lo)
        self._att("none_rifle_multi_01", "939", "wep_stocked_3")
        lo = self.s.state()["loadout"]
        self.assertEqual(lo.get("wep_stocked_3"), "none_rifle_multi_01")
        self.assertNotIn("weapon_attach_hand_right", lo)

    def test_helmet_from_hand_to_hook(self):
        self._att("vgl_flightsuit_helmet_01", "371", "weapon_attach_hand_left")
        self._att("vgl_flightsuit_helmet_01", "371", "helmethook_attach")
        lo = self.s.state()["loadout"]
        self.assertNotIn("weapon_attach_hand_left", lo)
        self.assertEqual(lo.get("helmethook_attach"), "vgl_flightsuit_helmet_01")

    def test_new_item_in_hand_replaces_old(self):
        self._att("Drink_bottle_cruz_01", "947", "weapon_attach_hand_right")
        self._att("none_rifle_multi_01", "939", "weapon_attach_hand_right")
        self.assertEqual(self.s.state()["loadout"]["weapon_attach_hand_right"], "none_rifle_multi_01")
        self._att("none_rifle_multi_01", "939", "wep_stocked_3")
        self.assertNotIn("weapon_attach_hand_right", self.s.state()["loadout"])

    def test_blueprint(self):
        self.s.feed(TS + 'Added notification "Received Blueprint: P4-AR Rifle: " [7]', 1)
        self.assertEqual(self.s.state()["blueprints"], 1)

    def test_blueprint_lines_of_one_receipt_count_once(self):
        name = 'Atzkav "Mirage" Sniper Rifle'
        for form in (f'Added notification "Received Blueprint: {name}: " [9] to queue.',
                     f'<UpdateNotificationItem> Notification "Received Blueprint: {name}: " [9], Action: Next',
                     f'<UpdateNotificationItem> Notification "Received Blueprint: {name}: " [9], Action: Remove'):
            self.s.feed(TS + form, 1)
        self.s.feed(TS + 'Added notification "Received Blueprint: P4-AR Rifle: " [10] to queue.', 1)
        self.assertEqual(self.s.state()["blueprints"], 2)
        self.s.reset()
        self.s.feed(TS + f'Added notification "Received Blueprint: {name}: " [9] to queue.', 1)
        self.assertEqual(self.s.state()["blueprints"], 1)

    def test_notice_seq_keeps_counting_past_the_cap(self):
        self.assertEqual(self.s.state()["seq"], 0)
        for i in range(45):
            self.s.feed(notif(f"Welcome {i}"), 2)
        st = self.s.state()
        self.assertEqual(len(st["notices"]), 40)
        self.assertEqual(st["seq"], 45)
        self.s.reset()
        self.assertEqual(self.s.state()["seq"], 0)

    def test_notice_groups_and_cap(self):
        texts = {"Moderate Injury Detected - Left Leg - Tier 2": "alert", "Contract Accepted: X": "msn",
                 "Awarded 1,500 aUEC": "money", "Party invite from Someone": "people",
                 "Hangar Request Completed": "ship", "Entering Armistice Zone": "zone", "Welcome": "tip"}
        for t, g in texts.items():
            self.s.feed(notif(t), 1)
            self.assertEqual(self.s.state()["notices"][-1]["group"], g, t)
        for i in range(50):
            self.s.feed(notif(f"Welcome {i}"), 2)
        n = self.s.state()["notices"]
        self.assertEqual(len(n), 40)
        self.assertEqual(n[-1]["text"], "Welcome 49")

    def test_line_time_used(self):
        s = sc_livelog.LogState()
        s.feed(notif("Welcome"), None)
        self.assertAlmostEqual(s.state()["notices"][0]["t"], 1795550400.0, delta=1)


class TailTests(unittest.TestCase):
    def test_tail_reads_appends_and_resets_on_truncate(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "Game.log")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(notif("Contract Accepted: Old Job") + "\n")
            tail = sc_livelog.LiveTail(lambda: p)
            tail.poll()
            self.assertEqual(tail.state()["contracts"][0]["name"], "Old Job")
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(notif("Contract Accepted: Second") + "\n" + notif("partial line no newline"))
            tail.poll()
            self.assertEqual([c["name"] for c in tail.state()["contracts"]], ["Old Job", "Second"])
            self.assertEqual(len(tail.state()["notices"]), 2)      # partial line waits for its newline
            with open(p, "w", encoding="utf-8") as fh:              # new session: file truncated
                fh.write(notif("Contract Accepted: New Job") + "\n")
            tail.poll()
            self.assertEqual([c["name"] for c in tail.state()["contracts"]], ["New Job"])

    def test_tail_new_path_resets(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "a.log"), os.path.join(td, "b.log")
            for path, name in ((a, "A"), (b, "B")):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(notif(f"Contract Accepted: {name}") + "\n")
            cur = [a]
            tail = sc_livelog.LiveTail(lambda: cur[0])
            tail.poll()
            cur[0] = b
            tail.poll()
            self.assertEqual([c["name"] for c in tail.state()["contracts"]], ["B"])

    def test_tail_missing_file(self):
        tail = sc_livelog.LiveTail(lambda: None)
        tail.poll()
        self.assertEqual(tail.state()["contracts"], [])


if __name__ == "__main__":
    unittest.main()
