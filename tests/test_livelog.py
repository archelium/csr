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
                     "NOTIF_MISSION_RE", "ACTOR_KILL_RE", "LOGIN_RE"):
            self.assertEqual(getattr(sc_livelog, name).pattern, getattr(sc_stats, name).pattern, name)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.s = sc_livelog.LogState()

    def test_empty_state_keys(self):
        st = self.s.state()
        self.assertEqual(set(st), {"place", "jurisdiction", "armistice", "notices", "contracts", "kills",
                                   "injuries", "loadout", "blueprints", "seq", "asks", "party", "qt", "places",
                                   "me", "deaths"})
        self.assertEqual(st["party"], {"members": [], "leader": None, "near": 0})
        self.assertEqual(st["qt"], {"target": None, "fuel": None, "t": None})
        self.assertIsNone(st["me"])
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
        self.assertEqual((k["cls"], k["level"], k["reason"], k["killer"], k["t"], k["by_me"]),
                         ("AEGS_Gladius", 2, "Combat", "Pilot_X", 5, False))

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


ZERO = "00000000-0000-0000-0000-000000000000"
QUEUE = " to queue. New queue size: 1, MissionId: [{mid}], ObjectiveId: [{obj}] [Team_CoreGameplayFeatures][Missions][Comms]"


def ml(*parts, n=1, mid=ZERO, obj=""):
    """A notice as the game writes it over several lines, each with its own timestamp."""
    lines = [TS + '[Notice] <SHUDEvent_OnNotification> Added notification "' + parts[0]]
    lines += [TS + p for p in parts[1:-1]]
    lines.append(TS + parts[-1] + f': " [{n}]' + QUEUE.format(mid=mid, obj=obj))
    return lines


def one(text, n=1, mid=ZERO, obj=""):
    return (TS + f'[Notice] <SHUDEvent_OnNotification> Added notification "{text}: " [{n}]'
            + QUEUE.format(mid=mid, obj=obj))


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.s = sc_livelog.LogState()

    def feed(self, lines, t=1):
        for line in ([lines] if isinstance(lines, str) else lines):
            self.s.feed(line, t)

    def login(self, name="Pilot_Me"):
        self.feed(TS + f"[Notice] <Legacy login response> [CIG-net] User Login Success - Handle[{name}] - "
                  "Time[123] [Team_GameServices][Login]")

    def test_multiline_notice(self):
        lines = ml("New Member Joined", "Pilot_A has joined the party.", n=5)
        self.feed(lines[:1])
        self.feed(TS + "[Notice] <CSCLoadingPlatformManager::TransitionLightGroupState> lights [Team_X]")
        self.feed(lines[1:])
        self.feed(TS + '   "New Member Joined')                      # queue dump, not a new notice
        self.feed(TS + 'Pilot_A has joined the party.: " [5]')
        st = self.s.state()
        self.assertEqual([n["text"] for n in st["notices"]], ["New Member Joined: Pilot_A has joined the party."])
        self.assertEqual(st["party"]["members"], ["Pilot_A"])

    def test_me_from_attachment_and_login(self):
        self.feed(TS + "<AttachmentReceived> Player[Pilot_Me] Attachment[a_1, a, 1] Status[persistent] "
                  "Port[Armor_Torso]")
        self.assertEqual(self.s.state()["me"], "Pilot_Me")
        self.s.reset()
        self.login("Pilot_B")
        self.assertEqual(self.s.state()["me"], "Pilot_B")

    def test_asks_open_and_close(self):
        self.login()
        self.feed(ml("Pilot_A", "Party Invite Received: Accept Invitation?", n=1), 10)
        self.feed(ml("Pilot_B", "Friend Request: Add to Friends?", n=2), 11)
        self.feed(one("Contract Shared: Preemptive Strike On ~mission(TargetName) <EM4>[100 Rep] [BP]*</EM4>"), 12)
        self.feed(ml("Initiated by Pilot_A", "Party Launching: Join Party In PU?", n=4), 13)
        self.feed(ml("Pilot_C wants to send you 750,000 UEC", "Currency Transfer Request: ", n=5), 14)
        asks = self.s.state()["asks"]
        self.assertEqual([(a["kind"], a["who"], a["what"], a["t"], a["open"]) for a in asks],
                         [("party", "Pilot_A", None, 10, True), ("friend", "Pilot_B", None, 11, True),
                          ("contract", None, "Preemptive Strike On ~mission(TargetName)", 12, True),
                          ("launch", "Pilot_A", None, 13, True), ("money", "Pilot_C", "750,000 UEC", 14, True)])
        self.feed(ml("You have joined", "party", n=6))
        self.feed(ml("Friend Added", "Pilot_B", n=7))
        self.feed(one("Contract Accepted:  Preemptive Strike On Some Target <EM4>[100 Rep] [BP]*</EM4>", n=8,
                      mid="1a2b3c4d-0000-4000-8000-000000000001"))
        self.feed(ml("Party Launch Accepted", "x", n=9))
        self.feed(ml("Pilot_C has sent you:", "750,000 aUEC", " ", n=10))
        self.assertEqual([a["open"] for a in self.s.state()["asks"]], [False] * 5)

    def test_decline_closes_party_invite(self):
        self.feed(ml("Pilot_A", "Party Invite Received: Accept Invitation?"))
        self.feed(TS + "[Notice] <Decline invitation> Client 1 decline invitation abc [Team_GameServices][Social]")
        self.assertFalse(self.s.state()["asks"][0]["open"])

    def test_asks_dedupe_and_cap(self):
        for i in range(25):
            self.feed(one(f"Contract Shared: Job {i}"), i)
        self.feed(one("Contract Shared: Job 24"), 99)
        asks = self.s.state()["asks"]
        self.assertEqual(len(asks), 20)
        self.assertEqual((asks[-1]["what"], asks[-1]["t"]), ("Job 24", 99))
        self.assertEqual(sum(a["what"] == "Job 24" for a in asks), 1)

    def test_party(self):
        self.login()
        self.feed(ml("You have created", "party"))
        self.assertEqual(self.s.state()["party"]["leader"], "Pilot_Me")
        self.feed(ml("New Member Joined", "Pilot_A has joined the party."))
        self.feed(ml("New Member Joined", "Pilot_B has joined the party."))
        self.feed(ml("Party", "Pilot_C connected."))
        self.feed(ml("Party", "Pilot_C disconnected."))
        self.feed(ml("Party", "Pilot_Me connected."))
        self.feed(ml("New Party Leader", "Pilot_A is now party leader."))
        self.feed(ml("Member Left", "pilot_b has left the party."))
        p = self.s.state()["party"]
        self.assertEqual((p["members"], p["leader"]), (["Pilot_A", "Pilot_C"], "Pilot_A"))
        self.feed(ml("New Member Joined", "Pilot_D has joined the group 'Some Org'."))
        self.assertNotIn("Pilot_D", self.s.state()["party"]["members"])
        self.feed(ml("You have been kicked from the party.", ""))
        self.assertEqual(self.s.state()["party"], {"members": [], "leader": None, "near": 0})

    def test_party_disbanded_and_left(self):
        for gone in (("Party Disbanded", "The party has been disbanded."), ("You have left the party.", "")):
            self.feed(ml("New Member Joined", "Pilot_A has joined the party."))
            self.feed(ml(*gone))
            self.assertEqual(self.s.state()["party"]["members"], [], gone)

    def _marker(self, verb, eid):
        comp = "RWES" if verb == "Streamed in" else "UFES"
        return (TS + f"[Notice] <CPartyMarkerComponent {comp}> {verb} party marker id 9{eid}. "
                f"TrackedEntityId: {eid} [Team_GameServices][EntitySubscription]")

    def test_party_markers_near(self):
        for eid in ("101", "102", "101"):
            self.feed(self._marker("Streamed in", eid))
        self.assertEqual(self.s.state()["party"]["near"], 2)
        self.feed(self._marker("Streaming out", "101"))
        self.assertEqual(self.s.state()["party"]["near"], 1)
        self.feed(ml("You have left the party.", ""))
        self.assertEqual(self.s.state()["party"]["near"], 0)

    def test_qt_target_and_fuel(self):
        s = sc_livelog.LogState(place_name=lambda code: "Orison" if code == "Orison_LOC" else None)
        ship = "[ItemNavigation][CL][1] | NOT AUTH | SHIP_A_123[123]|CSCItemNavigation::"
        s.feed(TS + f"[Notice] <Player Selected Quantum Target - Local> {ship}OnPlayerSelectedQuantumTarget|"
               "Player has selected point Orison_LOC as their destination, routing locally "
               "[Team_CGP4][QuantumTravel]", 3)
        self.assertEqual(s.state()["qt"], {"target": "Orison", "fuel": None, "t": 3})
        s.feed(TS + f"[Notice] <Calculate Route> {ship}CalculateRoute|Successfully calculated route to Orison_LOC "
               "fuel estimate 338.661255 [Team_CGP4][QuantumTravel]", 3)
        self.assertEqual(s.state()["qt"]["fuel"], 338.661255)
        s.feed(TS + f"[Notice] <Calculate Route> {ship}CalculateRoute|Successfully calculated route to "
               "PartyMemberMarker_555 fuel estimate 12.5 [Team_CGP4][QuantumTravel]", 4)
        self.assertEqual(s.state()["qt"], {"target": "PartyMemberMarker_555", "fuel": 12.5, "t": 4})

    def test_places_history(self):
        s = sc_livelog.LogState(place_name=lambda code: {"A_1": "Alpha"}.get(code))
        for i, code in enumerate(["A_1", "A_1", "B_2", "INVALID", "A_1"] + [f"C_{n}" for n in range(10)]):
            s.feed(TS + f"<RequestLocationInventory> Player[p] requested inventory for Location[{code}]", i)
        pl = s.state()["places"]
        self.assertEqual(len(pl), 10)
        self.assertEqual(pl[-1], {"name": "C_9", "t": 14})
        s.reset()
        for i, code in enumerate(["A_1", "A_1", "B_2", "A_1"]):
            s.feed(TS + f"<RequestLocationInventory> Player[p] requested inventory for Location[{code}]", i)
        self.assertEqual([p["name"] for p in s.state()["places"]], ["Alpha", "B_2", "Alpha"])

    def test_kill_by_me(self):
        self.feed(kill(killer="Pilot_Me"))
        self.assertFalse(self.s.state()["kills"][-1]["by_me"])          # who I am is not known yet
        self.login()
        self.feed(kill(killer="Pilot_Me"))
        self.feed(kill(killer="pilot_me"))
        self.assertEqual([k["by_me"] for k in self.s.state()["kills"]], [False, True, False])

    def _death(self, victim, killer):
        return (TS + f"[Notice] <Actor Death> CActor::Kill: '{victim}' [1] in zone 'SHIP_A_555' killed by "
                f"'{killer}' [2] using 'gun_01_777' [Class unknown] with damage type 'Bullet' from direction "
                "x: 0.000000, y: 0.000000, z: 0.000000 [Team_ActorTech][Actor]")

    def test_deaths(self):
        self.login()
        self.feed(self._death("Pilot_A", "Pilot_Me"), 1)
        self.feed(self._death("Pilot_Me", "Pilot_B"), 2)
        self.assertEqual(self.s.state()["deaths"], [{"t": 2, "killer": "Pilot_B", "zone": "SHIP_A_555"}])
        for i in range(25):
            self.feed(self._death("Pilot_Me", "Pilot_B"), i)
        self.assertEqual(len(self.s.state()["deaths"]), 20)

    def test_objectives_by_mission(self):
        a, b = "1a2b3c4d-0000-4000-8000-00000000000a", "1a2b3c4d-0000-4000-8000-00000000000b"
        self.feed(one("Contract Accepted:  Repair Location", mid=a))
        self.feed(one("Contract Accepted:  Other Job <EM4>[50/150/200 Rep]</EM4>", mid=b))
        self.feed(one("New Objective: Go to Outpost", mid=a, obj="o1"))
        self.feed(one("New Objective: Repair Pipe ", mid=a, obj="o2"))
        self.feed(one("New Objective: OPTIONAL: Investigate Ace Pilot", mid=a, obj="o3"))
        self.feed(ml("Objective Complete: Go to Outpost", "", mid=a, obj="o1"))
        self.feed(one("Objective Withdrawn: Repair Pipe", mid=a, obj="o2"))
        self.feed(ml("Objective Failed: OPTIONAL: Investigate Ace Pilot", "", mid=a, obj="o3"))
        self.feed(one("New Objective: Fix Relays", mid=a, obj="o4"))
        cs = {c["name"]: c for c in self.s.state()["contracts"]}
        self.assertEqual(cs["Repair Location"]["objectives"],
                         [{"text": "Go to Outpost", "state": "complete", "optional": False},
                          {"text": "Repair Pipe", "state": "withdrawn", "optional": False},
                          {"text": "Investigate Ace Pilot", "state": "failed", "optional": True},
                          {"text": "Fix Relays", "state": "active", "optional": False}])
        self.assertEqual(cs["Repair Location"]["objective"], "Fix Relays")
        self.assertEqual(cs["Other Job"]["objectives"], [])
        self.assertEqual(cs["Other Job"]["rep"], 200)
        # the complete notice can carry another title, the mission id finds the contract
        self.feed(one("Contract Complete: Outpost needs some repairs", mid=a))
        cs = self.s.state()["contracts"]
        self.assertEqual(len(cs), 2)
        self.assertEqual(cs[0]["status"], "complete")

    def test_objective_without_mission_goes_to_newest_active(self):
        self.feed(notif("Contract Accepted: Job A"))
        self.feed(notif("New Objective: Do A"))
        c = self.s.state()["contracts"][0]
        self.assertEqual((c["objective"], c["objectives"]),
                         ("Do A", [{"text": "Do A", "state": "active", "optional": False}]))

    def test_drops(self):
        mid = "1a2b3c4d-0000-4000-8000-00000000000c"
        self.feed(one("Contract Accepted:  Need a Hauler", mid=mid))
        self.feed(one("New Objective: Deliver 0/6 SCU of Hydrogen to Station One", mid=mid, obj="d1"))
        self.feed(one("New Objective: Deliver 0/4 SCU of Iron to Station Two", mid=mid, obj="d2"))
        self.feed(one("New Objective: Deliver 3/6 SCU of Hydrogen to Station One", mid=mid, obj="d1"))
        self.feed(ml("Objective Complete: Deliver 4/4 SCU of Iron to Station Two", "", mid=mid, obj="d2"))
        c = self.s.state()["contracts"][0]
        self.assertEqual(c["drops"], [{"cargo": "Hydrogen", "a": 3, "b": 6, "to": "Station One"},
                                      {"cargo": "Iron", "a": 4, "b": 4, "to": "Station Two"}])
        self.assertEqual(len(c["objectives"]), 2)
        self.assertEqual(c["objective"], "Deliver 3/6 SCU of Hydrogen to Station One")

    def test_rep_and_bp_tags(self):
        self.feed(notif("Contract Accepted:  Help Protect Site <EM4>[500 Rep] [BP]*</EM4>: "))
        self.feed(notif("Contract Accepted:  Plain Job: "))
        cs = self.s.state()["contracts"]
        self.assertEqual([(c["name"], c["rep"], c["bp"]) for c in cs],
                         [("Help Protect Site", 500, True), ("Plain Job", None, False)])

    def test_reset_and_copies(self):
        self.login()
        mid = "1a2b3c4d-0000-4000-8000-00000000000d"
        self.feed(ml("Pilot_A", "Party Invite Received: Accept Invitation?"))
        self.feed(ml("New Member Joined", "Pilot_A has joined the party."))
        self.feed(one("Contract Accepted:  Job", mid=mid))
        self.feed(one("New Objective: Do it", mid=mid, obj="x"))
        self.feed(TS + 'Added notification "Party')                 # cut off mid notice
        st = self.s.state()
        st["asks"][0]["open"] = False
        st["party"]["members"].append("Pilot_Z")
        st["contracts"][0]["objectives"][0]["state"] = "complete"
        st["qt"]["fuel"] = 1
        again = self.s.state()
        self.assertTrue(again["asks"][0]["open"])
        self.assertEqual(again["party"]["members"], ["Pilot_A"])
        self.assertEqual(again["contracts"][0]["objectives"][0]["state"], "active")
        self.assertIsNone(again["qt"]["fuel"])
        self.s.reset()
        self.assertEqual(self.s.state(), sc_livelog.LogState().state())
        self.feed(one("Welcome"))                                     # nothing left of the cut-off notice
        self.assertEqual([n["text"] for n in self.s.state()["notices"]], ["Welcome"])


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
