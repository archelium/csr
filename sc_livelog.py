"""Live Game.log state for the Kestrel slate.

`LiveTail` follows the newest channel's Game.log while the game runs and keeps a small
state: where you are, the zone, recent HUD notices, contracts and their objective,
ships destroyed nearby, injuries, the gear you put on and blueprints received.
A truncated or replaced log (new session) starts the state over.

The patterns are CSR's own (sc_stats.py). tests/test_livelog.py fails if they drift."""
import calendar
import os
import re
import threading
import time

NOTIF_RE = re.compile(r'Added notification "(.*?)" \[(\d+)\]')
CONTRACT_NOTIF_RE = re.compile(r"^Contract (Accepted|Complete|Failed):\s*(.+?)\s*$")
JURIS_RE = re.compile(r"^(?:Entered (.+?) Jurisdiction|Journal Entry Added: Jurisdiction: (.+?))\s*$")
INJURY_RE = re.compile(r"^(Minor|Moderate|Severe|Critical) Injury Detected - ([A-Za-z ]+?) - Tier (\d)", re.I)
VKILL_RE = re.compile(
    r"<Vehicle Destroyed> Vehicle '[^']+' \[(\d+)\] class '([^']+)' "
    r"\(owner player \[(\d+)\], driver \[\d+\]\) destroyed: reason \[(\w+)\] "
    r"level \[(\d+)\] -> \[(\d+)\] .*?killer '([^']*)' \[(\d+)\]")
BLUEPRINT_RE = re.compile(r'Received Blueprint: (.*?): " \[(\d+)\]')
PLACE_RE = re.compile(r"<RequestLocationInventory> Player\[([^\]]+)\] requested inventory for Location\[([^\]]+)\]")
HAUL_RE = re.compile(r"^New Objective: Deliver (\d+)/(\d+) SCU of (.+?) to (.+?):?\s*$")
ATTACH_RE = re.compile(r"AttachmentReceived> Player\[[^\]]+\] Attachment\[[^,]+,\s*([^,]+),")
ATTACH_PORT_RE = re.compile(r"Port\[([a-zA-Z0-9_]+)\]")
LINE_TS_RE = re.compile(r"^<(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d+)?Z>")

MAX_NOTICES = 40
MAX_KILLS = 30
MAX_CONTRACTS = 30
PARTS = {"head": "h", "torso": "t", "chest": "t", "body": "t", "left arm": "la", "right arm": "ra",
         "left leg": "ll", "right leg": "rl"}
LEVEL = {"minor": "min", "moderate": "mod", "severe": "sev", "critical": "sev"}
GROUPS = (("alert", ("injury", "destroyed", "crimestat", "hostile", "warning")),
          ("msn", ("contract", "objective")),
          ("money", ("auec", "awarded", "reward")),
          ("people", ("party", "friend")),
          ("ship", ("hangar", "docking", "vehicle")),
          ("zone", ("armistice", "jurisdiction", "monitored")))


def clean(text):
    """A HUD notice as the player read it, minus markup (same rules as sc_stats._notif_clean)."""
    t = re.sub(r"</?EM\d>", "", text or "").replace("\xa0", " ")
    t = re.sub(r"\[[^\]]*\bRep\]\*?", "", t)
    t = re.sub(r"\[BP\]\*?", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t.rstrip(":").strip()


def group_of(text):
    low = text.lower()
    for name, words in GROUPS:
        if any(w in low for w in words):
            return name
    return "tip"


def line_time(line):
    m = LINE_TS_RE.match(line)
    if not m:
        return None
    return float(calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0)))


class LogState:
    """State built from Game.log lines fed in order."""

    def __init__(self, place_name=None):
        self._place_name = place_name or (lambda code: None)
        self.reset()

    def reset(self):
        self._st = {"place": None, "jurisdiction": None, "armistice": None, "notices": [], "contracts": [],
                    "kills": [], "injuries": {}, "loadout": {}, "blueprints": 0}

    def state(self):
        s = self._st
        return {"place": s["place"], "jurisdiction": s["jurisdiction"], "armistice": s["armistice"],
                "notices": [dict(n) for n in s["notices"]], "contracts": [dict(c) for c in s["contracts"]],
                "kills": [dict(k) for k in s["kills"]], "injuries": dict(s["injuries"]),
                "loadout": dict(s["loadout"]), "blueprints": s["blueprints"]}

    def feed(self, line, t=None):
        if t is None:
            t = line_time(line) or time.time()
        s = self._st
        if "<Vehicle Destroyed>" in line:
            m = VKILL_RE.search(line)
            if m:
                s["kills"].append({"t": t, "cls": m.group(2), "level": int(m.group(6)),
                                   "reason": m.group(4), "killer": m.group(7)})
                del s["kills"][:-MAX_KILLS]
            return
        if "<RequestLocationInventory>" in line:
            m = PLACE_RE.search(line)
            if m and not m.group(2).startswith("INVALID"):
                s["place"] = self._place_name(m.group(2)) or m.group(2)
            return
        if "AttachmentReceived>" in line:
            m, p = ATTACH_RE.search(line), ATTACH_PORT_RE.search(line)
            if m and p:
                s["loadout"][p.group(1)] = m.group(1).strip()
            return
        if BLUEPRINT_RE.search(line):
            s["blueprints"] += 1
        m = NOTIF_RE.search(line)
        if m:
            self._notice(clean(m.group(1)), t)

    def _notice(self, txt, t):
        if not txt:
            return
        s = self._st
        s["notices"].append({"t": t, "text": txt, "group": group_of(txt)})
        del s["notices"][:-MAX_NOTICES]
        low = txt.lower()
        if low.startswith("entering armistice zone"):
            s["armistice"] = True
            return
        if low.startswith("leaving armistice zone"):
            s["armistice"] = False
            return
        m = JURIS_RE.match(txt)
        if m:
            s["jurisdiction"] = (m.group(1) or m.group(2)).strip()
            return
        m = CONTRACT_NOTIF_RE.match(txt)
        if m:
            status = {"Accepted": "active", "Complete": "complete", "Failed": "failed"}[m.group(1)]
            name = m.group(2)
            hit = next((c for c in reversed(s["contracts"]) if c["name"] == name), None)
            if hit and status != "active":
                hit["status"] = status
            elif status == "active" or not hit:
                s["contracts"].append({"name": name, "status": status, "objective": None, "t": t})
                del s["contracts"][:-MAX_CONTRACTS]
            return
        if txt.startswith("New Objective: "):
            active = next((c for c in reversed(s["contracts"]) if c["status"] == "active"), None)
            if active:
                active["objective"] = txt[len("New Objective: "):].strip()
            return
        m = INJURY_RE.match(txt)
        if m:
            part = PARTS.get(m.group(2).strip().lower())
            if part:
                s["injuries"][part] = LEVEL[m.group(1).lower()]


class LiveTail:
    """Follows one Game.log. `path_fn` returns the file to follow (or None)."""

    def __init__(self, path_fn, place_name=None):
        self._path_fn = path_fn
        self._state = LogState(place_name)
        self._path = None
        self._pos = 0
        self._rest = b""
        self._lock = threading.Lock()

    def poll(self):
        with self._lock:
            path = self._path_fn()
            if not path or not os.path.isfile(path):
                return
            size = os.path.getsize(path)
            if path != self._path or size < self._pos:
                self._state.reset()
                self._path, self._pos, self._rest = path, 0, b""
            if size == self._pos:
                return
            with open(path, "rb") as fh:
                fh.seek(self._pos)
                chunk = fh.read(size - self._pos)
            self._pos += len(chunk)
            data = self._rest + chunk
            lines = data.split(b"\n")
            self._rest = lines.pop()
            for raw in lines:
                self._state.feed(raw.decode("utf-8", "replace").rstrip("\r"))

    def state(self):
        with self._lock:
            return self._state.state()


_TAIL = {"tail": None, "thread": None}
_TAIL_LOCK = threading.Lock()
POLL_S = 2.0


def get_state(path_fn, place_name=None):
    """The live log state. The first call starts one background tail on `path_fn`."""
    with _TAIL_LOCK:
        if _TAIL["tail"] is None:
            tail = LiveTail(path_fn, place_name)
            tail.poll()

            def run():
                while True:
                    time.sleep(POLL_S)
                    try:
                        tail.poll()
                    except Exception:
                        pass

            th = threading.Thread(target=run, name="livelog", daemon=True)
            th.start()
            _TAIL.update(tail=tail, thread=th)
        return _TAIL["tail"].state()
