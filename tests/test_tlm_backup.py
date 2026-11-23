"""Backup and transfer: archive export with telemetry, zip round trip, import."""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402
import sc_tlm_stats as st  # noqa: E402
from tests.test_tlm_index import IndexBase, W2  # noqa: E402
from datetime import datetime  # noqa: E402


class BackupBase(IndexBase):
    def setUp(self):
        super().setUp()
        self.other = tempfile.TemporaryDirectory()
        self.addCleanup(self.other.cleanup)
        self.root_b = os.path.join(self.other.name, "telemetry")
        os.makedirs(self.root_b)
        self.arch = os.path.join(self.tmp.name, "arch.json")
        with open(self.arch, "w", encoding="utf-8") as f:
            json.dump({"v": 1, "app": "CSR", "version": "x", "saved": "now", "sessions": []}, f)
        self.rec(datetime(2026, 5, 1, 10, 0, 0), W2)
        self.rec(datetime(2026, 5, 2, 11, 0, 0), W2, channel="PTU")
        st.refresh_index(self.root)

    def payload(self, rec):
        return sc_stats.export_payload(rec, root=self.root, archive_file=self.arch)


class Export(BackupBase):
    def test_json_export_has_summaries(self):
        body, ctype, name = self.payload(False)
        obj = json.loads(body)
        self.assertEqual(ctype, "application/json")
        self.assertTrue(name.endswith(".json"))
        self.assertEqual({e["file"] for e in obj["telemetry"]},
                         set(st.load_index(self.root)))
        self.assertEqual(len(obj["telemetry"]), 2)

    def test_zip_members(self):
        body, ctype, name = self.payload(True)
        self.assertEqual(ctype, "application/zip")
        self.assertTrue(name.endswith(".zip"))
        names = zipfile.ZipFile(io.BytesIO(body)).namelist()
        self.assertEqual(sum(n.startswith("CSR-archive-") and n.endswith(".json") for n in names), 1)
        self.assertIn("telemetry/LIVE/20260501-100000.jsonl.gz", names)
        self.assertIn("telemetry/PTU/20260502-110000.jsonl.gz", names)


class Import(BackupBase):
    def test_zip_roundtrip(self):
        body, _c, _n = self.payload(True)
        zpath = os.path.join(self.other.name, "in.zip")
        with open(zpath, "wb") as f:
            f.write(body)
        res = sc_stats.import_zip_file(zpath, root=self.root_b, archive_file=None, merge=False)
        self.assertEqual(res["telemetry"], 2)
        self.assertEqual(set(st.load_index(self.root_b)), set(st.load_index(self.root)))
        self.assertEqual([os.path.relpath(p, self.root_b) for p in st.recording_files(self.root_b)],
                         [os.path.relpath(p, self.root) for p in st.recording_files(self.root)])

    def test_old_export_imports(self):
        self.assertEqual(sc_stats.import_telemetry(None, {}, root=self.root_b), 0)
        self.assertEqual(st.load_index(self.root_b), {})

    def test_import_skips_existing(self):
        entries = list(st.load_index(self.root).values())
        with open(st.recording_files(self.root)[0], "rb") as f:
            data = f.read()
        files = {"LIVE/20260501-100000.jsonl.gz": data}
        self.assertEqual(sc_stats.import_telemetry(entries, files, root=self.root_b), 2)
        self.assertEqual(sc_stats.import_telemetry(entries, files, root=self.root_b), 0)

    def test_zip_slip_ignored(self):
        zpath = os.path.join(self.other.name, "evil.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("CSR-archive-2026-01-01.json", json.dumps({"sessions": []}))
            z.writestr("telemetry/../../evil.jsonl.gz", b"x")
            z.writestr("/abs/x.jsonl.gz", b"x")
            z.writestr("telemetry/LIVE/..\evil.jsonl.gz", b"x")
            z.writestr("telemetry/LIVE/notes.txt", b"x")
            z.writestr("telemetry/LIVE/ok.jsonl.gz", b"x")
        res = sc_stats.import_zip_file(zpath, root=self.root_b, archive_file=None, merge=False)
        self.assertEqual(res["telemetry"], 1)
        self.assertEqual([d for d in os.listdir(self.root_b) if os.path.isdir(os.path.join(self.root_b, d))], ["LIVE"])
        self.assertEqual(os.listdir(os.path.join(self.root_b, "LIVE")), ["ok.jsonl.gz"])
        self.assertFalse(os.path.exists(os.path.join(self.other.name, "evil.jsonl.gz")))

    def test_zip_without_archive_json_fails(self):
        zpath = os.path.join(self.other.name, "bad.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("hello.txt", b"x")
        with self.assertRaises(ValueError):
            sc_stats.import_zip_file(zpath, root=self.root_b, archive_file=None, merge=False)


if __name__ == "__main__":
    unittest.main()
