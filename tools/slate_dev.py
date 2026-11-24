"""Dev preview for the Kestrel slate: the page plus a telemetry capture replayed in a loop.

  py -3 tools/slate_dev.py --fixture takeoff [--port 7895] [--log path/to/Game.log] [--state waiting]

Serves /live, /api/telemetry/live and /api/telemetry/ship like CSR does, with no key.
For layout work and the browser audit only. Nothing here ships in the exe."""
import argparse
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default="takeoff", choices=("takeoff", "landing"))
    ap.add_argument("--port", type=int, default=7895)
    ap.add_argument("--log", default=None, help="a Game.log to tail for the log state")
    ap.add_argument("--state", default="live", choices=("live", "waiting"))
    a = ap.parse_args()
    fr = frames(a.fixture)
    t0 = time.time()
    tail = sc_livelog.LiveTail(lambda: a.log) if a.log else None

    def live():
        if a.state != "live":
            return {"state": a.state, "reason": "", "snapshot": {}, "events": []}
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
                data["names"] = sc_stats._slate_gun_names(data.get("snapshot"))
                if tail:
                    tail.poll()
                data["log"] = tail.state() if tail else sc_livelog.LogState().state()
                self._send(200, json.dumps(sc_stats._json_safe(data), default=str))
            elif p.path == "/api/telemetry/ship":
                body = sc_stats.tlm_api_get(self.path, None, None, None)
                self._send(404, "{}") if body is None else self._send(200, body)
            else:
                self._send(404, "{}")

        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"slate dev: http://127.0.0.1:{a.port}/live  ({a.fixture}, {len(fr)} frames)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
