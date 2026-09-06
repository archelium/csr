#!/usr/bin/env python3
"""Take the README screenshots from a running CSR (python sc_stats.py --serve).

Drives headless Edge over the DevTools protocol with nothing but the standard
library — a 40-line websocket client is all it takes — so there is no Playwright
to install. Full-page capture at 1.25× to match the earlier shots (1875 px wide).

    python tools/shoot.py blueprints            -> docs/img/blueprints.png (whole page)
    python tools/shoot.py blueprints:#bpBoard   -> cropped at the bottom of that element
    python tools/shoot.py overview stability    -> one file each

Developer tool, not part of the app.
"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
URL = "http://127.0.0.1:7878/"
PORT = 9333
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "img")


class WS:
    """Minimal RFC 6455 client: text frames only, client-side masking, no extensions."""

    def __init__(self, url):
        host, rest = url.split("://", 1)[1].split("/", 1)
        h, p = host.split(":")
        self.s = socket.create_connection((h, int(p)))
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{rest} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                        f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        assert b" 101 " in buf.split(b"\r\n", 1)[0], buf[:200]
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.n = 0

    def send(self, obj):
        data = json.dumps(obj).encode()
        head = bytearray([0x81])
        L = len(data)
        if L < 126:
            head.append(0x80 | L)
        elif L < 65536:
            head.append(0x80 | 126); head += struct.pack(">H", L)
        else:
            head.append(0x80 | 127); head += struct.pack(">Q", L)
        mask = os.urandom(4)
        self.s.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(1 << 20)
            if not chunk:
                raise ConnectionError("socket closed")
            self.buf += chunk

    def recv(self):
        self._need(2)
        b1 = self.buf[1]; L = b1 & 0x7F; off = 2
        if L == 126:
            self._need(4); L = struct.unpack(">H", self.buf[2:4])[0]; off = 4
        elif L == 127:
            self._need(10); L = struct.unpack(">Q", self.buf[2:10])[0]; off = 10
        self._need(off + L)
        payload = self.buf[off:off + L]; self.buf = self.buf[off + L:]
        return json.loads(payload.decode())

    def call(self, method, **params):
        self.n += 1
        self.send({"id": self.n, "method": method, "params": params})
        while True:
            m = self.recv()
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})


def main(sections):
    prof = tempfile.mkdtemp(prefix="csr-shoot-")
    edge = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                             f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                             "--window-size=1525,1100", "--force-device-scale-factor=1.25",
                             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("Edge did not come up")
        ws = WS(page["webSocketDebuggerUrl"])
        ws.call("Page.enable"); ws.call("Runtime.enable")
        # the window flag is not reliable in headless; pin the viewport here instead
        ws.call("Emulation.setDeviceMetricsOverride", width=1500, height=1100,
                deviceScaleFactor=1.25, mobile=False)
        ev = lambda js: ws.call("Runtime.evaluate", expression=js, returnByValue=True).get("result", {}).get("value")
        # first load: plant the "already answered" flags so the account coach-mark stays
        # down, then load for real
        ws.call("Page.navigate", url=URL); time.sleep(2)
        ev("localStorage.setItem('csr_main_hint','1'); 1")
        ws.call("Page.navigate", url=URL)
        for _ in range(120):                     # wait for data + the scan terminal to clear
            time.sleep(0.5)
            if ev("typeof DATA==='object' && !!document.querySelector('#content .gsec, #content .card') && !(document.querySelector('#csrTerm')||{}).classList?.contains('on')"):
                break
        time.sleep(1.5)
        os.makedirs(OUT, exist_ok=True)
        for spec in sections:
            # "blueprints" = the whole page; "blueprints:#bpBoard" = down to that element
            sec, _, to = spec.partition(":")
            ev(f"gotoSec('{sec}'); window.scrollTo(0,0); 1")
            time.sleep(2.5)                      # charts + artwork settle
            ev("document.querySelectorAll('.coach.on').forEach(e=>e.classList.remove('on')); 1")
            h = ev("Math.ceil(document.documentElement.scrollHeight)")
            if to:
                h = ev(f"Math.ceil(document.querySelector({to!r}).getBoundingClientRect().bottom + window.scrollY + 28)")
            w = ev("document.documentElement.clientWidth")
            shot = ws.call("Page.captureScreenshot", format="png", captureBeyondViewport=True,
                           clip={"x": 0, "y": 0, "width": w, "height": h, "scale": 1})
            path = os.path.join(OUT, f"{sec}.png")
            open(path, "wb").write(base64.b64decode(shot["data"]))
            print(f"wrote {path}  ({w}x{h} css px)")
    finally:
        edge.kill()


if __name__ == "__main__":
    main(sys.argv[1:] or ["blueprints"])
