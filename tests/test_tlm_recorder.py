"""Telemetry recorder thread: connect, record, reconnect, give up."""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.tlm_helpers import StreamServer, actor_frame, exe_bytes, free_port, make_descriptor  # noqa: E402
import sc_telemetry as tlm  # noqa: E402


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class Rig:
    """Temp root, fake game install, switchable settings and game flag."""

    def __init__(self, test, with_exe=True):
        self.tmp = tempfile.TemporaryDirectory()
        test.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "telemetry")
        self.chan = os.path.join(self.tmp.name, "LIVE")
        os.makedirs(os.path.join(self.chan, "Bin64"))
        if with_exe:
            with open(os.path.join(self.chan, "Bin64", "StarCitizen.exe"), "wb") as fh:
                fh.write(exe_bytes(make_descriptor()))
        with open(os.path.join(self.chan, "build_manifest.id"), "w") as fh:
            json.dump({"Data": {"Version": "4.10.201.48085"}}, fh)
        self.running = True
        self.settings = {"record": True, "detail": "full", "groups": list(tlm.DEFAULT_GROUPS), "csr": "9.9"}
        self.closed = []
        self.rec = None
        test.addCleanup(self.stop)

    def make(self, port):
        self.rec = tlm.Recorder(self.root, lambda: dict(self.settings), lambda: self.running,
                                lambda: ("LIVE", self.chan), self.closed.append,
                                host="127.0.0.1", port=port)
        return self.rec

    def stop(self):
        if self.rec:
            self.rec.stop()


def frames(n, base=100):
    return [actor_frame(uec=base + i, health=50.0 + i) for i in range(n)]


class RecorderTests(unittest.TestCase):
    def setUp(self):
        for name, v in (("RETRY_S", 0.05), ("OFF_AFTER_S", 0.3), ("POLL_S", 0.05), ("IDLE_S", 0.05)):
            p = mock.patch.object(tlm, name, v)
            p.start()
            self.addCleanup(p.stop)

    def test_records_and_closes_on_game_exit(self):
        rig = Rig(self)
        with StreamServer([{"frames": frames(20), "gap": 0.01, "end": "hold"}]) as srv:
            rec = rig.make(srv.port)
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["frames"] >= 20))
            self.assertTrue(rec.connected)
            rig.running = False
            self.assertTrue(wait_for(lambda: len(rig.closed) == 1))
            self.assertTrue(wait_for(lambda: rec.status()["state"] == "idle"))
        self.assertEqual(len(rig.closed), 1)
        header, rows = tlm.read_recording(rig.closed[0])
        self.assertEqual(header["channel"], "LIVE")
        self.assertEqual(header["build"], "4.10.201.48085")
        self.assertEqual(header["v"], 1)
        self.assertEqual(header["csr"], "9.9")
        self.assertEqual(header["detail"], "full")
        self.assertTrue(header["started"] and header["started_utc"] and header["schema"])
        self.assertTrue(rows)
        self.assertEqual(os.path.dirname(rig.closed[0]), os.path.join(rig.root, "LIVE"))
        self.assertTrue(rig.closed[0].endswith(".jsonl.gz"))
        self.assertEqual(len(os.listdir(os.path.join(rig.root, "LIVE"))), 1)

    def test_reconnect_starts_new_file(self):
        rig = Rig(self)
        plans = [{"frames": frames(5), "gap": 0.01, "end": "drop"},
                 {"frames": frames(5, 500), "gap": 0.01, "end": "hold"}]
        with StreamServer(plans) as srv:
            rec = rig.make(srv.port)
            rec.start()
            self.assertTrue(wait_for(lambda: len(rig.closed) == 1))
            self.assertTrue(wait_for(lambda: srv.accepted == 2 and rec.status()["frames"] >= 5))
            rig.running = False
            self.assertTrue(wait_for(lambda: len(rig.closed) == 2))
        self.assertEqual(len(set(rig.closed)), 2)
        for p in rig.closed:
            self.assertTrue(os.path.isfile(p))
            self.assertTrue(tlm.read_recording(p)[1])

    def test_record_off_still_live(self):
        rig = Rig(self)
        rig.settings["record"] = False
        with StreamServer([{"frames": [actor_frame(7, 12.0, event=6)], "gap": 0.01, "end": "hold"}]) as srv:
            rec = rig.make(srv.port)
            rec.start()
            self.assertTrue(wait_for(lambda: rec.live()["snapshot"].get("actor")))
            lv = rec.live()
            self.assertEqual(lv["state"], "live")
            self.assertEqual(lv["channel"], "LIVE")
            self.assertEqual(lv["reason"], rec.status()["reason"])
            self.assertEqual(lv["snapshot"]["actor"]["state"]["uec_balance"], 7)
            self.assertNotIn("events", lv["snapshot"])
            self.assertGreater(lv["t"], time.time() - 5)
            self.assertTrue(lv["events"])
            self.assertIn("ACTOR_DOWNED", lv["events"][-1]["type"])
            self.assertLessEqual(len(lv["events"]), 50)
            self.assertEqual(rec.status()["file"], "")
        self.assertEqual(rig.closed, [])
        self.assertFalse(os.path.exists(os.path.join(rig.root, "LIVE")))

    def test_off_after_refusals(self):
        rig = Rig(self)
        port = free_port()
        attempts = []

        def refused(*a, **k):   # a real refusal takes about 2 s on Windows
            attempts.append(1)
            raise ConnectionRefusedError("refused")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(tlm, "GrpcStream", refused), redirect_stdout(out), redirect_stderr(err):
            rec = rig.make(port)
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["state"] == "off", 1.0))
            self.assertEqual(rec.status()["reason"], "port")
            rec.stop()
        self.assertGreaterEqual(len(attempts), 2)
        self.assertLessEqual(len(attempts), 25)
        self.assertEqual(out.getvalue() + err.getvalue(), "")

    def test_waiting_then_live(self):
        rig = Rig(self)
        port = free_port()
        rec = rig.make(port)
        rec.start()
        self.assertTrue(wait_for(lambda: rec.status()["state"] == "waiting"))
        time.sleep(0.2)
        self.assertEqual(rec.status()["state"], "waiting")
        with mock.patch.object(tlm, "OFF_AFTER_S", 60.0):
            with StreamServer([{"frames": frames(5), "gap": 0.01, "end": "hold"}], port=port):
                self.assertTrue(wait_for(lambda: rec.status()["state"] == "live"))
                self.assertTrue(rec.connected)

    def test_record_toggle_applies_now(self):
        rig = Rig(self)
        with StreamServer([{"frames": frames(5), "gap": 0.01, "end": "hold"}]) as srv:
            rec = rig.make(srv.port)
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["file"] != ""))
            rig.settings["record"] = False
            self.assertTrue(wait_for(lambda: len(rig.closed) == 1, 1.5))
            self.assertEqual(rec.status()["file"], "")
            f0 = rec.status()["frames"]
            self.assertTrue(wait_for(lambda: rec.status()["frames"] > f0 + 3))
            self.assertEqual(len(rig.closed), 1)
            rig.settings["record"] = True
            self.assertTrue(wait_for(lambda: rec.status()["file"] != ""))
            self.assertTrue(wait_for(lambda: rec.status()["bytes"] > 0))
            rig.settings["record"] = False
            self.assertTrue(wait_for(lambda: len(rig.closed) == 2, 1.5))
        self.assertEqual(len(set(rig.closed)), 2)

    def test_no_schema(self):
        rig = Rig(self, with_exe=False)
        attempts = []
        with mock.patch.object(tlm, "GrpcStream", lambda *a, **k: attempts.append(1)):
            rec = rig.make(free_port())
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["state"] == "off"))
            time.sleep(0.2)
            st = rec.status()
        self.assertEqual(st["reason"], "schema")
        self.assertEqual(attempts, [])

    def test_stop_closes_open_file(self):
        rig = Rig(self)
        with StreamServer([{"frames": frames(5), "gap": 0.01, "end": "hold"}]) as srv:
            rec = rig.make(srv.port)
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["file"] != ""))
            t0 = time.time()
            rec.stop()
            self.assertLess(time.time() - t0, 2.0)
        self.assertEqual(len(rig.closed), 1)
        self.assertFalse(rec.connected)
        self.assertFalse(any(t.name == "tlm-recorder" and t.is_alive() for t in threading.enumerate()))

    def test_idle_when_game_not_running(self):
        rig = Rig(self)
        rig.running = False
        attempts = []
        with mock.patch.object(tlm, "GrpcStream", lambda *a, **k: attempts.append(1)):
            rec = rig.make(free_port())
            rec.start()
            time.sleep(0.3)
        self.assertEqual(rec.status()["state"], "idle")
        self.assertEqual(attempts, [])

    def test_loop_survives_errors(self):
        rig = Rig(self)
        calls = []

        def boom():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("x")
            return True

        with mock.patch.object(tlm, "ERROR_BACKOFF_S", 0.05), \
                StreamServer([{"frames": frames(5), "gap": 0.01, "end": "hold"}]) as srv:
            rec = tlm.Recorder(rig.root, lambda: dict(rig.settings), boom,
                               lambda: ("LIVE", rig.chan), rig.closed.append, port=srv.port)
            rig.rec = rec
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["state"] == "live"))


    def test_silent_stream_record_off_applies(self):
        rig = Rig(self)
        with StreamServer([{"frames": frames(3), "gap": 0.01, "end": "silent"}]) as srv:
            rec = rig.make(srv.port)
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["file"] != ""))
            time.sleep(0.2)
            rig.settings["record"] = False
            self.assertTrue(wait_for(lambda: len(rig.closed) == 1, 1.5))
            self.assertEqual(rec.status()["state"], "live")

    def test_silent_stream_game_exit_goes_idle(self):
        rig = Rig(self)
        with StreamServer([{"frames": frames(3), "gap": 0.01, "end": "silent"}]) as srv:
            rec = rig.make(srv.port)
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["file"] != ""))
            time.sleep(0.2)
            rig.running = False
            self.assertTrue(wait_for(lambda: len(rig.closed) == 1, 1.5))
            self.assertTrue(wait_for(lambda: rec.status()["state"] == "idle", 1.5))
            self.assertFalse(rec.connected)

    def test_silent_past_limit_reconnects(self):
        rig = Rig(self)
        plans = [{"frames": frames(3), "gap": 0.01, "end": "silent"},
                 {"frames": frames(3, 900), "gap": 0.01, "end": "silent"}]
        with mock.patch.object(tlm, "SILENT_S", 0.4), StreamServer(plans) as srv:
            rec = rig.make(srv.port)
            rec.start()
            self.assertTrue(wait_for(lambda: len(rig.closed) == 1, 3.0))
            self.assertTrue(wait_for(lambda: rec.status()["state"] == "waiting", 1.0))
            self.assertTrue(wait_for(lambda: srv.accepted == 2, 3.0))
            self.assertTrue(wait_for(lambda: len(rig.closed) == 2, 3.0))

    def test_channel_none_is_waiting(self):
        rig = Rig(self)
        rec = tlm.Recorder(rig.root, lambda: dict(rig.settings), lambda: True,
                           lambda: None, rig.closed.append, port=free_port())
        rig.rec = rec
        rec.start()
        self.assertTrue(wait_for(lambda: rec.status()["state"] == "waiting"))
        self.assertEqual(rec.status()["reason"], "channel")

    def test_waiting_before_first_connect(self):
        rig = Rig(self)
        seen = []

        def slow(*a, **k):
            seen.append(rec.status()["state"])
            raise ConnectionRefusedError("x")

        with mock.patch.object(tlm, "GrpcStream", slow):
            rec = rig.make(free_port())
            rec.start()
            self.assertTrue(wait_for(lambda: seen))
        self.assertEqual(seen[0], "waiting")

    def test_write_failure_stops_recording_this_connection(self):
        rig = Rig(self)
        opened = []
        real = tlm.RecordingWriter

        class Bad(real):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                opened.append(1)

            def add(self, *a, **k):
                raise OSError("disk")

        with mock.patch.object(tlm, "RecordingWriter", Bad), \
                StreamServer([{"frames": frames(5), "gap": 0.01, "end": "hold"}]) as srv:
            rec = rig.make(srv.port)
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["frames"] >= 15))
        self.assertEqual(len(opened), 1)
        self.assertEqual(len(rig.closed), 1)

    def test_header_started_matches_filename(self):
        rig = Rig(self)
        os.makedirs(os.path.join(rig.root, "LIVE"))
        with StreamServer([{"frames": frames(3), "gap": 0.01, "end": "hold"}]) as srv:
            rec = rig.make(srv.port)
            real_now = tlm.datetime
            # occupy this second's and the next second's file names
            stamp = real_now.now()
            for d in (0, 1, 2):
                n = (stamp + tlm.timedelta(seconds=d)).strftime("%Y%m%d-%H%M%S")
                open(os.path.join(rig.root, "LIVE", n + ".jsonl.gz"), "w").close()
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["file"] != ""))
            path = rec.status()["file"]
            rec.stop()
        header, _ = tlm.read_recording(path)
        name = os.path.basename(path)[:-len(".jsonl.gz")]
        self.assertEqual(header["started"].replace("-", "").replace(":", "").replace("T", "-"), name)
        self.assertNotIn("+", header["started"])

    def test_start_after_stop_runs(self):
        rig = Rig(self)
        with StreamServer([{"frames": frames(3), "gap": 0.01, "end": "hold"}]) as srv:
            rec = rig.make(srv.port)
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["state"] == "live"))
            rec.stop()
            rec.start()
            self.assertTrue(wait_for(lambda: rec.status()["state"] == "live"))
            self.assertTrue(rec.connected)


if __name__ == "__main__":
    unittest.main()
