import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import sc_stats  # noqa: E402

TOOLS = "https://media.starcitizen.tools"


class ArtSourceTests(unittest.TestCase):
    def test_wiki_thumbnail_and_original_give_the_file(self):
        self.assertEqual(sc_stats.art_src(f"{TOOLS}/thumb/c/cb/Medpen_-_SCT_BG.png/600px-Medpen_-_SCT_BG.png.webp?f0r5z"),
                         ["w", "c/cb/Medpen_-_SCT_BG.png"])
        self.assertEqual(sc_stats.art_src(f"{TOOLS}/0/07/Cargo_-_Waste.png?t5h0b"), ["w", "0/07/Cargo_-_Waste.png"])

    def test_rsi_record_and_other_hosts(self):
        self.assertEqual(sc_stats.art_src({"img_small": "s.jpg", "img_large": "l.jpg"}), ["r", "s.jpg", "l.jpg"])
        self.assertEqual(sc_stats.art_src({"img_large": "l.jpg"}), ["r", "l.jpg", "l.jpg"])
        self.assertEqual(sc_stats.art_src("https://cstone.space/x.png"), ["u", "https://cstone.space/x.png"])

    def test_nothing(self):
        self.assertIsNone(sc_stats.art_src(None))
        self.assertIsNone(sc_stats.art_src({"img_small": "s.jpg"}))


class PackImagesTests(unittest.TestCase):
    def test_each_picture_is_stored_once(self):
        img = "data:image/jpeg;base64," + "A" * 400
        data = {"showcase": {"all": {"ships": [{"img": img}]}, "4.10": {"ships": [{"img": img}]}},
                "note": "data:image/x;short"}
        packed = sc_stats._pack_images(data)
        self.assertEqual(packed["ART"], [img])
        tag = sc_stats._ART_TAG + "0"
        self.assertEqual(packed["showcase"]["all"]["ships"][0]["img"], tag)
        self.assertEqual(packed["showcase"]["4.10"]["ships"][0]["img"], tag)
        self.assertEqual(packed["note"], "data:image/x;short")     # too short to be a picture

    def test_no_pictures_no_store(self):
        self.assertNotIn("ART", sc_stats._pack_images({"a": 1}))

    def test_tag_survives_json(self):
        img = "data:image/png;base64," + "B" * 300
        s = json.dumps(sc_stats._pack_images({"x": img}))
        self.assertIn("\\u0001art:0", s)


class ViewListTests(unittest.TestCase):
    def test_lists_in_display_order(self):
        v = {"ships_top": [["Asgard", 3]], "weapons_top": [["Gallant Rifle", 9, False], ["Multi-Tool", 4, True]],
             "haul": {"dest": [["Nyx System", 5], ["Everus Harbor", 3]], "com": [["Waste", 7]]}}
        lists = dict((k + str(i), ns) for i, (k, ns) in enumerate(sc_stats._view_lists(v)))
        self.assertEqual(lists["ship0"], ["Asgard"])
        self.assertEqual(lists["gun4"], ["Gallant Rifle"])
        self.assertEqual(lists["item5"], ["Multi-Tool"])
        self.assertEqual(lists["place9"], ["Nyx System", "Everus Harbor"])
        self.assertTrue(sc_stats._is_system("Nyx System"))
        self.assertFalse(sc_stats._is_system("Everus Harbor"))


if __name__ == "__main__":
    unittest.main()
