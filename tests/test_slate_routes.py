"""Slate routes: the live payload carries the Game.log state, ship data per class on both servers."""
import http.client
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_livelog  # noqa: E402
import sc_stats  # noqa: E402

LIVE = {"state": "live", "reason": None, "snapshot": {"t": 1}, "events": []}
LOG = {"place": "Port", "jurisdiction": None, "armistice": True, "notices": [], "contracts": [], "kills": [],
       "injuries": {}, "loadout": {}, "blueprints": 0}


def api(path, live=lambda: LIVE):
    return sc_stats.tlm_api_get(path, live, lambda: {"ok": True}, lambda: None)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        for target, attr, val in ((sc_livelog, "get_state", lambda *a, **k: dict(LOG)),
                                  (sc_stats, "_APP_DIR", self._td.name),
                                  (sc_stats, "_SLATE_LAST", {"loaded": False, "data": None, "saved": 0})):
            p = mock.patch.object(target, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def test_last_ship_kept_after_leaving_the_seat(self):
        veh = {"state": {"class_name": "DRAK_Marauder", "flight": {"instruments": {"hydrogen_fuel": 0.8}}}}
        api("/api/telemetry/live", live=lambda: dict(LIVE, snapshot={"vehicle": veh}))
        body = json.loads(api("/api/telemetry/live", live=lambda: dict(LIVE, snapshot={"actor": {}})))
        self.assertEqual(body["lastShip"]["vehicle"]["state"]["class_name"], "DRAK_Marauder")
        self.assertIn("t", body["lastShip"])

    def test_last_ship_survives_a_restart(self):
        veh = {"state": {"class_name": "DRAK_Marauder"}}
        api("/api/telemetry/live", live=lambda: dict(LIVE, snapshot={"vehicle": veh}))
        sc_stats._SLATE_LAST.update(loaded=False, data=None, saved=0)
        body = json.loads(api("/api/telemetry/live"))
        self.assertEqual(body["lastShip"]["vehicle"]["state"]["class_name"], "DRAK_Marauder")

    def test_contracts_get_mission_info(self):
        row = ["PU_Bounty_Test", "Bounty: Test Target", "Northrock Service Group", 2, "Applicant", 500, 1500,
               [["Northrock Service Group", 1500]], True, ["Stanton"], "https://example.invalid"]
        log = dict(LOG, contracts=[{"name": "Bounty: Test Target", "status": "active", "objective": None, "t": 1},
                                   {"name": "Unknown Job", "status": "active", "objective": None, "t": 2}])
        with mock.patch.object(sc_livelog, "get_state", lambda *a, **k: log),                 mock.patch.object(sc_stats.MIS, "rows", [row]), mock.patch.object(sc_stats.MIS, "ok", True):
            cs = json.loads(api("/api/telemetry/live"))["log"]["contracts"]
        self.assertEqual(cs[0]["info"], {"type": "Bounty", "giver": "Northrock Service Group", "standing": "Applicant",
                                         "rep": 1500, "bp": True})
        self.assertEqual(cs[1]["info"], {"type": "Other", "giver": None, "standing": None, "rep": None, "bp": False})

    def test_no_last_ship_yet(self):
        self.assertIsNone(json.loads(api("/api/telemetry/live"))["lastShip"])

    def test_live_has_log(self):
        body = json.loads(api("/api/telemetry/live"))
        self.assertEqual(body["log"]["place"], "Port")
        self.assertEqual(body["snapshot"], {"t": 1})

    def test_live_log_error_is_none(self):
        def boom(*a, **k):
            raise RuntimeError("no log")
        with mock.patch.object(sc_livelog, "get_state", boom):
            body = json.loads(api("/api/telemetry/live"))
        self.assertIsNone(body["log"])
        self.assertEqual(body["state"], "live")

    def test_live_names_weapon_classes(self):
        snap = {"weapons": {"weapons": [{"name": "KLWE_LaserRepeater_S3_951758134358"}]}}
        body = json.loads(api("/api/telemetry/live", live=lambda: dict(LIVE, snapshot=snap)))
        self.assertEqual(body["names"], {"klwe_laserrepeater_s3": "CF-337 Panther Repeater"})

    def test_live_names_loadout_items(self):
        log = dict(LOG, loadout={"Armor_Torso": "qrt_combat_medium_core_01_01_15", "wep_sidearm": "klwe_pistol_energy_01",
                                 "Armor_Helmet": "Default"})
        with mock.patch.object(sc_livelog, "get_state", lambda *a, **k: log):
            names = json.loads(api("/api/telemetry/live"))["names"]
        self.assertEqual(names, {"qrt_combat_medium_core_01_01_15": "Testudo Core Earthshake",
                                 "klwe_pistol_energy_01": "Arclight Pistol"})

    def test_live_names_empty_off_seat(self):
        self.assertEqual(json.loads(api("/api/telemetry/live"))["names"], {})

    def test_status_has_no_log(self):
        self.assertNotIn("log", json.loads(api("/api/telemetry/status")))

    def test_ship_route_main(self):
        body = json.loads(api("/api/telemetry/ship?c=RSI_Perseus"))
        self.assertEqual(body["class"], "RSI_Perseus")
        self.assertIn("turrets", body)

    def test_ship_route_unknown(self):
        self.assertIsNone(api("/api/telemetry/ship?c=NOPE_Ship"))
        self.assertIsNone(api("/api/telemetry/ship"))


class PhoneRouteTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        for target, attr, val in ((sc_stats, "_APP_DIR", self._td.name),
                                  (sc_stats, "LIVE_HTML", lambda: "<html>live page</html>"),
                                  (sc_livelog, "get_state", lambda *a, **k: dict(LOG))):
            p = mock.patch.object(target, attr, val)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(sc_stats.stop_phone_server)
        self.port = sc_stats.start_phone_server([0], key="k1", live=lambda: LIVE, on_seen=lambda: None,
                                                host="127.0.0.1")

    def get(self, path):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        out = (r.status, r.read().decode("utf-8"))
        c.close()
        return out

    def test_phone_ship_route(self):
        st, body = self.get("/api/telemetry/ship?c=RSI_Perseus&k=k1")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["class"], "RSI_Perseus")

    def test_phone_ship_route_needs_key(self):
        self.assertEqual(self.get("/api/telemetry/ship?c=RSI_Perseus")[0], 403)
        self.assertEqual(self.get("/api/telemetry/ship?c=RSI_Perseus&k=bad")[0], 403)

    def test_phone_ship_unknown(self):
        self.assertEqual(self.get("/api/telemetry/ship?c=NOPE&k=k1")[0], 404)

    def test_phone_live_has_log(self):
        st, body = self.get("/api/telemetry/live?k=k1")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["log"]["place"], "Port")


if __name__ == "__main__":
    unittest.main()
