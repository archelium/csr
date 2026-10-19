"""scan_log -> crash dict: the CrashContext wiring, on small synthetic logs."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

DAY = "2026-07-19"

BANNER = """
Cloud Imperium Games public crash handler taking over...
Details for StarCitizen.exe (md5=c62db05595abc47dec6fba9eefd41032)...
Exception EXCEPTION_ACCESS_VIOLATION(0xC0000005) addr=0x00007FF7C9E709B0 digest=2a7c3fa03b4efb17da23359db5a36f88
Is fatal error: No
Is GPU crash: No
Copying game.log...
"""


def stamped(hms, body):
    return f"<{DAY}T{hms}.000Z> {body}\n"


class ScanLogCrashTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def scan(self, text, name="crash.log"):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return sc_stats.scan_log(path)

    def test_launch_crash_after_boot_request(self):
        s = self.scan(
            stamped("10:00:00", "Log started on Sun Jul 19 10:00:00 2026")
            + stamped("10:00:02", '[CSessionManager::RequestFrontEnd] Started - '
                                  'RequestFrontEndReason="ProcessBootRequest"!')
            + stamped("10:00:02", "[CSessionManager::RequestFrontEnd] Restored!")
            + stamped("10:00:04", "[Notice] streaming the front end")
            + BANNER)
        c = s["crash"]
        self.assertEqual(s["outcome"], "crash")
        self.assertEqual(c["exception"], "EXCEPTION_ACCESS_VIOLATION")
        self.assertEqual(c["phase"], "loading")
        self.assertNotIn("where", c)
        self.assertNotIn("into_s", c)

    def test_in_game_crash_with_error_burst(self):
        errs = "".join(
            stamped("10:20:58", "[Error] <Failed attachment> Unable to find port for entity "
                                "[Vehicle_Screen_MFD_725183570527 - Class(RSI_Polaris_Turret) - x]")
            for _ in range(3))
        s = self.scan(
            stamped("10:00:00", "Log started on Sun Jul 19 10:00:00 2026")
            + stamped("10:01:00", "[Notice] <Join PU> address[1.2.3.4]")
            + stamped("10:01:30", '[Notice] <Context Establisher Done> establisher="Network" '
                                  'runningTime=1.0 map="megamap" gamerules="SC_Default" sessionId="x"')
            + stamped("10:05:00", '[Notice] <SHUDEvent_OnNotification> Added notification '
                                  '"Entered Crusader Industries Jurisdiction: " [4]')
            + errs
            + stamped("10:21:00", "[Notice] last words")
            + BANNER)
        c = s["crash"]
        self.assertEqual(c["phase"], "game")
        self.assertEqual(c["where"], "Crusader Industries")
        self.assertEqual(c["into_s"], 1170)
        self.assertEqual(c["last_err"],
                         [{"tag": "Failed attachment", "n": 3, "cls": "RSI_Polaris_Turret"}])

    def test_log_without_crash_mark_has_no_crash(self):
        s = self.scan(
            stamped("10:00:00", "Log started on Sun Jul 19 10:00:00 2026")
            + stamped("10:01:00", "[Notice] <Join PU> address[1.2.3.4]")
            + stamped("10:30:00", "[Notice] nothing went wrong"))
        self.assertIsNone(s["crash"])
        self.assertNotEqual(s["outcome"], "crash")

    def test_banner_before_the_tail_is_a_crash_without_context(self):
        # the log ran on for more than the tail after the banner: still a crash, as
        # before, but there is no report or context to read from the tail
        filler = "".join(stamped("10:02:00", "[Notice] filler %05d" % i) for i in range(2000))
        s = self.scan(
            stamped("10:00:00", "Log started on Sun Jul 19 10:00:00 2026")
            + stamped("10:01:00", "[Notice] before")
            + BANNER + filler)
        self.assertGreater(len(filler), sc_stats.CRASH_TAIL)
        self.assertEqual(s["crash"]["exception"], "UNKNOWN")
        self.assertNotIn("phase", s["crash"])


class EmbeddedDataTests(unittest.TestCase):
    def test_log_text_cannot_close_the_script(self):
        data = {"err": "</script><img src=x onerror=alert(1)>"}
        with mock.patch.object(sc_stats.sc_names, "fetch_google_fonts_embed", return_value=""):
            html = sc_stats.render_html(data)
        line = next(ln for ln in html.splitlines() if ln.startswith("const DATA = "))
        self.assertNotIn("</", line)
        payload = line[len("const DATA = "):].rstrip(";")
        self.assertEqual(json.loads(payload), data)


if __name__ == "__main__":
    unittest.main()
