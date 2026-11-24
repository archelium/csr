"""The Kestrel slate page: light, no mock data, no action hooks, the parts the live model fills."""
import os
import re
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_slate  # noqa: E402

MOCK_STRINGS = ("LtFreeman", "Xenthos", "Disturbed164", "PaleHorse", "Kaito_SC", "82,312,450", "archelium",
                "Shallow Fields", 'id="demo"', "Ihsanbasher", "SpaceDude7", "Kramada")


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = sc_slate.slate_html()

    def test_no_mock_data(self):
        for s in MOCK_STRINGS:
            self.assertNotIn(s, self.html, s)

    def test_size(self):
        self.assertLess(len(self.html), 450_000)
        for m in re.finditer(r"data:image[^\"')]+", self.html):
            self.assertLess(len(m.group(0)), 8192)

    def test_parts_present(self):
        for i in ('id="threat"', 'id="bezel"', 'id="page-ship"', 'id="hudsvg"', 'id="defsvg"', 'id="waitPanel"',
                  'scrBtn', 'bellBtn', "KESTREL"):
            self.assertIn(i, self.html, i)

    def test_foot_sys_you_parts_present(self):
        for i in ('id="bodyCard"', 'id="suitsvg"', 'id="lsSil"', 'id="page-sys"', 'id="page-you"', 'class="vgrid"'):
            self.assertIn(i, self.html, i)

    def test_no_foot_sys_you_demo_values(self):
        for s in ("40 m from you", "as of 12:16", "96 bpm", "2 h 14 min", "+79,500", "82.3 M", "57.5 km",
                  "Testudo set", "2 medpens", "hull 92%", "48 decoys", "Testudo · medium"):
            self.assertNotIn(s, self.html, s)

    def test_log_pages_and_empty_states(self):
        for i in ('id="page-msn"', 'id="page-feed"', 'id="page-nav"', 'id="page-ppl"',
                  "No contracts yet", "No notices yet", "No ships destroyed nearby"):
            self.assertIn(i, self.html, i)

    def test_no_party_or_demo_stops(self):
        for s in ('id="pplParty"', 'id="navParty"', "NAMES SEEN", 'Port Tressler<span class="r">', "ARC-L4 · 42 Gm", "42 min ago",
                  "partyRender()"):
            self.assertNotIn(s, self.html, s)

    def test_no_mock_starting_values(self):
        for v in ('id="spd">370<', 'id="bst">8%<', 'id="o2v">63%<', 'id="o2t">14 min<', "In space · no air",
                  "DECOYS <b>48</b>", "QT <b>100%</b>", "Marauder · top turret", "NIGHT VIS", 'id="mode">SCM<',
                  "${combat?'Ungoverned':'Armistice'}", "V/4.5", "v.o2/4.5", "STATE.master==='nav'?'NAV':'SCM'",
                  "total 61%", "quantum off"):
            self.assertNotIn(v, self.html, v)

    def test_filled_elements_start_without_numbers(self):
        for m in re.finditer(r'<\w+[^>]*\bid="([^"]+)"[^>]*>([^<]+)<', sc_slate.SLATE_BODY):
            self.assertFalse(re.search(r"\d", m.group(2)), m.group(1) + ": " + m.group(2))

    def test_deck_is_state_only(self):
        for s in ("SET MODE", "<small>emote</small>", "request pad", "v_toggle_", "v_set_", "act:'"):
            self.assertNotIn(s, self.html, s)

    def test_no_action_hooks(self):
        low = self.html.lower()
        self.assertNotIn("triggergameaction", low)
        self.assertNotIn("self-destruct", low)
        self.assertNotIn("v_eject", low)
        self.assertNotIn("(mock)", low)

    def test_fonts_placeholder(self):
        self.assertIn("/*__FONTS__*/", self.html)

    def test_polls_live_and_ship(self):
        self.assertIn("/api/telemetry/live", self.html)
        self.assertIn("/api/telemetry/ship?c=", self.html)


class SwitchOverTests(unittest.TestCase):
    def test_live_html_is_the_slate(self):
        import sc_stats
        with unittest.mock.patch.object(sc_stats.sc_names, "fetch_google_fonts_embed", lambda *a, **k: "/*fonts*/"):
            h = sc_stats.LIVE_HTML()
        self.assertIn("KESTREL", h)
        self.assertIn("/*fonts*/", h)
        self.assertNotIn("/*__FONTS__*/", h)
        self.assertNotIn("<title>CSR · Live</title>", h)

    def test_old_template_gone(self):
        import sc_stats
        self.assertFalse(hasattr(sc_stats, "LIVE_TEMPLATE"))


if __name__ == "__main__":
    unittest.main()
