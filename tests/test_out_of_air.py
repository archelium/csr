"""Out of air: suffocation and depressurization pairs, merged into episodes."""
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
    return f"<{DAY}T{hms}Z> {body}\n"


LOGIN_LOG_START = line("10:00:00.000", "Log started")
LOGIN = line("10:00:01.000", f"[Notice] <Legacy login response> Handle[{ME}] login ok")


def notice(hms, what, kind="suffocating", player=ME):
    return line(hms, f"[Notice] <[STAMINA] Player {what} {kind}> Player[{player}] Details:")


def block(hms, helmet=False):
    """The detail lines after a start, then the first line without the [STAMINA] prefix."""
    head = "Not Wearing Helmet" if not helmet else "Wearing Helmet: qrt_utility_heavy_helmet_01, with room: True"
    return (line(hms, f"[STAMINA] RoomStack for Occupant: {ME}_breathingHelper")
            + line(hms, "[STAMINA] Current Atmosphere Pressure: 0.5, GasSaturation: 0.02")
            + line(hms, f"[STAMINA] {head}")
            + line(hms, "[STAMINA] Time since revive: 1966.09s")
            + line(hms, " [Team_ActorFeatures][Actor][Roomsystem]"))


def pair(start, stop, kind="suffocating", player=ME, helmet=False):
    return notice(start, "started", kind, player) + block(start, helmet) + notice(stop, "stopped", kind, player)


def scan(text):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "Game.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(LOGIN_LOG_START + LOGIN + text + line("11:00:00.000", "end"))
        return sc_stats.scan_log(p)



class ScanLogTests(unittest.TestCase):
    def test_pair_under_two_seconds_is_dropped(self):
        self.assertIsNone(scan(pair("10:10:00.000", "10:10:01.900"))["air"])
        self.assertIsNone(scan(pair("10:10:00.235", "10:10:00.247"))["air"])

    def test_ten_second_pair_is_kept_and_helmet_off_counted(self):
        air = scan(pair("10:10:00.000", "10:10:10.000"))["air"]
        self.assertEqual(air, {"eps": [10.0], "nohelm": 1})

    def test_helmet_on_is_not_counted(self):
        air = scan(pair("10:10:00.000", "10:10:10.000", helmet=True))["air"]
        self.assertEqual(air, {"eps": [10.0], "nohelm": 0})

    def test_another_players_block_is_ignored(self):
        text = pair("10:10:00.000", "10:10:10.000", player="somebody_else") \
            + pair("10:20:00.000", "10:20:05.000", kind="depressurization", player="somebody_else")
        self.assertIsNone(scan(text)["air"])

    def test_unclosed_start_is_dropped(self):
        text = pair("10:10:00.000", "10:10:04.000") \
            + notice("10:30:00.000", "started") + block("10:30:00.000")
        self.assertEqual(scan(text)["air"], {"eps": [4.0], "nohelm": 1})

    def test_overlapping_kinds_are_one_episode(self):
        text = (notice("10:10:00.000", "started", "suffocating") + block("10:10:00.000", helmet=True)
                + notice("10:10:00.400", "started", "depressurization") + block("10:10:00.400")
                + notice("10:10:06.000", "stopped", "depressurization")
                + notice("10:10:07.000", "stopped", "suffocating"))
        # one 7 s episode, and one of its two blocks said no helmet
        self.assertEqual(scan(text)["air"], {"eps": [7.0], "nohelm": 1})

    def test_separate_stretches_stay_separate_episodes(self):
        text = pair("10:10:00.000", "10:10:03.000") \
            + pair("10:12:00.000", "10:12:05.000", kind="depressurization")
        self.assertEqual(scan(text)["air"], {"eps": [3.0, 5.0], "nohelm": 2})

    def test_two_short_pairs_that_touch_add_up_to_an_episode(self):
        text = pair("10:10:00.000", "10:10:01.500") \
            + pair("10:10:01.000", "10:10:02.500", kind="depressurization")
        self.assertEqual(scan(text)["air"]["eps"], [2.5])

    def test_log_without_notices(self):
        self.assertIsNone(scan("")["air"])


def _session(day, air):
    base = scan("")
    y, m, d = (int(x) for x in day.split("-"))
    return dict(base, start=datetime(y, m, d, 20, 0), air=air)


class AggregateTests(unittest.TestCase):
    def finalize(self, sessions):
        agg = sc_stats.blank_patch()
        for s in sessions:
            sc_stats.fold(agg, s)
        return sc_stats.finalize(agg)

    def test_totals_longest_and_sessions(self):
        v = self.finalize([
            _session("2026-03-01", {"eps": [3.0, 12.5], "nohelm": 1}),
            _session("2026-03-02", {"eps": [40.0], "nohelm": 0}),
            _session("2026-03-03", None),
        ])["air"]
        self.assertEqual(v, {"n": 3, "secs": 56, "longest": [40.0, "2026-03-02"],
                             "nohelm": 1, "sessions": 2})

    def test_sessions_archived_before_the_metric_are_ignored(self):
        old = _session("2026-03-01", None)
        del old["air"]
        self.assertIsNone(self.finalize([old])["air"])

    def test_scope_with_no_episodes(self):
        self.assertIsNone(self.finalize([_session("2026-03-01", None)])["air"])


if __name__ == "__main__":
    unittest.main()
