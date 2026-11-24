#!/usr/bin/env python3
"""Cut a test fixture from a raw telemetry capture. Stdlib only.

    python tools/make_tlm_fixture.py SRC DST --start 98 --end 185

SRC is a capture from `telemetry_probe.py --raw` (records of "<dI" time, length + message)
with its SRC.desc next to it. Keeps the records from --start to --end seconds after the
first one, times rebased to 0. Every kept message gets actor.state.uec_balance set to --uec
(default 1,000,000). Nothing else in the message changes. Writes DST and DST.desc.

Each rewritten message is decoded and checked against the original: same fields, only the
balance differs. Developer tool, not part of the app.
"""
import argparse
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_telemetry as tlm  # noqa: E402

# TelemetryStreamResponse.actor (6) -> ActorFrame.state (2) -> ActorState.uec_balance (4)
BALANCE_PATH = (6, 2, 4)
WALLET = "actor.state.uec_balance"


def read_raw(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    out, i = [], 0
    while i < len(raw):
        ts, n = struct.unpack_from("<dI", raw, i)
        i += 12
        out.append((ts, raw[i:i + n]))
        i += n
    return out


def read_desc(path):
    with open(path, "rb") as fh:
        b = fh.read()
    out, i = [], 0
    while i < len(b):
        n = struct.unpack_from("<I", b, i)[0]
        name = b[i + 4:i + 4 + n].decode()
        i += 4 + n
        m = struct.unpack_from("<I", b, i)[0]
        out.append((name, b[i + 4:i + 4 + m]))
        i += 4 + m
    return out


def _varint(n):
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


def _spans(b):
    """(field_no, wire_type, start, value_start, end) for each field, byte offsets into b."""
    i, n = 0, len(b)
    while i < n:
        start = i
        key, i = tlm._varint(b, i)
        f, w = key >> 3, key & 7
        if w == 0:
            vs = i
            _, i = tlm._varint(b, i)
        elif w == 1:
            vs, i = i, i + 8
        elif w == 2:
            ln, vs = tlm._varint(b, i)
            i = vs + ln
        elif w == 5:
            vs, i = i, i + 4
        else:
            raise ValueError("wire type %d" % w)
        if i > n:
            raise ValueError("truncated message")
        yield f, w, start, vs, i


def set_varint(msg, path, value):
    """msg with the varint at field path `path` set to `value`. Nested messages on the way
    are re-serialised with their new length, all other bytes are kept as they are. Returns
    (new bytes, number of fields replaced)."""
    head, rest = path[0], path[1:]
    out = bytearray()
    hits = 0
    for f, w, start, vs, end in _spans(msg):
        if f != head:
            out += msg[start:end]
        elif not rest and w == 0:
            out += msg[start:vs] + _varint(value)
            hits += 1
        elif rest and w == 2:
            inner, n = set_varint(msg[vs:end], rest, value)
            out += _varint((f << 3) | 2) + _varint(len(inner)) + inner
            hits += n
        else:
            out += msg[start:end]
    return bytes(out), hits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=float("inf"))
    ap.add_argument("--uec", type=int, default=1000000)
    a = ap.parse_args(argv)

    if os.path.abspath(a.src) == os.path.abspath(a.dst):
        sys.exit("SRC and DST must differ")
    schema = tlm.Schema.from_blobs(read_desc(a.src + ".desc"))
    recs = read_raw(a.src)
    if not recs:
        sys.exit("empty capture")
    t0 = recs[0][0]
    kept = [(ts, b) for ts, b in recs if a.start <= ts - t0 <= a.end]
    if not kept:
        sys.exit("no records in that window")
    k0 = kept[0][0]
    out = bytearray()
    replaced = 0
    old_values = set()
    for ts, b in kept:
        nb, hits = set_varint(b, BALANCE_PATH, a.uec)
        replaced += hits
        before = tlm.flatten(schema.decode(tlm.RESP_TYPE, b))
        after = tlm.flatten(schema.decode(tlm.RESP_TYPE, nb))
        if WALLET in before:
            old_values.add(before[WALLET])
            before[WALLET] = a.uec
        if before != after:
            sys.exit("rewrite changed more than the balance at t=%.3f" % (ts - t0))
        out += struct.pack("<dI", ts - k0, len(nb)) + nb
    for v in old_values - {a.uec}:
        if (_varint(4 << 3) + _varint(v)) in out:
            sys.exit("old balance bytes still in the output")
    with open(a.dst, "wb") as fh:
        fh.write(out)
    shutil.copyfile(a.src + ".desc", a.dst + ".desc")
    print("%s: %d of %d records (%.1f-%.1f s), %d bytes, balance replaced in %d"
          % (a.dst, len(kept), len(recs), kept[0][0] - t0, kept[-1][0] - t0, len(out), replaced))


if __name__ == "__main__":
    main()
