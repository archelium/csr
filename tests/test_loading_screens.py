"""Loading screens: per-session parse, launch to login, menu time, and the aggregates."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

DAY = "2026-10-24"


def line(hms, body):
    return f"<{DAY}T{hms}.000Z> {body}\n"


def screen(hms, dest, secs, kind="SC_Frontend"):
    return line(hms, f"Loading screen for {dest} : {kind} closed after {secs} seconds")


LOGIN = line("10:00:20", "[Notice] <LoginCompleted> LoginCompleted message [Team_GameServices][Login]")
# the other login lines carry the word too, none of them is the launch-to-login stamp
NOISE = line("10:00:05", "[Notice] <CDiffusionCryClient::OnLoginCompleted> New login completed")


def scan(text):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "Game.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return sc_stats.scan_log(p)


class ScanLogTests(unittest.TestCase):
    def test_loads_login_startup_and_menu_time(self):
        s = scan(line("10:00:00", "Log started")
                 + NOISE + LOGIN
                 + screen("10:00:30", "Frontend_Main", 2.5)       # startup ends
                 + screen("10:02:00", "pu", 40.0)                  # opened 10:01:20
                 + screen("10:30:00", "Frontend_Main", 3.0)        # back to the menu
                 + screen("10:32:00", "pu", 60.0)                  # opened 10:31:00
                 + screen("10:50:00", "Nyx", 1000.0)               # hung
                 + screen("10:55:00", "Frontend_Main", 2.0)        # menu until the log ends
                 + line("11:00:00", "end"))
        self.assertEqual(s["loads"], [["menu", 2.5], ["Stanton", 40.0], ["menu", 3.0],
                                      ["Stanton", 60.0], ["menu", 2.0]])
        self.assertEqual(s["loads_hung"], 1)
        self.assertEqual(s["login_s"], 20.0)
        self.assertEqual(s["startup_s"], 27.5)            # up to where the first menu screen opened
        # 10:00:30 to 10:01:20, 10:30:00 to 10:31:00, 10:55:00 to 11:00:00
        self.assertEqual(s["menu_s"], 50 + 60 + 300)
        # every screen that was not hung, the first menu one included
        self.assertEqual(s["load_s"], 2.5 + 40.0 + 3.0 + 60.0 + 2.0)
        self.assertEqual(s["load_s"], sum(secs for _d, secs in s["loads"]))

    def test_hung_first_menu_screen_stays_out_of_startup_and_loading(self):
        s = scan(line("10:00:00", "Log started")
                 + screen("13:00:00", "Frontend_Main", 10000.0)    # opened 10:13:20, hung
                 + screen("13:01:00", "pu", 40.0)
                 + line("13:05:00", "end"))
        self.assertEqual(s["loads"], [["Stanton", 40.0]])
        self.assertEqual(s["loads_hung"], 1)
        self.assertEqual(s["startup_s"], 800.0)                    # ends where the hung screen opened
        self.assertEqual(s["load_s"], 40.0)

    def test_destinations_are_normalised(self):
        s = scan(line("10:00:00", "Log started")
                 + screen("10:01:00", "pu", 30.0)
                 + screen("10:02:00", "Pyro", 31.0)
                 + screen("10:03:00", "Nyx", 32.0)
                 + screen("10:04:00", "EA_Demien_Comms_FPSKillConfirmed", 33.0, "EA_FPSKillConfirmed")
                 + screen("10:05:00", "Somewhere_New", 34.0)
                 + line("10:06:00", "end"))
        self.assertEqual([d for d, _s in s["loads"]],
                         ["Stanton", "Pyro", "Nyx", "Arena Commander", "Somewhere_New"])

    def test_log_without_screens_or_login(self):
        s = scan(line("10:00:00", "Log started") + line("10:10:00", "end"))
        self.assertEqual(s["loads"], [])
        self.assertEqual(s["loads_hung"], 0)
        self.assertIsNone(s["login_s"])
        self.assertIsNone(s["startup_s"])
        self.assertEqual(s["menu_s"], 0)


def _session(patch, dur, loads, **kw):
    base = scan(line("10:00:00", "Log started") + line("11:00:00", "end"))
    fields = dict(patch=patch, build=None, dur=dur, loads=loads, loads_hung=0, login_s=None,
                  startup_s=None, menu_s=0, load_s=0, aboard_secs=0)
    return dict(base, **{**fields, **kw})


class AggregateTests(unittest.TestCase):
    def finalize(self, sessions):
        agg = sc_stats.blank_patch()
        for s in sessions:
            sc_stats.fold(agg, s)
        return sc_stats.finalize(agg)

    def two_sessions(self):
        a = _session("4.9", 3600, [["Stanton", 40.0], ["Stanton", 60.0], ["menu", 3.0]],
                     login_s=10.0, startup_s=30.0, load_s=103.0, menu_s=400.0, aboard_secs=1200)
        b = _session("4.4", 1800, [["Pyro", 80.0], ["Stanton", 50.0]],
                     login_s=20.0, startup_s=20.0, load_s=130.0, menu_s=100.0)
        return a, b

    def test_loading_summary(self):
        a, b = self.two_sessions()
        v = self.finalize((a, b))["loading"]
        self.assertEqual(v["n"], 4)
        self.assertEqual(v["median"], 55.0)
        self.assertEqual(v["p90"], 80.0)
        self.assertEqual(v["slowest"], [80.0, b["start"].date().isoformat(), "Pyro"])
        self.assertEqual(v["total_s"], 233)
        self.assertEqual(v["by_dest"], [["Stanton", 3, 50.0], ["Pyro", 1, 80.0]])
        self.assertEqual(v["login_med"], 15.0)
        self.assertEqual(v["login_n"], 2)
        self.assertEqual(v["hung"], 0)

    def test_hours_buckets_sum_to_the_session_durations(self):
        v = self.finalize(self.two_sessions())["hours_split"]
        self.assertEqual(v["startup"], 50)
        self.assertEqual(v["loading"], 233)
        self.assertEqual(v["menu"], 500)
        self.assertEqual(v["aboard"], 1200)
        self.assertEqual(v["foot"], 3600 - 30 - 103 - 400 - 1200)
        self.assertEqual(v["unsplit"], 1800 - 20 - 130 - 100)
        self.assertEqual(v["old"], 0)
        self.assertEqual(v["total"], 3600 + 1800)

    def test_sessions_without_load_data_go_in_their_own_bucket(self):
        a, b = self.two_sessions()
        old = {k: v for k, v in b.items() if k not in ("loads", "loads_hung", "login_s",
                                                       "startup_s", "menu_s", "load_s")}
        v = self.finalize([a, old])
        self.assertEqual(v["hours_split"]["old"], 1800)
        self.assertEqual(v["hours_split"]["total"], 3600 + 1800)
        self.assertEqual(v["hours_split"]["loading"], 103)         # the old session adds none
        self.assertEqual(v["loading"]["n"], 2)

    def test_scope_with_no_loads(self):
        v = self.finalize([_session("4.9", 600, [])])
        self.assertIsNone(v["loading"])
        self.assertEqual(v["hours_split"]["total"], 600)

    def test_menu_only_scope_has_no_pu_numbers(self):
        v = self.finalize([_session("4.9", 600, [["menu", 3.0]])])["loading"]
        self.assertEqual(v["n"], 0)
        self.assertIsNone(v["median"])
        self.assertIsNone(v["slowest"])
        self.assertEqual(v["by_dest"], [])


if __name__ == "__main__":
    unittest.main()
