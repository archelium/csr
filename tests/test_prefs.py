"""Pins and main accounts saved in the data folder, merged on import."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402


class PrefsFileTests(unittest.TestCase):
    def setUp(self):
        self._old = sc_stats._APP_DIR
        self.tmp = tempfile.mkdtemp()
        sc_stats._APP_DIR = self.tmp

    def tearDown(self):
        sc_stats._APP_DIR = self._old

    def test_round_trip(self):
        self.assertEqual(sc_stats.load_prefs(), {"pins": {}, "main": {}})
        sc_stats.save_prefs({"pins": {"archelium": ["BP_A"]}, "main": {"LIVE": "archelium"}})
        self.assertEqual(sc_stats.load_prefs(), {"pins": {"archelium": ["BP_A"]}, "main": {"LIVE": "archelium"}})

    def test_bad_file_reads_empty(self):
        open(sc_stats.prefs_path(), "w").write("not json")
        self.assertEqual(sc_stats.load_prefs(), {"pins": {}, "main": {}})


class MergeTests(unittest.TestCase):
    def test_pins_are_added_in_order(self):
        m = sc_stats.merge_prefs({"pins": {"a": ["X", "Y"]}}, {"pins": {"a": ["Y", "Z"], "b": ["Q"]}})
        self.assertEqual(m["pins"], {"a": ["X", "Y", "Z"], "b": ["Q"]})

    def test_main_account_is_kept(self):
        m = sc_stats.merge_prefs({"main": {"LIVE": "me"}}, {"main": {"LIVE": "other", "PTU": "me"}})
        self.assertEqual(m["main"], {"LIVE": "me", "PTU": "me"})

    def test_junk_is_ignored(self):
        m = sc_stats.merge_prefs({}, {"pins": {"a": "nope", "b": [1, "K"]}, "main": {"LIVE": 3}})
        self.assertEqual(m, {"pins": {"b": ["K"]}, "main": {}})

    def test_script_is_safe(self):
        self.assertNotIn("</", sc_stats.prefs_script({"pins": {"</script>": []}, "main": {}}))


if __name__ == "__main__":
    unittest.main()
