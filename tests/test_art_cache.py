import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sc_names  # noqa: E402
import sc_stats  # noqa: E402


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


class GunPictureTests(unittest.TestCase):
    def test_action_shots_are_never_used(self):
        for f in ("Arlington Rifle in rifle mode with Myers (4x Telescopic), side.png",
                  "Parallax AR - Inspecting weapon.jpg", "Gallant - Aiming.jpg",
                  "VOLT-pulse-pistol-sneak-peek.jpg", "Apar ML Shot 002 11Dec19-Min.jpg",
                  "APAR Animus mag 01.jpg"):
            self.assertLess(sc_names.gun_image_score(f), 0, f)

    def test_bad_words_match_whole_words_only(self):
        self.assertGreaterEqual(sc_names.gun_image_score("Deadrig Shotgun.jpg"), 0)
        self.assertGreaterEqual(sc_names.gun_image_score("Devastator - with 2 clips - Flat.jpg"), 0)

    def test_flat_lay_beats_shop_display(self):
        self.assertGreater(sc_names.gun_image_score("Gallant - with 3 clips and ArcLight - Flat.png"),
                           sc_names.gun_image_score("Gallant - Standard skin - Shop display.jpg"))

    def _pick(self, name, api=None, wiki=None, wiki0=None, pin_url=None):
        sc_stats._GUN_PICK.pop(name, None)
        self.addCleanup(sc_stats._GUN_PICK.pop, name, None)
        with mock.patch.object(sc_names, "wiki_gun_image",
                               side_effect=lambda t, min_score=1: wiki if min_score == 1 else wiki0), \
                mock.patch.object(sc_names, "wiki_file_logo", return_value=pin_url),                 mock.patch.object(sc_names, "wiki_weapon_image", return_value=None):
            return sc_stats._gun_pick(name, api)

    def test_pinned_file_wins(self):
        self.assertEqual(self._pick("Quartz Energy SMG", wiki="https://w/flat.jpg",
                                    pin_url="https://w/pinned.jpg"), "https://w/pinned.jpg")

    def test_page_product_shot_beats_catalogue_image(self):
        self.assertEqual(self._pick("Arlington Rifle", api="https://w/600px-Arlington.png",
                                    wiki="https://w/flat.jpg"), "https://w/flat.jpg")

    def test_catalogue_action_shot_is_skipped(self):
        api = ("https://w/thumb/600px-Arlington_Rifle_in_rifle_mode_with_Myers_"
               "%284x_Telescopic%29%2C_side.png.webp?ej1lz")
        self.assertEqual(self._pick("Arlington Rifle", api=api, wiki0="https://w/410.jpg"),
                         "https://w/410.jpg")

    def test_nothing_found_is_not_saved(self):
        self.assertIsNone(self._pick("Nothing Rifle"))
        self.assertNotIn("Nothing Rifle", sc_stats._GUN_PICK)


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
