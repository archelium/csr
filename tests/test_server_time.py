"""Time on a server: joins to returns to the menu, next to playtime."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

DAY = "2026-11-26"
JOIN = ('[Notice] <Context Establisher Done> establisher="Network" runningTime=75.1 map="megamap" '
        'gamerules="SC_Default" sessionId="x" [Team_Network]')
MENU = '[CSessionManager::RequestFrontEnd] Started - RequestFrontEndReason="OnLobbyPostGameUnload"!'
BOOT = '[CSessionManager::RequestFrontEnd] Started - RequestFrontEndReason="ProcessBootRequest"!'


def line(hms, body):
    return f"<{DAY}T{hms}.000Z> {body}\n"


def scan(text):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "Game.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return sc_stats.scan_log(p)


class ServerSecondsTests(unittest.TestCase):
    T = datetime(2026, 11, 26, 10, 0, 0)

    def at(self, m):
        return self.T + timedelta(minutes=m)

    def test_join_to_menu_and_to_end(self):
        ev = [("F", self.at(0)), ("J", self.at(2)), ("F", self.at(32)), ("J", self.at(35))]
        self.assertEqual(sc_stats.server_seconds(ev, self.at(45), 45 * 60), 40 * 60)

    def test_menu_only_launch(self):
        self.assertEqual(sc_stats.server_seconds([("F", self.at(0))], self.at(10), 600), 0)

    def test_capped_at_playtime_and_zero_playtime(self):
        self.assertEqual(sc_stats.server_seconds([("J", self.at(0))], self.at(60), 600), 600)
        self.assertEqual(sc_stats.server_seconds([("J", self.at(0))], self.at(60), 0), 0)


class ScanTests(unittest.TestCase):
    def test_scan_log(self):
        # launch 10:00, join 10:02, back to the menu 10:32, rejoin 10:35, quit 10:45
        s = scan(line("10:00:00", "Log started") + line("10:00:30", BOOT) + line("10:02:00", JOIN)
                 + line("10:32:00", MENU) + line("10:35:00", JOIN) + line("10:45:00", "CSystem::Quit"))
        self.assertEqual(s["dur"], 45 * 60)
        self.assertEqual(s["srv"], 40 * 60)

    def test_never_joined(self):
        s = scan(line("10:00:00", "Log started") + line("10:00:30", BOOT) + line("10:10:00", "CSystem::Quit"))
        self.assertEqual(s["srv"], 0)


class FoldTests(unittest.TestCase):
    def test_sessions_without_the_figure_are_left_out(self):
        agg = sc_stats.blank_patch()
        self.assertEqual((agg["srv_seconds"], agg["srv_of"]), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
