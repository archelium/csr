"""Phone listener: live view only, private key, cookie, start/stop from config."""
import http.client
import json
import os
import socket
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

LIVE = {"state": "streaming", "reason": None, "snapshot": {"t": 1}, "events": []}


class _PhoneBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        p = mock.patch.object(sc_stats, "_APP_DIR", self._td.name)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(sc_stats, "LIVE_HTML", lambda: "<html>live page</html>")
        p.start()
        self.addCleanup(p.stop)
        self.seen = []
        self.addCleanup(sc_stats.stop_phone_server)

    def start(self, key="k1"):
        port = sc_stats.start_phone_server([0], key=key, live=lambda: LIVE,
                                           on_seen=lambda: self.seen.append(1),
                                           host="127.0.0.1")
        self.assertIsInstance(port, int)
        self.port = port
        return port

    def get(self, path, cookie=None, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Cookie": cookie} if cookie else {}
        c.request(method, path, headers=headers)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, r.getheader("Set-Cookie"), body


class PhoneScopeTests(_PhoneBase):
    def test_phone_scope(self):
        self.start("k1")
        st, cookie, body = self.get("/live?k=k1")
        self.assertEqual(st, 200)
        self.assertIn(b"live page", body)
        self.assertTrue(cookie)
        self.assertIn("csrk=k1", cookie)
        jar = cookie.split(";")[0]
        st, _c, body = self.get("/api/telemetry/live", cookie=jar)
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(body)["state"], "streaming")
        for path in ("/api/shutdown?k=k1", "/", "/api/status", "/api/export"):
            self.assertEqual(self.get(path, cookie=jar)[0], 404, path)
        self.assertEqual(self.get("/live?k=bad")[0], 403)
        self.assertEqual(self.get("/api/telemetry/live")[0], 403)

    def test_cookie_flags(self):
        self.start("k1")
        cookie = self.get("/live?k=k1")[1]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/", cookie)

    def test_key_checks(self):
        self.start("k1")
        self.assertEqual(self.get("/api/telemetry/live?k=k1")[0], 200)
        self.assertEqual(self.get("/api/telemetry/live", cookie="csrk=bad")[0], 403)
        self.assertEqual(self.get("/api/telemetry/live?k=k", cookie="a=b")[0], 403)
        self.assertEqual(self.get("/live", cookie="x=1; csrk=k1")[0], 200)
        self.assertEqual(self.get("/live?k=%C3%A9")[0], 403)
        self.assertIsNone(self.get("/live?k=bad")[1])
        self.assertEqual(self.get("/api/telemetry/live", method="POST")[0], 404)
        self.assertEqual(self.get("/api/telemetry/new-link?k=k1", method="POST")[0], 404)
        self.assertEqual(self.get("/live?k=k1", method="HEAD")[0], 404)

    def test_seen_only_on_valid_poll(self):
        self.start("k1")
        self.get("/api/telemetry/live")
        self.get("/live?k=k1")
        self.assertEqual(self.seen, [])
        self.get("/api/telemetry/live?k=k1")
        self.assertEqual(self.seen, [1])

    def test_live_error(self):
        port = sc_stats.start_phone_server([0], key="k1", live=lambda: 1 / 0,
                                           host="127.0.0.1")
        self.port = port
        st, _c, body = self.get("/api/telemetry/live?k=k1")
        self.assertEqual(st, 200)
        self.assertFalse(json.loads(body)["ok"])

    def test_no_key_refuses(self):
        port = sc_stats.start_phone_server([0], key=None, live=lambda: LIVE,
                                           host="127.0.0.1")
        self.port = port
        self.assertEqual(self.get("/live?k=")[0], 403)
        self.assertEqual(self.get("/live", cookie="csrk=")[0], 403)

    def test_stop(self):
        port = self.start("k1")
        self.assertIsNotNone(sc_stats.phone_url())
        sc_stats.stop_phone_server()
        self.assertIsNone(sc_stats.phone_url())
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
        sc_stats.stop_phone_server()           # second stop is a no-op

    def test_start_twice_keeps_port(self):
        port = self.start("k1")
        again = sc_stats.start_phone_server([0], key="k2", live=lambda: LIVE,
                                            host="127.0.0.1")
        self.assertEqual(again, port)
        self.assertEqual(self.get("/live?k=k1")[0], 403)
        self.assertEqual(self.get("/live?k=k2")[0], 200)

    def test_port_fallback(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        self.addCleanup(busy.close)
        taken = busy.getsockname()[1]
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        port = sc_stats.start_phone_server([taken, free], key="k1", live=lambda: LIVE,
                                           host="127.0.0.1")
        self.assertEqual(port, free)

    def test_no_port(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        self.addCleanup(busy.close)
        port = sc_stats.start_phone_server([busy.getsockname()[1]], key="k1",
                                           live=lambda: LIVE, host="127.0.0.1")
        self.assertIsNone(port)
        self.assertIsNone(sc_stats.phone_url())

    def test_phone_url(self):
        port = self.start("k1")
        with mock.patch.object(sc_stats, "lan_ip", return_value="192.168.1.5"):
            self.assertEqual(sc_stats.phone_url(), f"http://192.168.1.5:{port}/live?k=k1")


class LanIpTests(unittest.TestCase):
    def test_lan_ip_shape(self):
        ip = sc_stats.lan_ip()
        parts = ip.split(".")
        self.assertEqual(len(parts), 4)
        self.assertTrue(all(p.isdigit() and 0 <= int(p) <= 255 for p in parts))

    def test_lan_ip_fallback(self):
        with mock.patch.object(sc_stats.socket, "socket", side_effect=OSError("no net")):
            self.assertEqual(sc_stats.lan_ip(), "127.0.0.1")


class PhoneConfigTests(_PhoneBase):
    def sync(self):
        return sc_stats.sync_phone_server(live=lambda: LIVE, on_seen=lambda: None,
                                          host="127.0.0.1", port_range=[0])

    def test_sync_off_by_default(self):
        self.assertIsNone(self.sync())
        self.assertIsNone(sc_stats.phone_url())
        self.assertIsNone(sc_stats.tlm_settings()["phone_key"])

    def test_sync_on_creates_key_and_starts(self):
        sc_stats.save_tlm_settings({"phone": True})
        port = self.sync()
        self.port = port
        key = sc_stats.tlm_settings()["phone_key"]
        self.assertIsInstance(key, str)
        self.assertGreaterEqual(len(key), 20)
        self.assertEqual(self.get(f"/live?k={key}")[0], 200)
        # same key on the next sync
        self.sync()
        self.assertEqual(sc_stats.tlm_settings()["phone_key"], key)
        sc_stats.save_tlm_settings({"phone": False})
        self.assertIsNone(self.sync())
        self.assertIsNone(sc_stats.phone_url())

    def test_error_names_busy_ports(self):
        import socket
        blocker = socket.socket()
        blocker.bind(("0.0.0.0", 0))
        busy = blocker.getsockname()[1]
        try:
            sc_stats.save_tlm_settings({"phone": True})
            sc_stats.new_phone_key()
            msg = sc_stats.phone_sync_error(port_range=range(busy, busy + 1),
                                            live=lambda: LIVE, on_seen=lambda: None,
                                            host="0.0.0.0")
        finally:
            blocker.close()
        self.assertIn("no free port", msg)
        self.assertIn(str(busy), msg)

    def test_error_names_unreadable_config(self):
        sc_stats.save_tlm_settings({"phone": True})
        with mock.patch.object(sc_stats, "save_tlm_settings", return_value=None):
            msg = sc_stats.phone_sync_error(live=lambda: LIVE, on_seen=lambda: None,
                                            host="127.0.0.1", port_range=[0])
        self.assertIn("csr_config.json", msg)

    def test_no_error_when_off(self):
        self.assertIsNone(sc_stats.phone_sync_error(port_range=[0]))

    def test_handler_timeout(self):
        self.assertEqual(sc_stats._PhoneHandler.timeout, 10)

    def test_new_link(self):
        sc_stats.save_tlm_settings({"phone": True})
        self.port = self.sync()
        old = sc_stats.tlm_settings()["phone_key"]
        new = sc_stats.new_phone_key()
        self.assertNotEqual(new, old)
        self.assertEqual(sc_stats.tlm_settings()["phone_key"], new)
        self.assertEqual(self.get(f"/live?k={old}")[0], 403)
        self.assertEqual(self.get("/api/telemetry/live", cookie=f"csrk={old}")[0], 403)
        self.assertEqual(self.get(f"/live?k={new}")[0], 200)

    def test_new_link_unreadable_config(self):
        with open(sc_stats.config_path(), "wb") as fh:
            fh.write(b"{broken")
        self.assertIsNone(sc_stats.new_phone_key())

    def test_phone_status(self):
        self.assertEqual(sc_stats.phone_status(), {"on": False, "url": None})
        sc_stats.save_tlm_settings({"phone": True})
        self.port = self.sync()
        st = sc_stats.phone_status()
        self.assertTrue(st["on"])
        self.assertIn(f":{self.port}/live?k=", st["url"])
        self.assertTrue(st["qr_svg"].startswith("<svg"))


if __name__ == "__main__":
    unittest.main()
