"""Telemetry core: protobuf decode, schema cache, gRPC stream reassembly."""
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_telemetry as tlm  # noqa: E402
from tests.tlm_helpers import (RESP, FakeGrpcServer, enc_field, enc_varint, exe_bytes,  # noqa: E402
                               f32, make_descriptor)


PKG_SPAN = tlm.PKG + ".Span"


def schema():
    return tlm.Schema.from_blobs(make_descriptor())


def gauge(cur, mx):
    return enc_field(1, 5, f32(cur)) + enc_field(2, 5, f32(mx))


class DecodeTests(unittest.TestCase):
    def test_decode_nested_and_enums(self):
        vitals = enc_field(1, 2, gauge(80, 100))
        state = enc_field(1, 0, 1) + enc_field(4, 0, 1000000)
        actor = enc_field(1, 2, vitals) + enc_field(2, 2, state)
        out = schema().decode(RESP, enc_field(6, 2, actor))
        self.assertEqual(out, {"actor": {"vitals": {"health": {"current": 80.0, "max": 100.0}},
                                         "state": {"locomotion": "ON_FOOT", "uec_balance": 1000000}}})

    def test_decode_negative_int32(self):
        self.assertEqual(len(enc_varint(-1)), 10)
        actor = enc_field(1, 2, enc_field(8, 0, -1))
        out = schema().decode(RESP, enc_field(6, 2, actor))
        self.assertEqual(out["actor"]["vitals"]["intoxication_level"], -1)

    def test_decode_packed_floats(self):
        flight = enc_field(2, 2, enc_field(1, 2, f32(0.5) + f32(1.0)))
        out = schema().decode(RESP, enc_field(7, 2, flight))
        self.assertEqual(out["vehicle"]["flight"]["speeds"], [0.5, 1.0])

    def test_enum_prefix_stripped(self):
        ev = enc_field(1, 2, enc_field(1, 0, 6))
        out = schema().decode(RESP, enc_field(5, 2, ev))
        self.assertEqual(out["events"]["events"], [{"type": "ACTOR_DOWNED"}])

    def test_schema_json_roundtrip(self):
        s = schema()
        s2 = tlm.Schema.from_json(s.to_json())
        raw = enc_field(6, 2, enc_field(2, 2, enc_field(1, 0, 1) + enc_field(3, 2, b"seat")))
        self.assertEqual(s2.decode(RESP, raw), s.decode(RESP, raw))
        self.assertEqual(s2.hash, s.hash)
        self.assertEqual(len(s.hash), 12)
        self.assertEqual(s2.services, s.services)


class SchemaFixesTests(unittest.TestCase):
    def test_only_game_protos_kept(self):
        from tests import tlm_helpers as h
        other = h._file("opentelemetry/proto/trace/v1/trace.proto",
                        [h._msg("Span", [h._field("name", 1, tlm.T_STRING)])])
        blobs = make_descriptor() + [("opentelemetry/proto/trace/v1/trace.proto", other)]
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "StarCitizen.exe")
            with open(exe, "wb") as fh:
                fh.write(exe_bytes(blobs))
            found = tlm.descriptor_blobs(exe)
            names = [n for n, _ in found]
            self.assertEqual(sorted(names), ["sc/game/telemetry/v1/api.proto",
                                             "sc/game/telemetry/v1/types.proto"])
            self.assertEqual(tlm.Schema.from_blobs(found).hash, schema().hash)
            self.assertNotIn(PKG_SPAN, schema().messages)

    def test_mmap_closed(self):
        closed = []
        real = tlm.mmap.mmap

        class Spy:
            def __init__(self, *a, **k):
                self._m = real(*a, **k)

            def __enter__(self):
                self._m.__enter__()
                return self._m

            def __exit__(self, *e):
                closed.append(True)
                return self._m.__exit__(*e)

            def close(self):
                closed.append(True)
                self._m.close()

        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "x.exe")
            with open(exe, "wb") as fh:
                fh.write(exe_bytes(make_descriptor()))
            with mock.patch.object(tlm.mmap, "mmap", Spy):
                tlm.descriptor_blobs(exe)
        self.assertTrue(closed)

    def test_fixed_ints(self):
        from tests import tlm_helpers as h
        P = tlm.PKG
        fields = [h._field("a", 1, 7), h._field("b", 2, 6), h._field("c", 3, 15),
                  h._field("d", 4, 16), h._field("e", 5, 15, h.LABEL_REP)]
        f = h._file("sc/game/telemetry/v1/fx.proto", [h._msg("Fx", fields)])
        s = tlm.Schema.from_blobs([("sc/game/telemetry/v1/fx.proto", f)])
        raw = (enc_field(1, 5, struct.pack("<I", 4000000000))
               + enc_field(2, 1, struct.pack("<Q", 2 ** 63 + 5))
               + enc_field(3, 5, struct.pack("<i", -7))
               + enc_field(4, 1, struct.pack("<q", -9))
               + enc_field(5, 2, struct.pack("<ii", -1, 2)))
        self.assertEqual(s.decode(P + ".Fx", raw),
                         {"a": 4000000000, "b": 2 ** 63 + 5, "c": -7, "d": -9, "e": [-1, 2]})
        self.assertEqual(tlm.TYPE_NAMES[7], "fixed32")
        self.assertEqual(tlm.TYPE_NAMES[16], "sfixed64")


class LoadSchemaTests(unittest.TestCase):
    def test_load_schema_cache(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "StarCitizen.exe")
            with open(exe, "wb") as fh:
                fh.write(exe_bytes(make_descriptor()))
            first = tlm.load_schema(exe, d, "LIVE")
            self.assertIsNotNone(first)
            self.assertTrue(os.path.isfile(os.path.join(d, "schema-LIVE.json")))
            with mock.patch.object(tlm, "descriptor_blobs", side_effect=AssertionError("rescanned")):
                second = tlm.load_schema(exe, d, "LIVE")
            self.assertEqual(second.hash, first.hash)
            self.assertEqual(second.to_json(), first.to_json())
            os.remove(exe)
            third = tlm.load_schema(exe, d, "LIVE")
            self.assertEqual(third.hash, first.hash)

    def test_load_schema_nothing_available(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "x.exe")
            with open(exe, "wb") as fh:
                fh.write(b"MZ" + b"\x00" * 100)
            self.assertIsNone(tlm.load_schema(exe, d, "LIVE"))
            self.assertIsNone(tlm.load_schema(os.path.join(d, "missing.exe"), d, "LIVE"))


class StreamTests(unittest.TestCase):
    def test_stream_reassembles_split_messages(self):
        msgs = [enc_field(6, 2, enc_field(2, 2, enc_field(4, 0, n))) for n in (1, 2, 3)]
        with FakeGrpcServer(msgs, gap=0.01) as srv:
            call = tlm.GrpcStream(tlm.HOST, srv.port, tlm.STREAM_PATH)
            got = []
            with self.assertRaises(ConnectionError) as cm:
                for m in call.messages():
                    got.append(m)
            call.close()
        self.assertEqual(got, msgs)
        self.assertIn("trailers", str(cm.exception))


    def test_stream_ticks_in_silence_and_resumes_split_frame(self):
        import socket
        import threading
        import time
        msg = enc_field(6, 2, enc_field(2, 2, enc_field(4, 0, 42)))
        g = b"\x00" + struct.pack(">I", len(msg)) + msg
        data = tlm._frame(tlm.DATA, 0, 1, g)
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def serve():
            c, _ = srv.accept()
            c.recv(65536)
            c.sendall(tlm._frame(tlm.SETTINGS, 0, 0))
            time.sleep(0.25)               # silence
            c.sendall(data[:4])            # cut inside the frame header
            time.sleep(0.25)
            c.sendall(data[4:12])          # cut inside the payload
            time.sleep(0.25)
            c.sendall(data[12:])
            time.sleep(0.5)
            c.close()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        call = tlm.GrpcStream(tlm.HOST, srv.getsockname()[1], tlm.STREAM_PATH, read_timeout=0.05)
        got = []
        try:
            for m in call.messages():
                got.append(m)
                if m is not None:
                    break
        finally:
            call.close()
            srv.close()
        self.assertEqual(got[-1], msg)
        self.assertGreaterEqual(got.count(None), 3)
        self.assertEqual([m for m in got if m is not None], [msg])


if __name__ == "__main__":
    unittest.main()
