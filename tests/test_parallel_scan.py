"""A full re-read parses logs in worker processes and folds them in file order."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402


def write_log(folder, n, dup_of=None):
    """A minimal session: start, a server join, a quit. dup_of copies another log's
    lines, so the two share a fingerprint."""
    k = dup_of if dup_of is not None else n
    name = f"Game Build(12913470) {1 + n % 28:02d} Oct 26 ({n % 24:02d} 00 00).log"
    day = f"2026-10-{1 + k % 28:02d}"
    lines = [
        f"<{day}T10:00:00.000Z> Log started on Thu Oct 01 10:00:00 2026",
        f"<{day}T10:00:00.000Z> FileVersion: 4.10.197.2878",
        f"<{day}T10:00:00.000Z> [Trace] @env_session:  'pub-sc-alpha-4102-12881860'",
        f"<{day}T10:00:{k % 60:02d}.000Z> Loading screen for pu : SC_Frontend closed after {30 + k}.0 seconds",
        f"<{day}T11:{k % 60:02d}:00.000Z> [Notice] <SystemQuit> CSystem::Quit invoked with - cause=30016, reason=Quit via console command, exitCode=0",
    ]
    os.makedirs(folder, exist_ok=True)
    p = os.path.join(folder, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return p


class ParallelScanTests(unittest.TestCase):
    def setUp(self):
        for name in ("cstep", "csub"):
            p = mock.patch.object(sc_stats, name)
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tempfile.TemporaryDirectory()
        lb = os.path.join(self.tmp.name, "LIVE", "logbackups")
        self.files = [write_log(lb, n) for n in range(30)]
        self.files.append(write_log(os.path.join(self.tmp.name, "copy"), 40, dup_of=3))
        self.channels = [("LIVE", self.files, None)]

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, pool_min):
        with mock.patch.object(sc_stats, "SCAN_POOL_MIN", pool_min), \
             mock.patch.object(sc_stats, "set_progress"), mock.patch.object(sc_stats, "cprogress"):
            return sc_stats.scan_channels(self.channels)

    def test_pool_matches_one_process(self):
        one, drop1 = self.scan(10 ** 9)
        pool, drop2 = self.scan(2)
        self.assertEqual(len(one), 30)
        self.assertEqual(drop1, 1)
        self.assertEqual(drop2, 1)
        self.assertEqual([s["_src"] for s in pool], [s["_src"] for s in one])
        self.assertEqual([s["fp"] for s in pool], [s["fp"] for s in one])
        self.assertEqual([s["loads"] for s in pool], [s["loads"] for s in one])

    def test_broken_pool_falls_back(self):
        class Boom:
            def __init__(self, *a, **k):
                raise OSError("no processes here")
        import concurrent.futures as cf
        with mock.patch.object(cf, "ProcessPoolExecutor", Boom):
            got, dropped = self.scan(2)
        self.assertEqual(len(got), 30)
        self.assertEqual(dropped, 1)

    def test_copy_in_another_channel_parses_once(self):
        import shutil
        ptu = os.path.join(self.tmp.name, "PTU", "logbackups")
        os.makedirs(ptu)
        src = self.files[5]
        dst = os.path.join(ptu, os.path.basename(src))
        shutil.copy2(src, dst)                 # same name, size and modified time
        real = sc_stats.scan_log
        calls = []
        with mock.patch.object(sc_stats, "scan_log", side_effect=lambda p: calls.append(p) or real(p)), \
             mock.patch.object(sc_stats, "SCAN_POOL_MIN", 10 ** 9), \
             mock.patch.object(sc_stats, "set_progress"), mock.patch.object(sc_stats, "cprogress"):
            got, dropped = sc_stats.scan_channels([("LIVE", self.files[:30], None), ("PTU", [dst], None)])
        self.assertNotIn(dst, calls)
        self.assertEqual(len(calls), 30)
        self.assertEqual(len(got), 30)
        self.assertEqual(dropped, 1)

    def test_reused_sessions_skip_the_parse(self):
        one, _ = self.scan(10 ** 9)
        reuse = {sc_stats._file_key(os.path.basename(p), int(os.stat(p).st_mtime), os.stat(p).st_size): s
                 for p, s in zip(self.files, one)}
        with mock.patch.object(sc_stats, "scan_log", side_effect=AssertionError("parsed")), \
             mock.patch.object(sc_stats, "set_progress"), mock.patch.object(sc_stats, "cprogress"):
            got, _ = sc_stats.scan_channels([("LIVE", self.files[:30], None)], reuse=reuse)
        self.assertEqual(len(got), 30)


if __name__ == "__main__":
    unittest.main()
