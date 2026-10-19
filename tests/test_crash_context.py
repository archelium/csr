"""CrashContext: what the game was doing when it went down."""
import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sc_stats import CrashContext, CTX_FIELD_MAX  # noqa: E402

DAY = "2026-07-19"


def dt(hms):
    return datetime.strptime(f"{DAY}T{hms}", "%Y-%m-%dT%H:%M:%S")


def feed(ctx, hms, body):
    ctx.feed(f"<{DAY}T{hms}.000Z> {body}", dt(hms))


def join_pu(ctx, hms="10:00:00"):
    feed(ctx, hms, "[Notice] <Join PU> address[1.2.3.4]")
    feed(ctx, hms, '<Context Establisher Done> establisher="Network" gamerules="SC_Default"')


class CrashContextTests(unittest.TestCase):
    def test_game_phase_place_and_time_in(self):
        c = CrashContext()
        feed(c, "10:00:00", '[Notice] <Join PU> address[1.2.3.4]')
        feed(c, "10:00:30", '<Context Establisher Done> establisher="Network" gamerules="SC_Default"')
        feed(c, "10:05:00", '[Notice] <SHUDEvent_OnNotification> Added notification '
                            '"Entered Crusader Industries Jurisdiction: " [4]')
        s = c.snapshot(dt("11:12:30"))
        self.assertEqual(s["phase"], "game")
        self.assertEqual(s["into_s"], 4320)
        self.assertEqual(s["where"], "Crusader Industries")

    def test_launch_crash_is_loading_with_no_place(self):
        c = CrashContext()
        feed(c, "10:00:00", "Log started")
        s = c.snapshot(dt("10:00:05"))
        self.assertEqual(s, {"phase": "loading"})

    def test_front_end_request_leaves_then_menu_clears_place_and_ship(self):
        c = CrashContext()
        join_pu(c)
        feed(c, "10:01:00", 'Added notification "Entered UEE Jurisdiction: " [1]')
        feed(c, "10:02:00", "granted control token for 'ANVL_Asgard_123456789'")
        feed(c, "10:03:00", "[CSessionManager::RequestFrontEnd] going back")
        s = c.snapshot(dt("10:03:01"))
        self.assertEqual(s["phase"], "leaving")
        self.assertEqual(s["where"], "UEE")
        self.assertEqual(s["ship_cls"], "ANVL_Asgard")
        self.assertEqual(s["into_s"], 181)
        feed(c, "10:03:05", "Loading screen for Frontend_Main : SC_Frontend closed after 2 seconds")
        s = c.snapshot(dt("10:04:00"))
        self.assertEqual(s["phase"], "menu")
        self.assertNotIn("where", s)
        self.assertNotIn("ship_cls", s)
        self.assertNotIn("into_s", s)

    def test_join_pu_clears_stale_place(self):
        c = CrashContext()
        join_pu(c)
        feed(c, "10:01:00", 'Added notification "Entered UEE Jurisdiction: " [1]')
        feed(c, "10:30:00", "[Notice] <Join PU> address[5.6.7.8]")
        s = c.snapshot(dt("10:30:10"))
        self.assertEqual(s, {"phase": "loading"})

    def test_ship_only_from_granted_token(self):
        c = CrashContext()
        join_pu(c)
        feed(c, "10:01:00", "releasing control token for 'ANVL_Asgard_123456789'")
        self.assertNotIn("ship_cls", c.snapshot(dt("10:02:00")))
        feed(c, "10:03:00", "granted control token for 'ANVL_Asgard_123456789'")
        self.assertEqual(c.snapshot(dt("10:04:00"))["ship_cls"], "ANVL_Asgard")
        feed(c, "10:05:00", "releasing control token for 'ANVL_Asgard_123456789'")
        self.assertNotIn("ship_cls", c.snapshot(dt("10:06:00")))

    def test_death_clears_ship(self):
        c = CrashContext()
        join_pu(c)
        feed(c, "10:03:00", "granted control token for 'ANVL_Asgard_123456789'")
        feed(c, "10:04:00", "[Notice] <Actor Death> ActorState] Dead")
        self.assertNotIn("ship_cls", c.snapshot(dt("10:05:00")))

    def test_last_err_top_tags_in_window(self):
        c = CrashContext()
        join_pu(c, "15:00:00")
        feed(c, "15:16:20", "[Error] <Failed attachment> old one Class(Old_Thing)")
        for i in range(5):
            feed(c, "15:16:55", "[Error] <Failed attachment> Unable to find port for entity "
                                "[Vehicle_Screen_MFD_725183570527 - Class(RSI_Polaris_Turret_Side_Left) - x]")
        for i in range(2):
            feed(c, "15:16:56", "[Error] <Other> something else")
        s = c.snapshot(dt("15:16:57"))
        self.assertEqual(s["last_err"][0],
                         {"tag": "Failed attachment", "n": 5, "cls": "RSI_Polaris_Turret_Side_Left"})
        self.assertEqual(s["last_err"][1], {"tag": "Other", "n": 2})
        self.assertEqual(len(s["last_err"]), 2)

    def test_class_falls_back_to_entity_name(self):
        c = CrashContext()
        feed(c, "10:00:00", "[Error] <Bad thing> on entity [RSI_Polaris_Pilot_SCItem_Seat_725183570526 - x]")
        self.assertEqual(c.snapshot(dt("10:00:01"))["last_err"],
                         [{"tag": "Bad thing", "n": 1, "cls": "RSI_Polaris_Pilot_SCItem_Seat"}])

    def test_err_target_is_most_common_attach_target(self):
        c = CrashContext()
        line = ("[Error] <Failed attachment> Unable to find item port for entity "
                "[Vehicle_Screen_Physical_Holo_725183570731 - Class(Vehicle_Screen_Physical_Holo) - x], "
                "it spawned with intent to attach to [%s:725183570729] and portId [319097128]")
        feed(c, "10:00:00", line % "RSI_Polaris_Pilot_SCItem_SeatDashboard_725183570726")
        feed(c, "10:00:01", line % "RSI_Polaris_CoPilot_SCItem_SeatDashboard_725183570729")
        feed(c, "10:00:02", line % "RSI_Polaris_CoPilot_SCItem_SeatDashboard_725183570800")
        feed(c, "10:00:02", "[Error] <Other> no target here")
        s = c.snapshot(dt("10:00:03"))["last_err"]
        self.assertEqual(s[0]["to"], "RSI_Polaris_CoPilot_SCItem_SeatDashboard")
        self.assertEqual(s[0]["cls"], "Vehicle_Screen_Physical_Holo")
        self.assertNotIn("to", s[1])

    def test_only_top_three_tags(self):
        c = CrashContext()
        for tag, n in (("A", 4), ("B", 3), ("C", 2), ("D", 1)):
            for _ in range(n):
                feed(c, "10:00:00", f"[Error] <{tag}> x")
        tags = [e["tag"] for e in c.snapshot(dt("10:00:01"))["last_err"]]
        self.assertEqual(tags, ["A", "B", "C"])

    def test_no_crash_time_gives_no_into_or_errors(self):
        c = CrashContext()
        join_pu(c)
        feed(c, "10:00:05", "[Error] <Failed attachment> x")
        self.assertNotIn("into_s", c.snapshot(None))
        self.assertNotIn("last_err", c.snapshot(None))

    def test_long_and_html_error_text_is_capped(self):
        c = CrashContext()
        feed(c, "10:00:00", "[Error] <" + "x & 'y' " * 40 + "> Class(" + "K" * 2000 + ")")
        e = c.snapshot(dt("10:00:01"))["last_err"][0]
        self.assertLessEqual(len(e["tag"]), CTX_FIELD_MAX)
        self.assertLessEqual(len(e["cls"]), CTX_FIELD_MAX)


if __name__ == "__main__":
    unittest.main()
