"""Places visited: location codes to names, the per-session parse and the aggregate."""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

DAY = "2026-10-24"
ME = "archelium"


def line(hms, body):
    return f"<{DAY}T{hms}.000Z> {body}\n"


def inventory(hms, code, player=ME):
    return line(hms, f"[Notice] <RequestLocationInventory> Player[{player}] requested inventory "
                     f"for Location[{code}] [Team_CoreGameplayFeatures][Inventory]")


LOGIN = line("10:00:01", f"[Notice] <Legacy login response> Handle[{ME}] login ok")


def scan(text):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "Game.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return sc_stats.scan_log(p)


class PlaceNameTests(unittest.TestCase):
    def test_table_code(self):
        self.assertEqual(sc_stats.place_name("RR_MIC_L1"),
                         ("MIC-L1 Shallow Frontier Station", "Stanton", "Rest stop"))
        self.assertEqual(sc_stats.place_name("RR_HUR_LEO"), ("Everus Harbor", "Stanton", "Station"))
        self.assertEqual(sc_stats.place_name("RR_P2_L4"), ("Checkmate Station", "Pyro", "Rest stop"))

    def test_jump_point_station_sits_in_the_system_it_leaves(self):
        self.assertEqual(sc_stats.place_name("RR_JP_PyroStanton"),
                         ("Stanton Gateway (Pyro)", "Pyro", "Jump point"))
        self.assertEqual(sc_stats.place_name("RR_JP_StantonPyro"),
                         ("Pyro Gateway (Stanton)", "Stanton", "Jump point"))

    def test_mechanical_label_when_no_name_is_confirmed(self):
        self.assertEqual(sc_stats.place_name("RR_P2_L2"), ("Pyro II L2 rest stop", "Pyro", "Rest stop"))
        self.assertEqual(sc_stats.place_name("RR_CRU_L3"), ("Crusader L3 rest stop", "Stanton", "Rest stop"))
        self.assertEqual(sc_stats.place_name("Nyx_TSG_QVExtractionStation_049"),
                         ("QV Extraction Station 049", "Nyx", "Station"))

    def test_city(self):
        self.assertEqual(sc_stats.place_name("Stanton4_NewBabbage"), ("New Babbage", "Stanton", "City"))
        self.assertEqual(sc_stats.place_name("Nyx_Levski"), ("Levski", "Nyx", "City"))

    def test_wikelo_asteroid(self):
        self.assertEqual(sc_stats.place_name("TheCollectorAsteroid_Stanton4"),
                         ("Wikelo Emporium Kinga Station", "Stanton", "Wikelo"))
        self.assertEqual(sc_stats.place_name("TheCollectorAsteroid_Stanton3"),
                         ("Wikelo asteroid (ArcCorp)", "Stanton", "Wikelo"))

    def test_distribution_centre(self):
        self.assertEqual(sc_stats.place_name("Stanton4_DistributionCentre_Covalex_S4DC05"),
                         ("Covalex S4DC05", "Stanton", "Facility"))
        self.assertEqual(sc_stats.place_name("Stanton4_DistributionCentre_microTech_S4LD13"),
                         ("microTech S4LD13", "Stanton", "Facility"))

    def test_facilities_and_outposts(self):
        self.assertEqual(sc_stats.place_name("Stanton1_HurdynMining_HDMSHadley"),
                         ("HDMS-Hadley", "Stanton", "Facility"))
        self.assertEqual(sc_stats.place_name("Stanton4_Shubin_SM0_18"),
                         ("Shubin Mining Facility SM0-18", "Stanton", "Facility"))
        self.assertEqual(sc_stats.place_name("Outpost_PAF_Stanton1b_Ruptura_2"),
                         ("Ruptura PAF-II", "Stanton", "Outpost"))
        self.assertEqual(sc_stats.place_name("Stanton3b_ASD_Delve_Facility_010"),
                         ("ASD Delve 010 (Wala)", "Stanton", "Facility"))
        self.assertEqual(sc_stats.place_name("Pyro5b_Outpost_col_m_scrp_otlw_001"),
                         ("Pyro 5b outpost scrp otlw 001", "Pyro", "Outpost"))

    def test_unknown_code_falls_back_to_its_words(self):
        self.assertEqual(sc_stats.place_name("Stanton2_SomeNewPlace"), ("Some New Place", "Stanton", "Other"))


class ScanLogTests(unittest.TestCase):
    def test_only_your_requests_count_once_per_place(self):
        s = scan(line("10:00:00", "Log started") + LOGIN
                 + inventory("10:01:00", "RR_HUR_LEO")
                 + inventory("10:02:00", "RR_HUR_LEO")
                 + inventory("10:03:00", "Stanton4_NewBabbage")
                 + inventory("10:04:00", "RR_CRU_LEO", player="somebody_else")
                 + inventory("10:05:00", "INVALID_LOCATION_ID")
                 + line("10:06:00", "end"))
        self.assertEqual(s["places"], ["RR_HUR_LEO", "Stanton4_NewBabbage"])

    def test_log_without_requests(self):
        s = scan(line("10:00:00", "Log started") + line("10:10:00", "end"))
        self.assertEqual(s["places"], [])


def _session(day, places):
    base = scan(line("10:00:00", "Log started") + line("11:00:00", "end"))
    y, m, d = (int(x) for x in day.split("-"))
    return dict(base, start=datetime(y, m, d, 20, 0), places=places)


class AggregateTests(unittest.TestCase):
    def finalize(self, sessions):
        agg = sc_stats.blank_patch()
        for s in sessions:
            sc_stats.fold(agg, s)
        return sc_stats.finalize(agg)

    def test_rows_are_most_visited_first_with_the_first_local_date(self):
        v = self.finalize([
            _session("2026-03-02", ["RR_HUR_LEO", "Stanton4_NewBabbage"]),
            _session("2026-03-01", ["Stanton4_NewBabbage", "RR_CRU_LEO"]),
            _session("2026-03-03", ["Stanton4_NewBabbage", "RR_CRU_LEO", "Stanton1_Lorville"]),
        ])["places"]
        self.assertEqual(v["n"], 4)
        self.assertEqual(v["home"], "New Babbage")
        self.assertEqual(v["rows"], [
            ["New Babbage", "Stanton", "City", 3, "2026-03-01"],
            ["Seraphim Station", "Stanton", "Station", 2, "2026-03-01"],
            ["Everus Harbor", "Stanton", "Station", 1, "2026-03-02"],
            ["Lorville", "Stanton", "City", 1, "2026-03-03"],
        ])
        self.assertEqual(v["by_kind"], [["City", 2], ["Station", 2]])    # ties by name

    def test_codes_with_one_display_name_merge(self):
        # same outpost, logged with two spellings of the system prefix
        v = self.finalize([
            _session("2026-03-01", ["Pyro3_Outpost_col_s_frm_otlw_002", "pyro3_outpost_col_s_frm_otlw_002"]),
            _session("2026-03-02", ["pyro3_outpost_col_s_frm_otlw_002"]),
        ])["places"]
        self.assertEqual(v["n"], 1)
        self.assertEqual(v["rows"], [["Pyro 3 outpost frm otlw 002", "Pyro", "Outpost", 2, "2026-03-01"]])

    def test_sessions_archived_before_places_are_ignored(self):
        old = _session("2026-03-01", [])
        del old["places"]
        self.assertIsNone(self.finalize([old])["places"])

    def test_scope_with_no_places(self):
        self.assertIsNone(self.finalize([_session("2026-03-01", [])])["places"])


if __name__ == "__main__":
    unittest.main()
