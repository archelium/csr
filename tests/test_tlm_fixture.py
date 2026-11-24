"""Real 4.10 LIVE captures replayed through the recorder and summarized.

Fixtures (balance set to 1,000,000 by tools/make_tlm_fixture.py):
- live-4.10-takeoff.bin: Drake Marauder, landed, take-off, gear up, boost, NAV.
- live-4.10-landing.bin: SCM flight, touchdown with a one-frame 56.5 G contact spike,
  power-down, leave seat, on foot.
"""
import json
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.tlm_helpers import FakeGrpcServer, exe_bytes, read_desc, read_raw  # noqa: E402
import sc_telemetry as tlm  # noqa: E402
import sc_tlm_stats as st  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "telemetry")
UEC = 1000000
G_LIMIT = 19.07     # the Marauder's g_meter.max


def wait_for(cond, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class FixtureReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = []
        cls.events = []
        for name in ("live-4.10-takeoff.bin", "live-4.10-landing.bin"):
            entry, events = cls.replay(name)
            cls.entries.append(entry)
            cls.events.extend(events)
        cls.sum = st.combine(cls.entries)

    @classmethod
    def replay(cls, name):
        """Serve the capture with no gaps. The recorder's clock follows the capture's own
        times, one step per frame, so durations match the real session."""
        path = os.path.join(FIX, name)
        blobs = read_desc(path + ".desc")
        recs = read_raw(path)
        times = [ts - recs[0][0] for ts, _ in recs]
        tmp = tempfile.TemporaryDirectory()
        try:
            chan = os.path.join(tmp.name, "LIVE")
            os.makedirs(os.path.join(chan, "Bin64"))
            with open(os.path.join(chan, "Bin64", "StarCitizen.exe"), "wb") as fh:
                fh.write(exe_bytes(blobs))
            with open(os.path.join(chan, "build_manifest.id"), "w") as fh:
                json.dump({"Data": {"Version": "4.10.201.48085"}}, fh)
            settings = {"record": True, "detail": "normal",
                        "groups": list(tlm.DEFAULT_GROUPS), "csr": "test"}
            running = [True]
            closed = []

            def on_close(p):
                closed.append(p)
                running[0] = False

            box = []

            def clock():
                rec = box[0] if box else None
                k = rec._st["frames"] if rec is not None else 0
                return times[min(k, len(times) - 1)]

            fake_time = types.SimpleNamespace(monotonic=clock, time=time.time, sleep=time.sleep)
            patches = [mock.patch.object(tlm, n, v) for n, v in
                       (("RETRY_S", 0.05), ("OFF_AFTER_S", 0.3), ("POLL_S", 0.05),
                        ("IDLE_S", 0.05), ("time", fake_time))]
            for p in patches:
                p.start()
            try:
                with FakeGrpcServer([b for _, b in recs]) as srv:
                    rec = tlm.Recorder(os.path.join(tmp.name, "telemetry"), lambda: dict(settings),
                                       lambda: running[0], lambda: ("LIVE", chan), on_close,
                                       host="127.0.0.1", port=srv.port)
                    box.append(rec)
                    rec.start()
                    done = wait_for(lambda: closed, 30)
                    frames = rec.status()["frames"]
                    events = [e["type"] for e in rec.live()["events"]]
                    rec.stop()
            finally:
                for p in patches:
                    p.stop()
            if not done:
                raise AssertionError("%s: recording not closed, %d of %d frames"
                                     % (name, frames, len(recs)))
            entry = st.summarize(closed[0])
        finally:
            tmp.cleanup()
        return entry, events

    def test_each_file_covers_its_capture(self):
        takeoff, landing = (e["sum"] for e in self.entries)
        self.assertEqual((takeoff["takeoffs"], takeoff["landings"]), (1, 0))
        self.assertEqual((landing["takeoffs"], landing["landings"]), (0, 1))
        self.assertGreater(takeoff["t_nav"], 30)
        self.assertGreater(landing["t_on_foot"], 5)

    def test_takeoff_and_one_landing(self):
        self.assertGreaterEqual(self.sum["takeoffs"], 1)
        self.assertEqual(self.sum["landings"], 1)

    def test_speed_and_nav(self):
        self.assertGreater(self.sum["top_speed"], 100)
        self.assertGreater(self.sum["t_nav"], 0)

    def test_max_g_leaves_out_contact_spike(self):
        self.assertGreater(self.sum["max_g"], 5)
        self.assertLessEqual(self.sum["max_g"], 1.25 * G_LIMIT)

    def test_fuel_power_down_not_used(self):
        self.assertLess(self.sum["h2_used"], 0.05)

    def test_wallet_is_the_fixture_value(self):
        self.assertEqual((self.sum["uec_start"], self.sum["uec_end"]), (UEC, UEC))
        self.assertEqual((self.sum["uec_earned"], self.sum["uec_spent"]), (0, 0))

    def test_on_foot_and_leave_seat(self):
        self.assertGreater(self.sum["t_on_foot"], 0)
        self.assertTrue(any("LEAVE_SEAT" in e for e in self.events), self.events)


if __name__ == "__main__":
    unittest.main()
