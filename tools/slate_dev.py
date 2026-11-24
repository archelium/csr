"""Dev preview for the Kestrel slate: the page plus a telemetry capture replayed in a loop.

  py -3 tools/slate_dev.py --fixture takeoff [--port 7895] [--log path/to/Game.log] [--state waiting] [--range -170:]
  py -3 tools/slate_dev.py --recording %LOCALAPPDATA%/CSR/telemetry/LIVE/<file>.jsonl.gz [--start 1500] [--speed 4]

Serves /live, /api/telemetry/live and /api/telemetry/ship like CSR does, with no key,
plus /slate_audit.js (load it in the page, then auditLive({touch: true})).
For layout work and the browser audit only. Nothing here ships in the exe."""
import argparse
import re
import zlib
import http.server
import json
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import sc_livelog  # noqa: E402
import sc_slate  # noqa: E402
import sc_stats  # noqa: E402
import sc_telemetry as tlm  # noqa: E402
from tests.tlm_helpers import read_desc, read_raw  # noqa: E402

FIX = os.path.join(HERE, "tests", "fixtures", "telemetry")


def frames(name):
    s = tlm.Schema.from_blobs(read_desc(os.path.join(FIX, f"live-4.10-{name}.bin.desc")))
    out = []
    for _, msg in read_raw(os.path.join(FIX, f"live-4.10-{name}.bin")):
        f = s.decode(tlm.RESP_TYPE, msg)
        out.append(({k: v for k, v in f.items() if k != "events"}, tlm.frame_events(f)))
    return out


IDX_RE = re.compile(r"^(.*)\[(\d+)\]$")


def unflatten(flat):
    """A recording row's dotted keys ("weapons.weapons[3].name") back into the stream's nested frame."""
    out = {}
    for key, val in flat.items():
        cur, parts = out, key.split(".")
        for i, part in enumerate(parts):
            m = IDX_RE.match(part)
            name, idx = (m.group(1), int(m.group(2))) if m else (part, None)
            last = i == len(parts) - 1
            if idx is None:
                if last:
                    cur[name] = val
                else:
                    cur = cur.setdefault(name, {})
            else:
                lst = cur.setdefault(name, [])
                while len(lst) <= idx:
                    lst.append({})
                if last:
                    lst[idx] = val
                else:
                    cur = lst[idx]
    return out


def recording(path):
    """[(t, flat-delta row)] from a CSR recording (.jsonl.gz, still open is fine)."""
    raw = open(path, "rb").read()
    txt = zlib.decompressobj(31).decompress(raw).decode("utf-8", "replace")
    rows = []
    for ln in txt.splitlines()[1:]:
        try:
            rows.append(json.loads(ln))
        except ValueError:
            pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default="takeoff", choices=("takeoff", "landing"))
    ap.add_argument("--port", type=int, default=7895)
    ap.add_argument("--log", default=None, help="a Game.log to tail for the log state")
    ap.add_argument("--state", default="live", choices=("live", "waiting"))
    ap.add_argument("--range", default=":", help="frames to loop, python slice a:b (landing -170: is on foot)")
    ap.add_argument("--recording", default=None, help="a CSR recording (.jsonl.gz) to replay instead of a fixture")
    ap.add_argument("--start", type=float, default=0.0, help="recording: start at this many seconds in")
    ap.add_argument("--speed", type=float, default=1.0, help="recording: playback speed")
    a = ap.parse_args()
    lo, hi = (int(x) if x else None for x in a.range.split(":"))
    fr = frames(a.fixture)[lo:hi] if not a.recording else None
    rec = recording(a.recording) if a.recording else None
    play = {"i": 0, "flat": {}, "ev": []}
    t0 = time.time()

    def rec_frame():
        """Apply recording rows up to the play clock; loops at the end."""
        end = rec[-1]["t"] if rec else 0
        clock = a.start + (time.time() - t0) * a.speed
        if end:
            clock = a.start + (clock - a.start) % max(1.0, end - a.start)
        if play["i"] and rec[play["i"] - 1]["t"] > clock:
            play.update(i=0, flat={})
        while play["i"] < len(rec) and rec[play["i"]]["t"] <= clock:
            r = rec[play["i"]]
            for k in r.get("-") or []:
                play["flat"].pop(k, None)
            play["flat"].update({k: v for k, v in r.items() if k not in ("t", "-", "ev")})
            for e in r.get("ev") or []:
                play["ev"].append({"t": time.time() - (clock - r["t"]) / a.speed, "type": e})
            play["i"] += 1
        play["ev"] = play["ev"][-50:]
        return unflatten(play["flat"]), list(play["ev"])
    tail = sc_livelog.LiveTail(lambda: a.log, lambda c: sc_stats.place_name(c)[0]) if a.log else None

    def live():
        if a.state != "live":
            return {"state": a.state, "reason": "", "snapshot": {}, "events": []}
        if rec is not None:
            snap, evs = rec_frame()
            return {"state": "live", "reason": "", "snapshot": snap, "events": evs}
        i = int((time.time() - t0) * 14) % len(fr)
        snap, evs = fr[i]
        return {"state": "live", "reason": "", "snapshot": snap, "events": [{"t": time.time(), "type": e} for e in evs]}

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            b = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            p = urllib.parse.urlsplit(self.path)
            if p.path == "/live":
                self._send(200, sc_slate.slate_html().replace("/*__FONTS__*/", ""), "text/html; charset=utf-8")
            elif p.path == "/api/telemetry/live":
                data = live()
                if tail:
                    tail.poll()
                data["log"] = tail.state() if tail else sc_livelog.LogState().state()
                data["names"] = sc_stats._slate_gun_names(data.get("snapshot"), data["log"]["loadout"].values())
                data["lastShip"] = sc_stats._slate_last_ship(data.get("snapshot"))
                self._send(200, json.dumps(sc_stats._json_safe(data), default=str))
            elif p.path == "/api/telemetry/ship":
                body = sc_stats.tlm_api_get(self.path, None, None, None)
                self._send(404, "{}") if body is None else self._send(200, body)
            elif p.path == "/slate_audit.js":
                with open(os.path.join(HERE, "tools", "slate_audit.js"), encoding="utf-8") as fh:
                    self._send(200, fh.read(), "text/javascript; charset=utf-8")
            else:
                self._send(404, "{}")

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"slate dev: http://127.0.0.1:{a.port}/live  ({a.recording or a.fixture}, {len(rec or fr)} rows)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
