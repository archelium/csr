"""ShipTime: time aboard each ship (crew channel) and in its pilot seat (control token)."""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_names  # noqa: E402
import sc_stats  # noqa: E402
from sc_stats import ShipTime  # noqa: E402

DAY = "2026-10-24"
ME = "archelium"
CONNIE = "RSI Constellation Mk V Centaurus"
CONNIE_CLS = "RSI_Constellation_Mk5_Centaurus"
URSA = "RSI Ursa Medivac"


def dt(hms):
    return datetime.strptime(f"{DAY}T{hms}", "%Y-%m-%dT%H:%M:%S")


def line(hms, body):
    return f"<{DAY}T{hms}.000Z> {body}"


def feed(st, hms, body):
    ln = line(hms, body)
    st.feed(ln, ln[1:20])


def pu_join(st, hms):
    feed(st, hms, '[Notice] <Context Establisher Done> establisher="Network" '
                  'gamerules="SC_Default" message="CET completed"')


def joined(st, hms, ship, owner=ME):
    feed(st, hms, '[Notice] <SHUDEvent_OnNotification> Added notification '
                  f'"You have joined channel \'{ship} : {owner}\'.')


def left(st, hms, ship, owner=ME):
    feed(st, hms, '[Notice] <SHUDEvent_OnNotification> Added notification '
                  f'"You have left the channel \'{ship} : {owner}\'.')


def granted(st, hms, cls, eid="883385977732"):
    feed(st, hms, '[Notice] <Vehicle Control Flow> CVehicleMovementBase::SetDriver: Local client '
                  f"node [204821712526] granted control token for '{cls}_{eid}' [{eid}]")


def released(st, hms, cls, eid="883385977732"):
    feed(st, hms, '[Notice] <Vehicle Control Flow> CVehicleMovementBase::ClearDriver: Local client '
                  f"node [204821712526] releasing control token for '{cls}_{eid}' [{eid}] "
                  "[Team_CGP4][Vehicle]")


def nav(st, hms, cls, eid="883385977732"):
    feed(st, hms, '[Notice] <Failed to get starmap route data!> [ItemNavigation][CL][37952] '
                  f'| NOT AUTH | {cls}_{eid}[{eid}]|CSCItemNavigation::GetStarmapRouteSegmentData|'
                  'No Route loaded!')


def spawned(st, hms, cls, eid="883385977732"):
    feed(st, hms, '[Notice] <CEntityComponentShipListProvider::SetVehicleSpawningInfo> '
                  f'vehicle {cls}_{eid}[{eid}] spawning')


def dead(st, hms):
    feed(st, hms, "[Notice] <[ActorState] Dead> Actor 'archelium' [204821712526] ejected")


def menu(st, hms):
    feed(st, hms, "Loading screen for Frontend_Main : SC_Frontend closed after 2 seconds")


class AboardTests(unittest.TestCase):
    def test_join_then_leave(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        joined(st, "10:05:00", CONNIE)
        left(st, "10:10:00", CONNIE)
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE: 300})
        self.assertEqual(r["aboard_secs"], 300)
        self.assertEqual(r["crew_secs"], 0)

    def test_repeat_burst_counts_once_from_the_first_line(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        for hms in ("10:05:00", "10:05:00", "10:05:00", "10:05:10", "10:05:11"):
            joined(st, hms, CONNIE)
        for hms in ("10:10:00", "10:10:00", "10:10:00", "10:10:10", "10:10:11"):
            left(st, hms, CONNIE)
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE: 300})

    def test_woke_aboard_runs_from_pu_join_to_the_last_seat_exit(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        released(st, "10:20:00", CONNIE_CLS)
        r = st.result(dt("10:30:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE_CLS: 1200})
        self.assertEqual(r["aboard_secs"], 1200)

    def test_woke_aboard_later_exits_extend_it_menu_does_not(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        released(st, "10:20:00", CONNIE_CLS)
        released(st, "10:50:00", CONNIE_CLS)
        menu(st, "11:30:00")
        self.assertEqual(st.result(dt("11:31:00"), ME)["aboard"], {CONNIE_CLS: 3000})

    def test_woke_aboard_closed_by_a_later_join_ends_at_its_exit(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        released(st, "10:10:00", CONNIE_CLS)
        joined(st, "10:40:00", CONNIE)
        left(st, "10:50:00", CONNIE)
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE_CLS: 600, CONNIE: 600})

    def test_woke_aboard_leave_line_after_an_exit_ends_it(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        released(st, "10:10:00", CONNIE_CLS)
        left(st, "10:15:00", CONNIE)
        self.assertEqual(st.result(dt("11:00:00"), ME)["aboard"], {CONNIE: 900})

    def test_no_fallback_on_builds_before_crew_channels(self):
        st = ShipTime(chan=False)
        pu_join(st, "10:00:00")
        released(st, "10:20:00", CONNIE_CLS)
        r = st.result(dt("10:30:00"), ME)
        self.assertEqual(r["aboard"], {})
        self.assertEqual(r["aboard_secs"], 0)

    def test_crew_channel_builds(self):
        self.assertFalse(sc_stats.has_crew_channel("4.4", "4.4.164.18318"))
        self.assertFalse(sc_stats.has_crew_channel("4.5", "4.5.165.55819"))
        self.assertTrue(sc_stats.has_crew_channel("4.5", "4.5.167.22732"))
        self.assertTrue(sc_stats.has_crew_channel("4.10", "1.0.191.1"))
        self.assertTrue(sc_stats.has_crew_channel(None, None))

    def test_woke_aboard_leave_with_no_join_starts_at_pu_join(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        left(st, "10:40:00", CONNIE)
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE: 2400})

    def test_hyphenated_channel_name_matches_its_class(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        joined(st, "10:05:00", "Aegis Idris-P")
        released(st, "10:20:00", "AEGS_Idris_P")
        left(st, "10:25:00", "Aegis Idris-P")
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {"Aegis Idris-P": 1200})

    def test_unlocalized_channel_name_is_keyed_by_its_class(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        joined(st, "10:05:00", "@vehicle_NameRSI_Hermes")
        released(st, "10:20:00", "RSI_Hermes")
        left(st, "10:25:00", "@vehicle_NameRSI_Hermes")
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {"RSI_Hermes": 1200})

    def test_seat_exit_after_channel_left_does_not_reopen(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        joined(st, "10:05:00", CONNIE)
        left(st, "10:10:00", CONNIE)
        released(st, "10:20:00", CONNIE_CLS)
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE: 300})

    def test_seat_exit_inside_open_channel_keeps_the_channel_span(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        joined(st, "10:05:00", CONNIE)
        released(st, "10:20:00", CONNIE_CLS)
        left(st, "10:25:00", CONNIE)
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE: 1200})

    def test_death_closes_and_later_leave_is_ignored(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        joined(st, "10:00:00", CONNIE)
        dead(st, "10:10:00")
        left(st, "10:12:00", CONNIE)
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE: 600})

    def test_menu_closes_open_ships(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        joined(st, "10:00:00", CONNIE)
        menu(st, "10:15:00")
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE: 900})

    def test_no_fallback_after_death_until_the_next_pu_join(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        dead(st, "10:10:00")
        released(st, "10:20:00", CONNIE_CLS)
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {})

    def test_log_end_closes_open_ship(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        joined(st, "10:00:00", CONNIE)
        r = st.result(dt("10:45:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE: 2700})

    def test_overlap_counts_per_ship_and_once_in_the_total(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        joined(st, "10:00:00", CONNIE)
        joined(st, "10:20:00", URSA)
        left(st, "10:30:00", URSA)
        left(st, "11:00:00", CONNIE)
        r = st.result(dt("11:30:00"), ME)
        self.assertEqual(r["aboard"], {CONNIE: 3600, URSA: 600})
        self.assertEqual(r["aboard_secs"], 3600)

    def test_someone_elses_ship_is_crew_time(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        joined(st, "10:00:00", "Anvil Asgard", owner="Dregnod")
        left(st, "10:30:00", "Anvil Asgard", owner="Dregnod")
        joined(st, "10:40:00", CONNIE)
        left(st, "10:50:00", CONNIE)
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["crew_secs"], 1800)
        self.assertEqual(r["aboard_secs"], 2400)

    def test_owner_match_ignores_case(self):
        st = ShipTime()
        joined(st, "10:00:00", CONNIE, owner="Archelium")
        left(st, "10:10:00", CONNIE, owner="Archelium")
        self.assertEqual(st.result(dt("11:00:00"), ME)["crew_secs"], 0)

    def test_one_span_is_capped_at_twelve_hours(self):
        st = ShipTime()
        joined(st, "00:00:00", CONNIE)
        r = st.result(datetime.strptime("2026-10-25T06:00:00", "%Y-%m-%dT%H:%M:%S"), ME)
        self.assertEqual(r["aboard"], {CONNIE: 12 * 3600})


class SeatTests(unittest.TestCase):
    def test_old_build_granted_to_released_is_exact(self):
        st = ShipTime()
        granted(st, "10:00:00", "ANVL_C8R_Pisces")
        nav(st, "10:02:00", "ANVL_C8R_Pisces")
        released(st, "10:07:30", "ANVL_C8R_Pisces")
        self.assertEqual(st.result(dt("11:00:00"), ME)["seat"], {"ANVL_C8R_Pisces": 450})

    def test_new_build_entry_is_the_first_nav_line(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        spawned(st, "10:01:00", CONNIE_CLS)
        nav(st, "10:05:00", CONNIE_CLS)
        nav(st, "10:06:00", CONNIE_CLS)
        released(st, "10:15:00", CONNIE_CLS)
        self.assertEqual(st.result(dt("11:00:00"), ME)["seat"], {CONNIE_CLS: 600})

    def test_landing_gear_line_is_an_entry(self):
        st = ShipTime()
        feed(st, "10:00:00", "[Notice] <SCItemVehicleLandingGearSystem::CheckAutoDockingRequirmentsMet()> "
                             "Failed to AutoDock vehicle [RSI_Skylark_883385977792 - Class(RSI_Skylark) - "
                             "Context(Streamable Runtime-spawned) - Socpak()] to docking tube")
        released(st, "10:00:40", "RSI_Skylark", eid="883385977792")
        self.assertEqual(st.result(dt("11:00:00"), ME)["seat"], {"RSI_Skylark": 40})

    def test_seat_exit_with_no_earlier_line_adds_no_time(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        released(st, "10:15:00", CONNIE_CLS)
        self.assertEqual(st.result(dt("11:00:00"), ME)["seat"], {})

    def test_nav_line_before_the_previous_exit_does_not_count(self):
        st = ShipTime()
        nav(st, "10:00:00", CONNIE_CLS)
        released(st, "10:10:00", CONNIE_CLS)
        released(st, "10:20:00", CONNIE_CLS)
        self.assertEqual(st.result(dt("11:00:00"), ME)["seat"], {CONNIE_CLS: 600})

    def test_open_seat_closes_at_death(self):
        st = ShipTime()
        granted(st, "10:00:00", "ANVL_C8R_Pisces")
        dead(st, "10:03:00")
        released(st, "10:04:00", "ANVL_C8R_Pisces")
        self.assertEqual(st.result(dt("11:00:00"), ME)["seat"], {"ANVL_C8R_Pisces": 180})

    def test_open_seat_closes_at_log_end(self):
        st = ShipTime()
        granted(st, "10:00:00", "ANVL_C8R_Pisces")
        self.assertEqual(st.result(dt("10:20:00"), ME)["seat"], {"ANVL_C8R_Pisces": 1200})


class FloorTests(unittest.TestCase):
    def test_uncovered_seat_stint_counts_as_aboard(self):
        st = ShipTime()
        pu_join(st, "10:00:00")
        dead(st, "10:10:00")
        nav(st, "10:20:00", "KRIG_P72_Archimedes")
        released(st, "10:26:00", "KRIG_P72_Archimedes")
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {"KRIG_P72_Archimedes": 360})
        self.assertEqual(r["seat"], {"KRIG_P72_Archimedes": 360})

    def test_no_floor_on_builds_before_crew_channels(self):
        st = ShipTime(chan=False)
        granted(st, "10:00:00", "ANVL_C8R_Pisces")
        released(st, "10:05:00", "ANVL_C8R_Pisces")
        self.assertEqual(st.result(dt("11:00:00"), ME)["aboard"], {})

    def test_no_floor_inside_an_open_channel(self):
        st = ShipTime()
        joined(st, "10:00:00", CONNIE)
        nav(st, "10:05:00", CONNIE_CLS)
        released(st, "10:10:00", CONNIE_CLS)
        left(st, "10:20:00", CONNIE)
        self.assertEqual(st.result(dt("11:00:00"), ME)["aboard"], {CONNIE: 1200})


class CommandModuleTests(unittest.TestCase):
    CM, CM_CLS = "Drake Command Module", "DRAK_Command_Module"
    HOST, HOST_CLS = "Drake Ironclad Assault", "DRAK_Ironclad_Assault"

    def test_module_time_goes_to_the_host_once(self):
        st = ShipTime()
        joined(st, "09:55:00", self.HOST)
        joined(st, "10:00:00", self.CM)
        nav(st, "10:05:00", self.CM_CLS, eid="900000000001")
        released(st, "10:15:00", self.CM_CLS, eid="900000000001")
        left(st, "10:30:00", self.CM)
        left(st, "10:40:00", self.HOST)
        r = st.result(dt("11:00:00"), ME)
        self.assertEqual(r["aboard"], {self.HOST: 2700})
        self.assertEqual(r["aboard_secs"], 2700)
        self.assertEqual(r["seat"], {self.HOST: 600})

    def test_host_from_a_seat_exit_when_it_has_no_channel_line(self):
        st = ShipTime(chan=False)
        joined(st, "10:00:00", self.CM)
        left(st, "10:30:00", self.CM)
        released(st, "10:40:00", "DRAK_Caterpillar")
        self.assertEqual(st.result(dt("11:00:00"), ME)["aboard"], {"DRAK_Caterpillar": 1800})

    def test_module_with_no_host_keeps_its_own_key(self):
        st = ShipTime()
        joined(st, "10:00:00", self.CM)
        left(st, "10:30:00", self.CM)
        self.assertEqual(st.result(dt("11:00:00"), ME)["aboard"], {self.CM: 1800})


class MatchDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ix = sc_names.ShipIndex()
        assert cls.ix.load(allow_fetch=False)

    def name(self, s):
        rec = self.ix.match_display(s)
        return rec["name"] if rec else None

    def test_channel_names_resolve_to_matrix_names(self):
        self.assertEqual(self.name("Anvil C8R Pisces Rescue"), "C8R Pisces")
        self.assertEqual(self.name(CONNIE), "Constellation Mk V Centaurus")
        self.assertEqual(self.name(URSA), "Ursa Medivac")
        self.assertEqual(self.name("RSI Skylark"), "Skylark")

    def test_variant_names_fall_back_to_the_model_word(self):
        self.assertEqual(self.name("Origin 600i"), "600i Touring")
        self.assertEqual(self.name("Drake Dragonfly Star Kitten"), "Dragonfly Black")
        self.assertEqual(self.name("Hornet F7A Mk II PYAM Exec"), "F7A Hornet Mk II")
        self.assertEqual(self.name("RSI Constellation Phoenix Emerald"), "Constellation Mk IV Phoenix")
        self.assertEqual(self.name("Corsair PYAM Exec"), "Corsair")

    def test_same_name_as_the_class_route(self):
        for cls, chan in (("ORIG_600i", "Origin 600i"), ("DRAK_Dragonfly", "Drake Dragonfly")):
            self.assertEqual(self.ix.display(cls)[0], self.name(chan))

    def test_unknown_name_is_none(self):
        self.assertIsNone(self.name("Totally Made Up Ship"))
        self.assertIsNone(self.name("Drake Command Module"))

    def test_wikelo_f7_is_a_super_hornet_mk_ii_both_ways(self):
        self.assertEqual(self.name("Anvil F7 Hornet Mk Wikelo"), "F7C-M Super Hornet Mk II")
        self.assertEqual(self.ix.display("ANVL_Hornet_F7_Mk2_Collector_Mod")[0], "F7C-M Super Hornet Mk II")


class ScanLogWiringTests(unittest.TestCase):
    def test_scan_log_carries_ship_time(self):
        body = "".join(line(h, b) + "\n" for h, b in [
            ("10:00:00", "Log started"),
            ("10:00:01", "[Notice] <Legacy login response> [CIG-net] User Login Success - Handle[archelium] - Time[1]"),
            ("10:01:00", '[Notice] <Context Establisher Done> establisher="Network" gamerules="SC_Default"'),
            ("10:02:00", '[Notice] <SHUDEvent_OnNotification> Added notification '
                         f'"You have joined channel \'{CONNIE} : {ME}\'.'),
            ("10:05:00", '[Notice] <Failed to get starmap route data!> [ItemNavigation][CL][37952] '
                         f'| NOT AUTH | {CONNIE_CLS}_883385977732[883385977732]|x'),
            ("10:15:00", "[Notice] <Vehicle Control Flow> CVehicleMovementBase::ClearDriver: Local client "
                         f"node [1] releasing control token for '{CONNIE_CLS}_883385977732' [883385977732]"),
            ("10:20:00", '[Notice] <SHUDEvent_OnNotification> Added notification '
                         f'"You have left the channel \'{CONNIE} : {ME}\'.'),
            ("10:30:00", "end"),
        ])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Game.log")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(body)
            s = sc_stats.scan_log(p)
        self.assertEqual(s["aboard"], {CONNIE: 1080})
        self.assertEqual(s["aboard_secs"], 1080)
        self.assertEqual(s["crew_secs"], 0)
        self.assertEqual(s["seat"], {CONNIE_CLS: 600})


def _base_session():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "Game.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(line("10:00:00", "Log started") + "\n" + line("11:00:00", "end") + "\n")
        return sc_stats.scan_log(p)


def _agg(sessions):
    base, agg = _base_session(), sc_stats.blank_patch()
    for s in sessions:
        sc_stats.fold(agg, dict(base, **s))
    return agg


class RankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sc_stats.SHIPS.load(allow_fetch=False)

    def sessions(self, patch="4.9", build="4.9.188.3"):
        # Asgard: 1 session, 3 h aboard. Corsair: 2 sessions, 1 h aboard.
        return [
            {"ships": {"ANVL_Asgard"}, "ship_events": {"ANVL_Asgard": 1},
             "aboard": {"Anvil Asgard": 10800}, "aboard_secs": 10800, "patch": patch, "build": build},
            {"ships": {"DRAK_Corsair"}, "ship_events": {"DRAK_Corsair": 1},
             "aboard": {"Drake Corsair": 1800}, "aboard_secs": 1800, "patch": patch, "build": build},
            {"ships": {"DRAK_Corsair"}, "ship_events": {"DRAK_Corsair": 1},
             "aboard": {"Drake Corsair": 1800}, "aboard_secs": 1800, "patch": patch, "build": build},
        ]

    def test_covered_scope_ranks_by_time_aboard(self):
        v = sc_stats.finalize(_agg(self.sessions()))
        self.assertEqual([r[0] for r in v["ships_top"]], ["Asgard", "Corsair"])
        self.assertEqual(v["ships_rank"], "time")

    def test_scope_with_older_builds_ranks_by_sessions(self):
        old = self.sessions()
        old[0].update(patch="4.4", build="4.4.164.18318")
        v = sc_stats.finalize(_agg(old))
        self.assertEqual([r[0] for r in v["ships_top"]], ["Corsair", "Asgard"])
        self.assertEqual(v["ships_rank"], "sessions")


class FinalizeTests(unittest.TestCase):
    def test_top_ships_carry_aboard_and_seat_hours(self):
        sc_stats.SHIPS.load(allow_fetch=False)
        agg = sc_stats.blank_patch()
        s = {"dur": 7200, "start": dt("10:00:00"), "ships": {CONNIE_CLS}, "ship_events": {CONNIE_CLS: 3},
             "missions": {}, "aboard": {CONNIE: 3600, CONNIE_CLS: 1800, "Anvil Asgard": 900},
             "aboard_secs": 6300, "crew_secs": 1080, "seat": {CONNIE_CLS: 1440}}
        agg["aboard"].update(s["aboard"])
        agg["seat"].update(s["seat"])
        agg["aboard_secs"] += s["aboard_secs"]
        agg["crew_secs"] += s["crew_secs"]
        agg["ships"][CONNIE_CLS] += 1
        agg["ship_events"].update(s["ship_events"])
        v = sc_stats.finalize(agg)
        row = next(r for r in v["ships_top"] if r[0] == "Constellation Mk V Centaurus")
        self.assertEqual(row[5], 1.5)      # aboard hours: channel name + woke-aboard class merged
        self.assertEqual(row[6], 0.4)      # seat hours
        self.assertEqual(v["aboard_hours"], 1.8)
        self.assertEqual(v["crew_hours"], 0.3)


if __name__ == "__main__":
    unittest.main()
