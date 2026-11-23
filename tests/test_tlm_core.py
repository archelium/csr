"""Telemetry core: protobuf decode, schema cache, gRPC stream reassembly."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_telemetry as tlm  # noqa: E402
from tests.tlm_helpers import (RESP, FakeGrpcServer, enc_field, enc_varint, exe_bytes,  # noqa: E402
                               f32, make_descriptor)


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


if __name__ == "__main__":
    unittest.main()
