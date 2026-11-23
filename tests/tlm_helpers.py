"""Helpers for telemetry tests: protobuf encoder, synthetic descriptor, fake gRPC server."""
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_telemetry as tlm  # noqa: E402

PKG = tlm.PKG
RESP = tlm.RESP_TYPE


def enc_varint(n):
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def enc_field(no, wire, payload):
    """One field. wire 0: payload is an int. 2: bytes. 1 and 5: raw fixed-width bytes."""
    key = enc_varint((no << 3) | wire)
    if wire == 0:
        return key + enc_varint(payload)
    if wire == 2:
        return key + enc_varint(len(payload)) + payload
    return key + payload


def f32(x):
    return struct.pack("<f", x)


# --- synthetic descriptor ---------------------------------------------------------

LABEL_OPT, LABEL_REP = 1, 3


def _s(no, text):
    return enc_field(no, 2, text.encode())


def _field(name, no, ftype, label=LABEL_OPT, type_name=""):
    b = _s(1, name) + enc_field(3, 0, no) + enc_field(4, 0, label) + enc_field(5, 0, ftype)
    if type_name:
        b += _s(6, "." + type_name)
    return b


def _msg(name, fields, enums=()):
    b = _s(1, name)
    for f in fields:
        b += enc_field(2, 2, f)
    for e in enums:
        b += enc_field(4, 2, e)
    return b


def _enum(name, values):
    b = _s(1, name)
    for vname, num in values:
        b += enc_field(2, 2, _s(1, vname) + enc_field(2, 0, num))
    return b


def _file(fname, msgs=(), enums=(), services=()):
    b = _s(1, fname) + _s(2, PKG)
    for m in msgs:
        b += enc_field(4, 2, m)
    for e in enums:
        b += enc_field(5, 2, e)
    for s in services:
        b += enc_field(6, 2, s)
    return b


def make_descriptor():
    """Small types.proto + api.proto pair using the real telemetry names."""
    T = tlm
    gauge = _msg("Gauge", [_field("current", 1, T.T_FLOAT), _field("max", 2, T.T_FLOAT)])
    vitals = _msg("ActorVitals", [
        _field("health", 1, T.T_MESSAGE, type_name=PKG + ".Gauge"),
        _field("stamina", 2, T.T_MESSAGE, type_name=PKG + ".Gauge"),
        _field("body_temp", 7, T.T_FLOAT),
        _field("intoxication_level", 8, T.T_INT32),
    ])
    astate = _msg("ActorState", [
        _field("locomotion", 1, T.T_ENUM, type_name=PKG + ".ActorLocomotionState"),
        _field("seat", 3, T.T_STRING),
        _field("uec_balance", 4, T.T_UINT64),
    ])
    loco = _enum("ActorLocomotionState", [
        ("ACTOR_LOCOMOTION_STATE_UNKNOWN", 0),
        ("ACTOR_LOCOMOTION_STATE_ON_FOOT", 1),
        ("ACTOR_LOCOMOTION_STATE_SWIMMING", 2),
    ])
    types = _file("sc/game/telemetry/v1/types.proto", [gauge, vitals, astate], [loco])

    actor = _msg("ActorFrame", [
        _field("vitals", 1, T.T_MESSAGE, type_name=PKG + ".ActorVitals"),
        _field("state", 2, T.T_MESSAGE, type_name=PKG + ".ActorState"),
    ])
    gtype = _enum("GameEventType", [
        ("GAME_EVENT_TYPE_UNSPECIFIED", 0),
        ("GAME_EVENT_TYPE_ACTOR_DOWNED", 6),
        ("GAME_EVENT_TYPE_VEHICLE_DESTROYED", 7),
    ])
    gevent = _msg("GameEvent", [
        _field("type", 1, T.T_ENUM, type_name=PKG + ".GameEvent.GameEventType"),
    ], enums=[gtype])
    events = _msg("GameEvents", [
        _field("events", 1, T.T_MESSAGE, LABEL_REP, PKG + ".GameEvent"),
    ])
    vstate = _msg("VehicleState", [_field("name", 1, T.T_STRING)])
    vflight = _msg("VehicleFlightInstruments", [_field("speeds", 1, T.T_FLOAT, LABEL_REP)])
    vehicle = _msg("VehicleFrame", [
        _field("state", 1, T.T_MESSAGE, type_name=PKG + ".VehicleState"),
        _field("flight", 2, T.T_MESSAGE, type_name=PKG + ".VehicleFlightInstruments"),
    ])
    resp = _msg("TelemetryStreamResponse", [
        _field("events", 5, T.T_MESSAGE, type_name=PKG + ".GameEvents"),
        _field("actor", 6, T.T_MESSAGE, type_name=PKG + ".ActorFrame"),
        _field("vehicle", 7, T.T_MESSAGE, type_name=PKG + ".VehicleFrame"),
    ])
    req = _msg("TelemetryStreamRequest", [])
    rpc = (_s(1, "TelemetryStream") + _s(2, "." + PKG + ".TelemetryStreamRequest")
           + _s(3, "." + RESP) + enc_field(6, 0, 1))
    svc = _s(1, "GameTelemetryService") + enc_field(2, 2, rpc)
    api = _file("sc/game/telemetry/v1/api.proto",
                [actor, gevent, events, vstate, vflight, vehicle, resp, req], services=[svc])
    return [("sc/game/telemetry/v1/types.proto", types), ("sc/game/telemetry/v1/api.proto", api)]


def exe_bytes(blobs):
    """A fake exe image: junk, then each blob followed by zero padding."""
    out = b"MZ\x90\x00junk\x01\x02" + b"\x00" * 64
    for _name, blob in blobs:
        out += blob + b"\x00" * 64
    return out


# --- fake gRPC server -------------------------------------------------------------

class FakeGrpcServer:
    """Serves `frames` (raw message bytes) on one connection, each split across two DATA
    frames, then trailers. `.port` is set once the context is entered."""

    def __init__(self, frames, gap=0.0):
        self.frames = frames
        self.gap = gap
        self.port = 0

    def __enter__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.srv.settimeout(10)
        self.port = self.srv.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.srv.close()
        self.thread.join(5)

    def _serve(self):
        try:
            c, _ = self.srv.accept()
        except OSError:
            return
        try:
            c.recv(65536)
            c.sendall(tlm._frame(tlm.SETTINGS, 0, 0) + tlm._frame(tlm.HEADERS, tlm.F_END_HEADERS, 1, b"\x88"))
            for msg in self.frames:
                g = b"\x00" + struct.pack(">I", len(msg)) + msg
                c.sendall(tlm._frame(tlm.DATA, 0, 1, g[:7]) + tlm._frame(tlm.DATA, 0, 1, g[7:]))
                if self.gap:
                    time.sleep(self.gap)
            c.sendall(tlm._frame(tlm.HEADERS, tlm.F_END_HEADERS | tlm.F_END_STREAM, 1,
                                 b"\x00\x0bgrpc-status\x010"))
            time.sleep(0.2)
        except OSError:
            pass
        finally:
            c.close()
