import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sc_names  # noqa: E402
import sc_stats  # noqa: E402


class ArtCacheTests(unittest.TestCase):
    def test_same_source_uses_the_cache(self):
        cache = {"Galaxy": ["https://a/old.jpg", "data:old"]}
        got = sc_stats._art(cache, "Galaxy", "https://a/old.jpg", lambda: self.fail("fetched"))
        self.assertEqual(got, "data:old")

    def test_new_source_refetches(self):
        cache = {"Galaxy": ["https://a/old.jpg", "data:old"]}
        got = sc_stats._art(cache, "Galaxy", "https://a/new.jpg", lambda: "data:new")
        self.assertEqual(got, "data:new")
        self.assertEqual(cache["Galaxy"], ["https://a/new.jpg", "data:new"])

    def test_legacy_entry_is_refetched(self):
        cache = {"Galaxy": "data:old"}
        self.assertEqual(sc_stats._art(cache, "Galaxy", "https://a/new.jpg", lambda: "data:new"),
                         "data:new")

    def test_failed_refetch_keeps_the_old_picture_and_retries(self):
        cache = {"Galaxy": ["https://a/old.jpg", "data:old"]}
        self.assertEqual(sc_stats._art(cache, "Galaxy", "https://a/new.jpg", lambda: None), "data:old")
        self.assertEqual(cache["Galaxy"][0], "https://a/old.jpg")

    def test_miss_is_retried_only_when_asked(self):
        cache = {"Arlington Rifle": ["page", None]}
        self.assertIsNone(sc_stats._art(cache, "Arlington Rifle", "page", lambda: "data:x"))
        self.assertEqual(sc_stats._art(cache, "Arlington Rifle", "page", lambda: "data:x",
                                       retry_miss=True), "data:x")


class ItemIndexTests(unittest.TestCase):
    def _index(self, raw):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(raw, fh)
        it = sc_names.ItemIndex(cache=path)
        it.load(allow_fetch=False)
        return it

    def test_dated_format_loads_names_and_guns(self):
        it = self._index({"fetched": "2026-11-23",
                          "names": {"hdgw_rifle_ballistic_01": "Arlington Rifle"},
                          "guns": {"hdgw_rifle_ballistic_01": ["Rifle", "Ballistic", "https://x/a.webp"]}})
        self.assertEqual(it.fetched, "2026-11-23")
        self.assertEqual(it.gun("hdgw_rifle_ballistic_01_black01"), ["Rifle", "Ballistic", "https://x/a.webp"])

    def test_bare_name_map_still_loads(self):
        it = self._index({"behr_rifle_ballistic_01": "P4-AR Rifle"})
        self.assertTrue(it.ok)
        self.assertIsNone(it.fetched)
        self.assertIsNone(it.gun("behr_rifle_ballistic_01"))

    def test_game_typos_are_fixed(self):
        it = self._index({"qrt_specialist_heavy_helmet_01_01_12": "Antium Core Jet"})
        self.assertEqual(it.idx["qrt_specialist_heavy_helmet_01_01_12"], "Antium Helmet Jet")

    def test_empty_index_stays_offline(self):
        it = self._index({})
        self.assertFalse(it.ok)

    def test_gun_type_comes_from_the_wiki(self):
        it = self._index({"fetched": "2026-11-23", "names": {"hdgw_rifle_ballistic_01": "Arlington Rifle"},
                          "guns": {"hdgw_rifle_ballistic_01": ["Rifle", "Ballistic", ""]}})
        with mock.patch.object(sc_names, "ITEMS", it):
            self.assertEqual(sc_names.gun_class_labels("hdgw_rifle_ballistic_01", "Arlington Rifle"),
                             ("Rifle", "Ballistic"))


class NameTests(unittest.TestCase):
    def test_placeholder_names_are_rejected(self):
        self.assertFalse(sc_names._real_name("volt_ubarrel_flsh_s1_sniperglint",
                                             "volt_ubarrel_flsh_s1_sniperglint"))
        self.assertFalse(sc_names._real_name("x", "@item_NameX"))
        self.assertTrue(sc_names._real_name("hdgw_rifle_ballistic_01", "Arlington Rifle"))

    def test_single_quoted_skin_is_stripped(self):
        self.assertEqual(sc_names._skin_strip("Deadrig 'Noxious' Shotgun"), "Deadrig Shotgun")
        self.assertEqual(sc_names._skin_strip("Chairman's Club"), "Chairman's Club")


if __name__ == "__main__":
    unittest.main()
