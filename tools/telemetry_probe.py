#!/usr/bin/env python3
"""Probe Star Citizen's local game-telemetry gRPC stream. Stdlib only.

    python tools/telemetry_probe.py                    -> print a snapshot every second
    python tools/telemetry_probe.py --every 0          -> print every frame
    python tools/telemetry_probe.py --out frames.jsonl -> also record every decoded frame
    python tools/telemetry_probe.py --raw cap.bin      -> also append every raw message
    python tools/telemetry_probe.py --schema           -> dump the schema embedded in the exe

The game must run with `grpc.client.server.enabled = true` in user.cfg. The decode, schema
and gRPC client code lives in sc_telemetry.py.

--raw writes records of struct "<dI" (time, length) + message. The exe's telemetry
descriptor blobs go once to <PATH>.desc as (u32 name length, name, u32 blob length, blob).

Developer tool, not part of the app.
"""
import argparse
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_telemetry as tlm  # noqa: E402


def write_desc(path, blobs):
    with open(path, "wb") as fh:
        for name, blob in blobs:
            nb = name.encode()
            fh.write(struct.pack("<I", len(nb)) + nb + struct.pack("<I", len(blob)) + blob)


# --- CLI --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=tlm.HOST)
    ap.add_argument("--port", type=int, default=tlm.PORT)
    ap.add_argument("--exe", help="StarCitizen.exe to read the schema from")
    ap.add_argument("--every", type=float, default=1.0, help="seconds between printed snapshots, 0 = every frame")
    ap.add_argument("--out", help="append every decoded frame to this .jsonl file")
    ap.add_argument("--raw", help="append every raw message to this file, descriptor blobs to PATH.desc")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this long, 0 = run until Ctrl+C")
    ap.add_argument("--schema", action="store_true", help="print the embedded schema and exit")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    exe = a.exe or tlm.find_exe()
    if not exe:
        sys.exit("StarCitizen.exe not found, pass --exe")
    blobs = tlm.descriptor_blobs(exe)
    schema = tlm.Schema.from_blobs(blobs)
    if not schema.messages:
        sys.exit(f"no telemetry schema in {exe}")
    if a.schema:
        print(f"# {exe}")
        schema.dump()
        return

    resp_type = tlm.RESP_TYPE
    print(f"schema: {exe} ({len(schema.messages)} messages)")
    try:
        call = tlm.GrpcStream(a.host, a.port, tlm.STREAM_PATH)
    except OSError as e:
        sys.exit(f"can't reach {a.host}:{a.port} ({e}). Game not running, or telemetry is off.")
    print(f"connected to {a.host}:{a.port}, waiting for frames")

    out = open(a.out, "a", encoding="utf-8") if a.out else None
    raw_out = None
    if a.raw:
        write_desc(a.raw + ".desc", blobs)
        raw_out = open(a.raw, "ab")
    start = last_print = time.monotonic()
    frames = 0
    state = {}
    try:
        for raw in call.messages():
            now = time.monotonic()
            if raw_out:
                raw_out.write(struct.pack("<dI", time.time(), len(raw)) + raw)
            frame = schema.decode(resp_type, raw)
            frames += 1
            if out:
                out.write(json.dumps({"t": round(time.time(), 3), **frame}) + "\n")
            for ev in frame.get("events", {}).get("events", []):
                print(f"[{now - start:7.1f}s] EVENT {ev.get('type')}")
            # Each frame is a full snapshot. Missing keys mean proto3 defaults (false, 0, no vehicle).
            state = {k: v for k, v in frame.items() if k != "events"}
            if a.every == 0 or now - last_print >= a.every:
                rate = frames / max(now - start, 1e-6)
                print(f"--- {now - start:.1f}s, {frames} frames, {rate:.1f}/s, {len(raw)} bytes")
                print(json.dumps(state, indent=1 if a.every else None))
                last_print = now
            if a.seconds and now - start >= a.seconds:
                break
    except KeyboardInterrupt:
        pass
    except ConnectionError as e:
        print(f"stopped: {e}")
    finally:
        if out:
            out.close()
        if raw_out:
            raw_out.close()
        print(f"{frames} frames")


if __name__ == "__main__":
    main()
