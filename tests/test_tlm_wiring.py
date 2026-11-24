"""Telemetry wiring in sc_stats: config helpers, user.cfg, settings validation, channel pick."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

LINE = "grpc.client.server.enabled = true"
BOM = bytes([0xEF, 0xBB, 0xBF])


class _TmpDir(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.addCleanup(self._td.cleanup)


class ConfigTests(_TmpDir):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(sc_stats, "_APP_DIR", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def _write_cfg(self, cfg):
        with open(sc_stats.config_path(), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)

    def test_update_config_preserves(self):
        self._write_cfg({"live_path": "C:/old", "tlm": {"phone_key": "abc", "record": False}})
        sc_stats.remember_folder("C:/Games/StarCitizen")
        cfg = sc_stats.load_config()
        self.assertEqual(cfg["live_path"], "C:/Games/StarCitizen")
        self.assertEqual(cfg["tlm"]["phone_key"], "abc")
        self.assertFalse(cfg["tlm"]["record"])

    def test_update_config_merges_top_level(self):
        self._write_cfg({"a": 1, "b": 2})
        out = sc_stats.update_config(b=3, c=4)
        self.assertEqual(out, {"a": 1, "b": 3, "c": 4})
        self.assertEqual(sc_stats.load_config(), {"a": 1, "b": 3, "c": 4})

    def _raw(self):
        with open(sc_stats.config_path(), "rb") as fh:
            return fh.read()

    def test_update_config_refuses_corrupt(self):
        for bad in (b'{"live_path": "C:/x", "tlm": {"phone_key": "abc"', b"[1, 2]"):
            with open(sc_stats.config_path(), "wb") as fh:
                fh.write(bad)
            self.assertIsNone(sc_stats.update_config(live_path="C:/y"))
            self.assertIsNone(sc_stats.remember_folder("C:/y"))
            self.assertIsNone(sc_stats.save_tlm_settings({"detail": "light"}))
            self.assertEqual(self._raw(), bad)
            self.assertEqual(sc_stats.load_config(), {} if bad.startswith(b"{") else [1, 2])

    def test_update_config_creates_missing(self):
        self.assertEqual(sc_stats.update_config(a=1), {"a": 1})
        self.assertEqual(sc_stats.load_config(), {"a": 1})

    def test_save_config_atomic(self):
        self._write_cfg({"keep": 1})
        self.assertTrue(sc_stats.save_config({"keep": 2}))
        self.assertEqual(sc_stats.load_config(), {"keep": 2})
        self.assertEqual(os.listdir(self.tmp), [sc_stats.CONFIG_NAME])
        with mock.patch.object(sc_stats.os, "replace", side_effect=OSError("locked")):
            self.assertFalse(sc_stats.save_config({"keep": 3}))
            self.assertIsNone(sc_stats.update_config(keep=4))
        self.assertEqual(sc_stats.load_config(), {"keep": 2})
        self.assertEqual(os.listdir(self.tmp), [sc_stats.CONFIG_NAME])

    def test_config_bom_and_empty(self):
        with open(sc_stats.config_path(), "wb") as fh:
            fh.write(BOM + json.dumps({"live_path": "C:/x"}).encode())
        self.assertEqual(sc_stats.load_config(), {"live_path": "C:/x"})
        self.assertEqual(sc_stats.remember_folder("C:/y"), {"live_path": "C:/y"})
        for raw in (b"", b"  \r\n", BOM, BOM + b" \n"):
            with open(sc_stats.config_path(), "wb") as fh:
                fh.write(raw)
            self.assertEqual(sc_stats.load_config(), {})
            self.assertEqual(sc_stats.remember_folder("C:/z"), {"live_path": "C:/z"})
            self.assertEqual(sc_stats.load_config(), {"live_path": "C:/z"})

    def test_unreadable_config_error_names_path(self):
        with open(sc_stats.config_path(), "wb") as fh:
            fh.write(b"{broken")
        with mock.patch.object(sc_stats, "cerr") as err:
            self.assertIsNone(sc_stats.remember_folder("C:/y"))
        self.assertIn(sc_stats.config_path(), err.call_args[0][0])

    def test_settings_reply_survives_status_error(self):
        saved = sc_stats.save_tlm_settings({"detail": "light", "phone_key": "secret"})

        def boom():
            raise RuntimeError("x")

        out = sc_stats.tlm_settings_reply(boom, saved)
        self.assertTrue(out["ok"])
        self.assertEqual(out["settings"]["detail"], "light")
        self.assertNotIn("phone_key", out["settings"])
        self.assertEqual(sc_stats.tlm_settings_reply(lambda: {"a": 1}, saved), {"ok": True, "a": 1})

    def test_tlm_settings_defaults(self):
        s = sc_stats.tlm_settings()
        self.assertEqual(s["record"], True)
        self.assertEqual(s["detail"], "normal")
        self.assertIsNone(s["keep_days"])
        self.assertEqual(s["groups"], sc_stats.sc_telemetry.DEFAULT_GROUPS)
        self.assertFalse(s["phone"])
        self.assertIsNone(s["phone_key"])

    def test_tlm_settings_merges_saved(self):
        self._write_cfg({"tlm": {"detail": "light", "keep_days": 30}})
        s = sc_stats.tlm_settings()
        self.assertEqual(s["detail"], "light")
        self.assertEqual(s["keep_days"], 30)
        self.assertTrue(s["record"])

    def test_save_tlm_settings_keeps_other_keys(self):
        self._write_cfg({"live_path": "C:/x", "tlm": {"phone_key": "abc", "detail": "full"}})
        sc_stats.save_tlm_settings({"detail": "light", "record": False})
        cfg = sc_stats.load_config()
        self.assertEqual(cfg["live_path"], "C:/x")
        self.assertEqual(cfg["tlm"], {"phone_key": "abc", "detail": "light", "record": False})


class UserCfgTests(_TmpDir):
    def _cfg(self, text):
        with open(os.path.join(self.tmp, "user.cfg"), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def _log(self, text):
        with open(os.path.join(self.tmp, "Game.log"), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def test_user_cfg_state(self):
        self._cfg("grpc-client-server-enabled = 1\r\n")
        self.assertFalse(sc_stats.user_cfg_state(self.tmp)["line"])
        self._cfg("g_language = english\r\ngrpc.client.server.enabled = true\r\n")
        self.assertTrue(sc_stats.user_cfg_state(self.tmp)["line"])
        self._cfg("GRPC.Client.Server.Enabled=1\n")
        self.assertTrue(sc_stats.user_cfg_state(self.tmp)["line"])
        self._cfg("grpc.client.server.enabled = false\n")
        self.assertFalse(sc_stats.user_cfg_state(self.tmp)["line"])

    def test_user_cfg_state_missing_files(self):
        self.assertEqual(sc_stats.user_cfg_state(self.tmp), {"line": False, "applied": False})
        self.assertEqual(sc_stats.user_cfg_state(None), {"line": False, "applied": False})

    def test_user_cfg_state_applied(self):
        self._log("<boot>\nCVar GRPC_CLIENT_SERVER_ENABLED set\n")
        self.assertTrue(sc_stats.user_cfg_state(self.tmp)["applied"])
        self._log("<boot>\n[cvars] grpc-client-server-enabled = 1\n")
        self.assertTrue(sc_stats.user_cfg_state(self.tmp)["applied"])
        self._log("x\n" * 300 + "GRPC_CLIENT_SERVER_ENABLED\n")
        self.assertFalse(sc_stats.user_cfg_state(self.tmp)["applied"])

    def test_user_cfg_state_applied_needs_newer_log(self):
        self._log("<boot>\nCVar GRPC_CLIENT_SERVER_ENABLED set\n")
        self._cfg(LINE + "\r\n")
        cfg, log = os.path.join(self.tmp, "user.cfg"), os.path.join(self.tmp, "Game.log")
        t = time.time()
        os.utime(log, (t - 600, t - 600))       # log from a launch before the edit
        os.utime(cfg, (t - 60, t - 60))
        self.assertEqual(sc_stats.user_cfg_state(self.tmp), {"line": True, "applied": False})
        os.utime(log, (t, t))                   # game launched after the edit
        self.assertEqual(sc_stats.user_cfg_state(self.tmp), {"line": True, "applied": True})

    def test_user_cfg_state_bom(self):
        with open(os.path.join(self.tmp, "user.cfg"), "wb") as fh:
            fh.write(b"\xef\xbb\xbf" + LINE.encode() + b"\r\n")
        self.assertTrue(sc_stats.user_cfg_state(self.tmp)["line"])
        ok, _ = sc_stats.enable_user_cfg(self.tmp)
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "user.cfg.csr-bak")))

    def test_enable_user_cfg_keeps_first_backup(self):
        bak = os.path.join(self.tmp, "user.cfg.csr-bak")
        with open(bak, "wb") as fh:
            fh.write(b"first\r\n")
        with open(os.path.join(self.tmp, "user.cfg"), "wb") as fh:
            fh.write(b"g_language = english\r\n")
        ok, _ = sc_stats.enable_user_cfg(self.tmp)
        self.assertTrue(ok)
        with open(bak, "rb") as fh:
            self.assertEqual(fh.read(), b"first\r\n")

    def test_enable_user_cfg(self):
        original = b"g_language = english\r\n"
        with open(os.path.join(self.tmp, "user.cfg"), "wb") as fh:
            fh.write(original)
        ok, msg = sc_stats.enable_user_cfg(self.tmp)
        self.assertTrue(ok, msg)
        with open(os.path.join(self.tmp, "user.cfg"), "rb") as fh:
            data = fh.read()
        self.assertEqual(data, original + (LINE + "\r\n").encode())
        with open(os.path.join(self.tmp, "user.cfg.csr-bak"), "rb") as fh:
            self.assertEqual(fh.read(), original)
        self.assertTrue(sc_stats.user_cfg_state(self.tmp)["line"])

    def test_enable_user_cfg_no_trailing_newline(self):
        with open(os.path.join(self.tmp, "user.cfg"), "wb") as fh:
            fh.write(b"g_language = english")
        ok, _ = sc_stats.enable_user_cfg(self.tmp)
        self.assertTrue(ok)
        with open(os.path.join(self.tmp, "user.cfg"), "rb") as fh:
            self.assertEqual(fh.read(), b"g_language = english\r\n" + (LINE + "\r\n").encode())

    def test_enable_user_cfg_noop_when_present(self):
        original = ("g_language = english\r\n" + LINE + "\r\n").encode()
        with open(os.path.join(self.tmp, "user.cfg"), "wb") as fh:
            fh.write(original)
        ok, _ = sc_stats.enable_user_cfg(self.tmp)
        self.assertTrue(ok)
        with open(os.path.join(self.tmp, "user.cfg"), "rb") as fh:
            self.assertEqual(fh.read(), original)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "user.cfg.csr-bak")))

    def test_enable_user_cfg_creates_file(self):
        ok, _ = sc_stats.enable_user_cfg(self.tmp)
        self.assertTrue(ok)
        with open(os.path.join(self.tmp, "user.cfg"), "rb") as fh:
            self.assertEqual(fh.read(), (LINE + "\r\n").encode())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "user.cfg.csr-bak")))

    def test_enable_user_cfg_no_folder(self):
        ok, _ = sc_stats.enable_user_cfg(None)
        self.assertFalse(ok)
        ok, _ = sc_stats.enable_user_cfg(os.path.join(self.tmp, "nope"))
        self.assertFalse(ok)


class ValidationTests(unittest.TestCase):
    def test_settings_validation(self):
        for body in ({"detail": "ultra"}, {"keep_days": 7}, {"groups": ["wallet", "nope"]}):
            ok, err = sc_stats.validate_tlm_settings(body)
            self.assertFalse(ok, body)
            self.assertIsInstance(err, str)

    def test_settings_validation_empty_groups(self):
        ok, err = sc_stats.validate_tlm_settings({"groups": []})
        self.assertFalse(ok)
        self.assertEqual(err, "Pick at least one group, or turn recording off.")

    def test_settings_validation_types(self):
        for body in ({"record": "yes"}, {"phone": 1}, {"keep_days": True}, {"keep_days": "30"},
                     {"groups": "wallet"}, {"groups": [1]}, [], "x", None):
            ok, _ = sc_stats.validate_tlm_settings(body)
            self.assertFalse(ok, body)

    def test_settings_validation_accepts(self):
        body = {"record": False, "detail": "full", "keep_days": 90,
                "groups": ["wallet", "other", "wallet"], "phone": True, "phone_key": "x"}
        ok, cleaned = sc_stats.validate_tlm_settings(body)
        self.assertTrue(ok, cleaned)
        self.assertEqual(cleaned, {"record": False, "detail": "full", "keep_days": 90,
                                   "groups": ["wallet", "other"], "phone": True})
        ok, cleaned = sc_stats.validate_tlm_settings({"keep_days": None})
        self.assertTrue(ok)
        self.assertEqual(cleaned, {"keep_days": None})

    def test_settings_validation_max_mb(self):
        for v in (None, 5000, 1000, 250):
            ok, cleaned = sc_stats.validate_tlm_settings({"max_mb": v})
            self.assertTrue(ok, v)
            self.assertEqual(cleaned, {"max_mb": v})
        for v in (123, "1000", True, -1):
            ok, err = sc_stats.validate_tlm_settings({"max_mb": v})
            self.assertFalse(ok, v)
            self.assertIsInstance(err, str)

    def test_default_space_limit(self):
        self.assertEqual(sc_stats.TLM_DEFAULTS["max_mb"], 1000)
        self.assertIn(sc_stats.TLM_DEFAULTS["max_mb"], sc_stats.TLM_MAX_MB)


class ChannelNowTests(_TmpDir):
    def test_tlm_channel_now(self):
        old, new = os.path.join(self.tmp, "LIVE"), os.path.join(self.tmp, "PTU")
        for d in (old, new):
            os.makedirs(d)
            with open(os.path.join(d, "Game.log"), "w") as fh:
                fh.write("x")
        t = time.time()
        os.utime(os.path.join(old, "Game.log"), (t - 600, t - 600))
        os.utime(os.path.join(new, "Game.log"), (t - 5, t - 5))
        with mock.patch.dict(sc_stats._CHANNEL_DIRS, {"LIVE": old, "PTU": new}, clear=True):
            self.assertEqual(sc_stats.tlm_channel_now(), ("PTU", new))
            os.utime(os.path.join(old, "Game.log"), (t, t))
            self.assertEqual(sc_stats.tlm_channel_now(), ("LIVE", old))

    def test_tlm_channel_now_stale_log(self):
        d = os.path.join(self.tmp, "LIVE")
        os.makedirs(d)
        log = os.path.join(d, "Game.log")
        with open(log, "w") as fh:
            fh.write("x")
        t = time.time()
        os.utime(log, (t - 660, t - 660))
        with mock.patch.dict(sc_stats._CHANNEL_DIRS, {"LIVE": d}, clear=True):
            self.assertIsNone(sc_stats.tlm_channel_now())
            os.utime(log, (t - 500, t - 500))
            self.assertEqual(sc_stats.tlm_channel_now(), ("LIVE", d))

    def test_tlm_channel_now_none(self):
        with mock.patch.dict(sc_stats._CHANNEL_DIRS, {"LIVE": os.path.join(self.tmp, "x")},
                             clear=True):
            self.assertIsNone(sc_stats.tlm_channel_now())
        with mock.patch.dict(sc_stats._CHANNEL_DIRS, {}, clear=True):
            self.assertIsNone(sc_stats.tlm_channel_now())


class RouteTests(unittest.TestCase):
    def test_live_get_counts_as_seen(self):
        state = {"seen": 0.0}
        seen = lambda: state.__setitem__("seen", time.time())  # noqa: E731
        with mock.patch.object(sc_stats.sc_livelog, "get_state", lambda *a, **k: {"place": None}):
            body = sc_stats.tlm_api_get("/api/telemetry/live", lambda: {"state": "live", "x": float("nan")},
                                        lambda: {"status": 1}, seen)
        self.assertEqual(json.loads(body), {"state": "live", "x": None, "names": {}, "log": {"place": None}})
        self.assertGreater(state["seen"], time.time() - 5)
        state["seen"] = 0.0
        body = sc_stats.tlm_api_get("/api/telemetry/status", lambda: {}, lambda: {"status": 1}, seen)
        self.assertEqual(json.loads(body), {"status": 1})
        self.assertEqual(state["seen"], 0.0)

    def test_local_request(self):
        ok = sc_stats.local_request_ok
        for host in ("127.0.0.1:7931", "localhost:7931", "LOCALHOST:7931"):
            self.assertTrue(ok("GET", {"Host": host}, 7931))
            self.assertTrue(ok("POST", {"Host": host}, 7931))
        for origin in ("http://127.0.0.1:7931", "http://localhost:7931"):
            self.assertTrue(ok("POST", {"Host": "127.0.0.1:7931", "Origin": origin}, 7931))
        for host in ("evil.example:7931", "127.0.0.1:7878", "127.0.0.1", "", None):
            self.assertFalse(ok("GET", {"Host": host} if host is not None else {}, 7931), host)
        for origin in ("http://evil.example", "http://127.0.0.1:7878", "null",
                       "https://127.0.0.1:7931", ""):
            self.assertFalse(ok("POST", {"Host": "127.0.0.1:7931", "Origin": origin}, 7931), origin)
        # a foreign Origin on a GET is the browser's business: the page can't read the reply
        self.assertTrue(ok("GET", {"Host": "127.0.0.1:7931", "Origin": "http://evil.example"}, 7931))


class JsonSafeTests(unittest.TestCase):
    def test_non_finite_floats_become_none(self):
        out = sc_stats._json_safe({"a": float("nan"), "b": [1.5, float("inf")], "c": {"d": 2}})
        self.assertEqual(json.loads(json.dumps(out, allow_nan=False)),
                         {"a": None, "b": [1.5, None], "c": {"d": 2}})


if __name__ == "__main__":
    unittest.main()
