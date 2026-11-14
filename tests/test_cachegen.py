"""PSOCacheGen logs are shader-cache runs, not play sessions."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

GAME = "Game Build(12913470) 05 Nov 26 (10 00 00).log"
CACHE = "PSOCacheGen Build(12913470) 05 Nov 26 (12 18 53).log"


def touch(folder, name):
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("x\n")
    return p


def sess(src, stamp):
    return {"_src": src, "channel": "LIVE", "handle": "me",
            "fp": [stamp, stamp, 10], "start": "2026-11-05T12:00:00"}


class CacheGenTests(unittest.TestCase):
    def test_channel_files(self):
        with tempfile.TemporaryDirectory() as d:
            lb = os.path.join(d, "LIVE", "logbackups")
            touch(lb, GAME)
            touch(lb, CACHE)
            touch(lb, CACHE.replace("PSO", "pso"))
            files, _ = sc_stats._channel_files(os.path.join(d, "LIVE"))
            self.assertEqual([os.path.basename(f) for f in files], [GAME])

    def test_find_logs_recursive(self):
        with tempfile.TemporaryDirectory() as d:
            touch(os.path.join(d, "a"), GAME)
            touch(os.path.join(d, "a", "b"), CACHE)
            found = sc_stats.find_logs_recursive(d)
            self.assertEqual([os.path.basename(f) for f in found], [GAME])

    def test_explicit_logs(self):
        with tempfile.TemporaryDirectory() as d:
            lb = os.path.join(d, "logbackups")
            touch(lb, GAME)
            touch(lb, CACHE)
            ch = sc_stats.resolve_channels(explicit_logs=lb)
            self.assertEqual([os.path.basename(f) for f in ch[0][1]], [GAME])

    def test_merge_drops_archived_cachegen(self):
        a, b = sess(GAME, "2026-11-05T10:00:00"), sess(CACHE, "2026-11-05T12:18:53")
        self.assertEqual(sc_stats.merge_sessions([a, b]), [a])

    def test_load_archive_drops_archived_cachegen(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "archive.json")
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"sessions": [sess(GAME, "2026-11-05T10:00:00"),
                                        sess(CACHE, "2026-11-05T12:18:53")]}, fh)
            with mock.patch.object(sc_stats, "archive_path", return_value=p):
                got = sc_stats.load_archive()
            self.assertEqual([s["_src"] for s in got], [GAME])


if __name__ == "__main__":
    unittest.main()
