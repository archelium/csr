"""The slate's data model (pure JS in sc_slate.SLATE_MODEL_JS) run in Node against real 4.10 frames."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.tlm_helpers import read_desc, read_raw  # noqa: E402
import sc_slate  # noqa: E402
import sc_slate_ships  # noqa: E402
import sc_telemetry as tlm  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "telemetry")
NODE = shutil.which("node")
HARNESS = """const fs=require('fs');const m=require('./model.js');
const p=JSON.parse(fs.readFileSync(0,'utf8'));
process.stdout.write(JSON.stringify({model:m.slateModel(p.payload,p.ship),levels:p.levels.map(m.annunciatorLevel)}));"""


def frame(name, which):
    s = tlm.Schema.from_blobs(read_desc(os.path.join(FIX, name + ".bin.desc")))
    raw = read_raw(os.path.join(FIX, name + ".bin"))
    msg = raw[len(raw) // 2][1] if which == "middle" else raw[-1][1]
    f = s.decode(tlm.RESP_TYPE, msg)
    return {k: v for k, v in f.items() if k != "events"}


def payload(snapshot, state="live", events=None, log=None, names=None):
    return {"state": state, "reason": "", "snapshot": snapshot, "events": events or [], "log": log,
            "names": names or {}}


@unittest.skipUnless(NODE, "node not installed")
class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        with open(os.path.join(cls._td.name, "model.js"), "w", encoding="utf-8") as fh:
            fh.write(sc_slate.SLATE_MODEL_JS)
        with open(os.path.join(cls._td.name, "run.js"), "w", encoding="utf-8") as fh:
            fh.write(HARNESS)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def run_model(self, p, ship=None, levels=()):
        r = subprocess.run([NODE, "run.js"], cwd=self._td.name, capture_output=True, text=True,
                           input=json.dumps({"payload": p, "ship": ship, "levels": list(levels)}, default=str),
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_master_mode_from_annunciators(self):
        def snap(**ann):
            return {"actor": {"state": {"locomotion": "SEATED", "crew_role": "PILOT"}},
                    "vehicle": {"state": {"class_name": "DRAK_Marauder", "annunciators": ann}}}
        nav = self.run_model(payload(snap(shld="OFF", qdrv="NOMINAL", thr="MAJOR_ON")))["model"]
        self.assertEqual(nav["flight"]["master"], "nav")
        self.assertEqual(nav["ann"]["thr"], "ok")              # engines held by quantum travel, not damage
        scm = self.run_model(payload(snap(shld="NOMINAL", qdrv="OFF", thr="MINOR_ON", hit="MAJOR_FLASHING")))["model"]
        self.assertEqual(scm["flight"]["master"], "scm")
        self.assertEqual(scm["ann"]["thr"], "min")
        self.assertEqual(scm["ann"]["hit"], "maj")
        self.assertTrue(scm["threat"])

    def test_410_fixture_flight(self):
        snap = frame("live-4.10-takeoff", "middle")
        m = self.run_model(payload(snap, names={"klwe_laserrepeater_s3": "CF-337 Panther Repeater"}),
                           ship=sc_slate_ships.ship_payload("DRAK_Marauder"))["model"]
        self.assertTrue(m["live"])
        self.assertEqual(m["seat"], "pilot")
        self.assertEqual(m["ship"]["cls"], "DRAK_Marauder")
        self.assertEqual(m["ship"]["name"], "DRAK_Marauder")
        self.assertIsNone(m["flight"]["alt"])                  # 3.4e38 = no reference body
        self.assertAlmostEqual(m["flight"]["speed"], 929.9, delta=60)
        self.assertIsNone(m["hull"])                           # not in LIVE 4.10
        self.assertIsNone(m["shields"]["faces"])
        self.assertEqual(m["flight"]["master"], "nav")        # 4.10: from shields off + quantum drive on
        self.assertTrue(m["flight"]["masterGuess"])
        self.assertEqual(m["ann"]["wpn"], "min")
        self.assertEqual(m["cm"]["decoy"], 48)
        self.assertEqual(m["weapons"][0][0], "CF-337 Panther Repeater")
        self.assertEqual(m["weapons"][0][1], "S3")
        self.assertTrue(m["ifcs"]["ifcs_coupled"]["active"])
        self.assertTrue(m["power"]["engines"])
        self.assertEqual(m["wallet"], 1000000)
        self.assertEqual(m["vitals"]["o2"], 100)
        self.assertFalse(m["threat"])

    def test_410_fixture_on_foot(self):
        m = self.run_model(payload(frame("live-4.10-landing", "last")))["model"]
        self.assertEqual(m["seat"], "foot")
        self.assertIsNone(m["ship"])
        self.assertEqual(m["weapons"], [])

    def test_waiting(self):
        m = self.run_model({"state": "waiting", "snapshot": {}, "events": []})["model"]
        self.assertFalse(m["live"])
        self.assertTrue(m["wait"])
        self.assertEqual(m["log"]["notices"], [])
        self.assertIsNone(m["seat"])

    def test_threat(self):
        snap = {"vehicle": {"state": {"class_name": "AEGS_Gladius", "name": "AEGS_Gladius_123456789",
                                      "annunciators": {"misl": "MAJOR_ON", "hit": "NOMINAL"}}},
                "actor": {"state": {"locomotion": "SEATED", "crew_role": "PILOT"}}}
        m = self.run_model(payload(snap))["model"]
        self.assertTrue(m["threat"])
        self.assertEqual(m["ann"]["misl"], "maj")

    def test_5_0_shields_and_hull(self):
        snap = {"vehicle": {"state": {"class_name": "AEGS_Gladius", "health": 0.62,
                                      "shields": {"level": 0.8, "faces": [0.18, 0.84, 0.62, 0.79]},
                                      "master_mode": {"mode": "NAVIGATION", "spooled": True}}},
                "actor": {"state": {"locomotion": "SEATED", "crew_role": "PILOT"}}}
        m = self.run_model(payload(snap))["model"]
        self.assertEqual(m["shields"]["faces"], [18, 84, 62, 79])
        self.assertEqual(m["shields"]["bubble"], 80)
        self.assertEqual(m["hull"], 62)
        self.assertEqual(m["flight"]["master"], "nav")

    def test_annunciator_levels(self):
        out = self.run_model(payload({}), levels=["NOMINAL", "MINOR_ON", "MINOR_FLASHING", "MAJOR_ON",
                                                  "MAJOR_FLASHING", "OFF", None])["levels"]
        self.assertEqual(out, ["ok", "min", "min", "maj", "maj", "off", "off"])

    def test_events_counted(self):
        p = payload({}, events=[{"t": 1, "type": "ACTOR_DOWNED"}, {"t": 2, "type": "ACTOR_DEAD"},
                                {"t": 3, "type": "ACTOR_ENTER_SEAT"}, {"t": 4, "type": "ACTOR_ENTER_SEAT"}])
        m = self.run_model(p)["model"]
        self.assertEqual(m["events"], {"downed": 1, "dead": 1, "respawn": 0, "seat": 2})


if __name__ == "__main__":
    unittest.main()
