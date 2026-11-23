"""Telemetry recording writer: flatten, groups, change-only lines with peaks."""
import gzip
import json
import os
import sys
import tempfile
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_telemetry as tlm  # noqa: E402

SPEED = "vehicle.state.flight.instruments.speed"
NAME = "vehicle.state.name"
WALLET = "actor.state.uec_balance"


def read_lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(x) for x in fh.read().splitlines()]


class FlattenTests(unittest.TestCase):
    def test_flatten_paths_and_sentinel(self):
        frame = {"vehicle": {"state": {"flight": {"instruments": {
            "altitude": 3.4028234663852886e+38, "speed": 5.0}}}},
            "weapons": {"weapons": [{"name": "A"}]},
            "events": {"events": [{"type": "ACTOR_DEAD"}]}}
        self.assertEqual(tlm.flatten(frame), {
            "vehicle.state.flight.instruments.altitude": None,
            "vehicle.state.flight.instruments.speed": 5.0,
            "weapons.weapons[0].name": "A"})

    def test_flatten_scalar_lists_kept(self):
        self.assertEqual(tlm.flatten({"vehicle": {"flight": {"speeds": [0.5, 1.0]}}}),
                         {"vehicle.flight.speeds": [0.5, 1.0]})

    def test_frame_events(self):
        frame = {"events": {"events": [{"type": "ACTOR_DEAD"}, {"type": "ACTOR_DOWNED"}]}}
        self.assertEqual(tlm.frame_events(frame), ["ACTOR_DEAD", "ACTOR_DOWNED"])
        self.assertEqual(tlm.frame_events({"actor": {}}), [])

    def test_group_of(self):
        for path, group in [
            ("actor.state.uec_balance", "wallet"),
            ("actor.state.locomotion", "you"),
            ("actor.vitals.health.current", "vitals"),
            ("actor.environment.gravity", "surroundings"),
            ("vehicle.state.flight.settings.ifcs_esp.active", "settings"),
            ("weapons.settings.staggered_fire", "settings"),
            ("vehicle.state.power.engines", "ship"),
            ("vehicle.state.flight.instruments.speed", "flight"),
            ("weapons.weapons[0].name", "weapons"),
            ("turret.x", "turret"),
            ("force_feedback.axis", "force_feedback"),
            ("foo.bar", "other")]:
            self.assertEqual(tlm.group_of(path), group, path)
        self.assertNotIn("force_feedback", tlm.DEFAULT_GROUPS)
        self.assertIn("other", tlm.DEFAULT_GROUPS)

    def test_is_smooth(self):
        self.assertTrue(tlm.is_smooth(SPEED))
        self.assertTrue(tlm.is_smooth("vehicle.state.boost.reserves"))
        self.assertTrue(tlm.is_smooth("force_feedback.stick_x"))
        self.assertFalse(tlm.is_smooth(NAME))
        self.assertFalse(tlm.is_smooth("vehicle.state.top_speed"))


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "r.jsonl.gz")

    def writer(self, detail="normal", groups=None):
        return tlm.RecordingWriter(self.path, {"v": 1}, detail,
                                   tlm.DEFAULT_GROUPS if groups is None else groups)

    def test_header_first(self):
        w = self.writer()
        w.close()
        head = read_lines(self.path)[0]
        self.assertEqual(head["v"], 1)
        self.assertEqual(head["detail"], "normal")
        self.assertIn("wallet", head["groups"])
        self.assertNotIn("force_feedback", head["groups"])

    def test_changes_only(self):
        w = self.writer()
        w.add(0.0, {NAME: "A"}, [])
        w.add(0.1, {NAME: "A"}, [])
        w.close()
        lines = read_lines(self.path)
        self.assertEqual(lines[1:], [{"t": 0.0, NAME: "A"}])

    def test_bytes_written_grows(self):
        w = self.writer()
        w.add(0.0, {NAME: "A"}, [])
        w.close()
        self.assertEqual(w.bytes_written, os.path.getsize(self.path))
        self.assertGreater(w.bytes_written, 0)

    def test_removed_paths(self):
        w = self.writer()
        w.add(0.0, {NAME: "A", WALLET: 5}, [])
        w.add(1.0, {WALLET: 5}, [])
        w.close()
        self.assertEqual(read_lines(self.path)[2], {"t": 1.0, "-": [NAME]})

    def _speeds(self, w):
        w.add(0.0, {SPEED: 0, NAME: "A"}, [])
        w.add(0.1, {SPEED: 50, NAME: "B"}, [])
        w.add(0.2, {SPEED: 300, NAME: "B"}, [])
        w.add(0.3, {SPEED: 40, NAME: "B"}, [])
        w.add(0.6, {SPEED: 41, NAME: "B"}, [])
        w.close()

    def test_smooth_sampling_and_peaks_normal(self):
        self._speeds(self.writer())
        lines = read_lines(self.path)[1:]
        self.assertEqual(lines, [
            {"t": 0.0, SPEED: 0, NAME: "A"},
            {"t": 0.1, NAME: "B"},
            {"t": 0.6, SPEED: 41, SPEED + "^": 300, SPEED + "_": 40}])

    def test_peaks_written_when_value_unchanged(self):
        w = self.writer()
        w.add(0.0, {SPEED: 10}, [])
        w.add(0.2, {SPEED: 90}, [])
        w.add(0.6, {SPEED: 10}, [])
        w.close()
        self.assertEqual(read_lines(self.path)[2], {"t": 0.6, SPEED + "^": 90})

    def test_light_detail_interval(self):
        w = self.writer("light")
        w.add(0.0, {SPEED: 1}, [])
        w.add(1.0, {SPEED: 2}, [])
        w.add(5.0, {SPEED: 3}, [])
        w.close()
        self.assertEqual(read_lines(self.path)[1:], [
            {"t": 0.0, SPEED: 1},
            {"t": 5.0, SPEED: 3, SPEED + "_": 2}])

    def test_full_detail_writes_every_change(self):
        self._speeds(self.writer("full"))
        lines = read_lines(self.path)[1:]
        self.assertEqual(len(lines), 5)
        for ln in lines:
            self.assertFalse(any(k.endswith(("^", "_")) for k in ln))

    def test_groups_filter(self):
        groups = [g for g in tlm.DEFAULT_GROUPS if g != "wallet"]
        w = self.writer(groups=groups)
        w.add(0.0, {WALLET: 5, NAME: "A"}, [])
        w.close()
        lines = read_lines(self.path)
        self.assertNotIn("wallet", lines[0]["groups"])
        self.assertTrue(all(WALLET not in ln for ln in lines))
        self.assertEqual(lines[1], {"t": 0.0, NAME: "A"})

    def test_events_always_written(self):
        w = self.writer()
        w.add(0.0, {NAME: "A"}, [])
        w.add(2.0, {NAME: "A"}, ["ACTOR_DEAD"])
        w.add(3.0, {NAME: "A"}, ["ACTOR_DEAD"])
        w.close()
        self.assertEqual(read_lines(self.path)[2:], [{"t": 2.0, "ev": ["ACTOR_DEAD"]},
                                                     {"t": 3.0, "ev": ["ACTOR_DEAD"]}])

    def _raw_text(self):
        with open(self.path, "rb") as fh:
            raw = fh.read()
        d = zlib.decompressobj(31)
        try:
            return d.decompress(raw).decode("utf-8")
        except zlib.error:
            return ""

    def test_periodic_flush_readable_before_close(self):
        w = self.writer()
        w.add(0.0, {NAME: "A"}, [])
        w.add(1.0, {NAME: "B"}, [])
        self.assertNotIn('"t":1.0', self._raw_text())
        w.add(5.0, {NAME: "C"}, [])
        self.assertIn('"t":5.0', self._raw_text())
        w.close()

    def test_events_group_gates_ev(self):
        groups = [g for g in tlm.DEFAULT_GROUPS if g != "events"]
        w = self.writer(groups=groups)
        w.add(0.0, {NAME: "A"}, ["ACTOR_DEAD"])
        w.add(1.0, {NAME: "A"}, ["ACTOR_DEAD"])
        w.close()
        self.assertEqual(read_lines(self.path)[1:], [{"t": 0.0, NAME: "A"}])

    def test_close_writes_pending_smooth_and_peaks(self):
        w = self.writer("light")
        w.add(0.0, {SPEED: 10}, [])
        w.add(1.0, {SPEED: 90}, [])
        w.add(2.0, {SPEED: 20}, [])
        w.close()
        self.assertEqual(read_lines(self.path)[2:],
                         [{"t": 2.0, SPEED: 20, SPEED + "^": 90}])

    def test_close_idempotent_and_quiet_when_nothing_pending(self):
        w = self.writer()
        w.add(0.0, {SPEED: 10}, [])
        w.close()
        w.close()
        self.assertEqual(len(read_lines(self.path)), 2)

    def test_close_closes_file_when_sync_fails(self):
        w = self.writer()
        fh = w._fh
        with mock.patch.object(w, "_sync", side_effect=OSError("x")):
            with self.assertRaises(OSError):
                w.close()
        w.close()
        self.assertTrue(fh.closed)


if __name__ == "__main__":
    unittest.main()
