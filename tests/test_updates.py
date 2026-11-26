"""New-version check: version order and which channel shows."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402


def rel(tag, pre=False, draft=False):
    return {"tag": tag, "prerelease": pre, "draft": draft,
            "url": f"https://github.com/archelium/csr/releases/tag/{tag}", "date": "2026-11-26T00:00:00Z"}


class VersionKeyTests(unittest.TestCase):
    def test_order(self):
        k = sc_stats.version_key
        self.assertLess(k("1.11.0"), k("2.0.0-beta"))
        self.assertLess(k("2.0.0-beta"), k("2.0.0-beta.2"))
        self.assertLess(k("2.0.0-beta.2"), k("2.0.0"))
        self.assertLess(k("2.0.0-alpha"), k("2.0.0-beta"))
        self.assertLess(k("1.9.3"), k("1.10.0"))
        self.assertEqual(k("v1.11.0"), k("1.11.0"))
        self.assertIsNone(k("latest"))
        self.assertIsNone(k(None))


class UpdateInfoTests(unittest.TestCase):
    def test_stable_user_sees_both(self):
        u = sc_stats.update_info([rel("v1.12.0"), rel("v2.0.0-beta", pre=True), rel("v1.11.0")], "1.11.0")
        self.assertEqual(u["stable"]["version"], "1.12.0")
        self.assertEqual(u["beta"]["version"], "2.0.0-beta")

    def test_up_to_date(self):
        u = sc_stats.update_info([rel("v1.11.0"), rel("v1.10.0")], "1.11.0")
        self.assertIsNone(u["stable"])
        self.assertIsNone(u["beta"])

    def test_old_beta_hidden_once_stable_passes_it(self):
        u = sc_stats.update_info([rel("v2.0.0"), rel("v2.0.0-beta", pre=True)], "1.11.0")
        self.assertEqual(u["stable"]["version"], "2.0.0")
        self.assertIsNone(u["beta"])

    def test_beta_user(self):
        rs = [rel("v2.0.0-beta.2", pre=True), rel("v2.0.0-beta", pre=True), rel("v1.11.0")]
        u = sc_stats.update_info(rs, "2.0.0-beta")
        self.assertIsNone(u["stable"])                      # 1.11.0 is older than the beta
        self.assertEqual(u["beta"]["version"], "2.0.0-beta.2")
        u = sc_stats.update_info([rel("v2.0.0")] + rs, "2.0.0-beta")
        self.assertEqual(u["stable"]["version"], "2.0.0")
        self.assertIsNone(u["beta"])

    def test_drafts_and_junk_ignored(self):
        u = sc_stats.update_info([rel("v9.0.0", draft=True), {"tag": "nightly"}, None, rel("v1.11.0")], "1.11.0")
        self.assertIsNone(u["stable"])
        self.assertIsNone(u["beta"])

    def test_no_data(self):
        self.assertEqual(sc_stats.update_info(None, "2.0.0-beta"),
                         {"current": "2.0.0-beta", "stable": None, "beta": None})


if __name__ == "__main__":
    unittest.main()
