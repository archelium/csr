"""The Kestrel slate page: light, no mock data, no action hooks, the parts the live model fills."""
import os
import re
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
