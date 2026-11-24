"""Live Game.log state for the Kestrel slate.

`LiveTail` follows the newest channel's Game.log while the game runs and keeps a small
state: where you are, the zone, recent HUD notices, contracts and their objectives,
prompts waiting for an answer in game, the party, the quantum target, ships destroyed
nearby, your deaths, injuries, the gear you put on and blueprints received.
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
ACTOR_KILL_RE = re.compile(
    r"<Actor Death> CActor::Kill: '([^']+)' \[\d+\] in zone '([^']*)' "
    r"killed by '([^']+)' \[\d+\] using '([^']*)' \[Class [^\]]*\] "
    r"with damage type '([^']*)'")
BLUEPRINT_RE = re.compile(r'Received Blueprint: (.*?): " \[(\d+)\]')
PLACE_RE = re.compile(r"<RequestLocationInventory> Player\[([^\]]+)\] requested inventory for Location\[([^\]]+)\]")
HAUL_RE = re.compile(r"^New Objective: Deliver (\d+)/(\d+) SCU of (.+?) to (.+?):?\s*$")
ATTACH_RE = re.compile(r"AttachmentReceived> Player\[[^\]]+\] Attachment\[[^,]+,\s*([^,]+),")
ATTACH_PORT_RE = re.compile(r"Port\[([a-zA-Z0-9_]+)\]")
LOGIN_RE = re.compile(r"Handle\[([^\]]+)\]")
# mission ends (abandon raises no HUD notice) and the mission id a notice carries, as in sc_stats
MISSION_RE = re.compile(r"MissionId\[([^\]]+)\].*?CompletionType\[([^\]]+)\]")
NOTIF_MISSION_RE = re.compile(r"MissionId: \[([0-9a-f-]+)\], ObjectiveId: \[([^\]]*)\]")
END_KIND = {"complete": "complete", "abandon": "abandoned", "fail": "failed", "failed": "failed"}
# the item's id, third field of Attachment[...]: the same id on a new port means the item moved
ATTACH_ID_RE = re.compile(r"Attachment\[[^,\]]+,\s*[^,\]]+,\s*(\d+)\]")
LINE_TS_RE = re.compile(r"^<(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d+)?Z>")

# Many notices run over two or more lines, each with its own timestamp:
#   Added notification "New Member Joined
#   Pilot_A has joined the party.: " [263] to queue. ...
NOTIF_ML_RE = re.compile(NOTIF_RE.pattern, re.S)
NOTIF_END_RE = re.compile(r'" \[\d+\]')
TS_PREFIX_RE = re.compile(r"^<[^>]*>\s?")
EVENT_RE = re.compile(r"^\[(?:Notice|Error|Warning|Trace|Info|Debug)\]")   # a new log event, not a continuation
OWN_RE = re.compile(r"AttachmentReceived> Player\[([^\]]+)\]")
MARKER_RE = re.compile(r"(Streamed in|Streaming out) party marker id \d+\. TrackedEntityId: (\d+)")
QT_SEL_RE = re.compile(r"Player has selected point (\S+) as their destination")
QT_ROUTE_RE = re.compile(r"Successfully calculated route to (\S+) fuel estimate ([\d.]+)")
OBJ_RE = re.compile(r"^(New Objective|Objective Complete|Objective Withdrawn|Objective Failed):\s*(.+)$")
OBJ_STATE = {"New Objective": "active", "Objective Complete": "complete",
             "Objective Withdrawn": "withdrawn", "Objective Failed": "failed"}
DROP_RE = re.compile(r"^Deliver (\d+)/(\d+) SCU of (.+?) to (.+?)$")
REP_RE = re.compile(r"\[([\d/,]+) Rep\]")
MONEY_ASK_RE = re.compile(r"^(.+?) wants to send you (.+)$")
MONEY_SENT_RE = re.compile(r"^(.+?) has sent you$")
MEMBER_RE = {"New Member Joined": re.compile(r"^(.+) has joined the party\.$"),
             "Member Left": re.compile(r"^(.+) has left the party\.$"),
             "New Party Leader": re.compile(r"^(.+) is now party leader\.$"),
             "Party": re.compile(r"^(.+) connected\.$")}
PARTY_GONE = ("You have left the party.", "You have been kicked from the party.", "Party Disbanded")

MAX_NOTICES = 40
MAX_KILLS = 30
MAX_CONTRACTS = 30
MAX_ASKS = 20
MAX_PLACES = 10
MAX_DEATHS = 20
MAX_OBJECTIVES = 20
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


def rep_of(raw):
    """The rep a contract title's [N Rep] tag offers. A tiered tag (50/150/200) gives its top value."""
    m = REP_RE.search(raw or "")
    if not m:
        return None
    vals = [int(v.replace(",", "")) for v in m.group(1).split("/") if v.replace(",", "")]
    return max(vals) if vals else None


def real_mid(mid):
    """Notices outside a mission carry an all-zero MissionId."""
    return bool(mid) and mid.strip("0-") != ""


def title_match(shared, name):
    """A shared contract's title against an accepted one. Shared titles keep ~mission(...) placeholders."""
    parts = re.split(r"~mission\(\w*\)", shared)
    return re.fullmatch(".+?".join(re.escape(p) for p in parts), name) is not None


class LogState:
    """State built from Game.log lines fed in order."""

    def __init__(self, place_name=None):
        self._place_name = place_name or (lambda code: None)
        self.reset()

    def reset(self):
        self._st = {"place": None, "jurisdiction": None, "armistice": None, "notices": [], "contracts": [],
                    "kills": [], "injuries": {}, "loadout": {}, "blueprints": 0, "seq": 0,
                    "asks": [], "party": {"members": [], "leader": None, "near": 0},
                    "qt": {"target": None, "fuel": None, "t": None}, "places": [], "me": None, "deaths": []}
        self._bp_seen = set()     # one blueprint receipt is 2-4 lines sharing its notification id
        self._item_port = {}      # item id -> port it was last received on
        self._port_item = {}      # port -> item id on it
        self._pend = None         # lines of a notice still waiting for its closing quote
        self._near = set()        # party markers streamed in
        self._qt_code = None      # raw quantum target, to pair the route line with it

    def state(self):
        s = self._st
        me = (s["me"] or "").lower()
        p = s["party"]
        return {"place": s["place"], "jurisdiction": s["jurisdiction"], "armistice": s["armistice"],
                "notices": [dict(n) for n in s["notices"]],
                "contracts": [dict(c, objectives=[{k: o[k] for k in ("text", "state", "optional")}
                                                  for o in c["objectives"]],
                                   drops=[dict(d) for d in c["drops"]]) for c in s["contracts"]],
                "kills": [dict(k) for k in s["kills"]], "injuries": dict(s["injuries"]),
                "loadout": dict(s["loadout"]), "blueprints": s["blueprints"], "seq": s["seq"],
                "asks": [dict(a) for a in s["asks"]],
                "party": {"members": [m for m in p["members"] if m.lower() != me], "leader": p["leader"],
                          "near": len(self._near)},
                "qt": dict(s["qt"]), "places": [dict(x) for x in s["places"]], "me": s["me"],
                "deaths": [dict(d) for d in s["deaths"]]}

    def feed(self, line, t=None):
        if t is None:
            t = line_time(line) or time.time()
        s = self._st
        if "Received Blueprint:" in line:
            m = BLUEPRINT_RE.search(line)
            if m and (m.group(2), m.group(1)) not in self._bp_seen:
                self._bp_seen.add((m.group(2), m.group(1)))
                s["blueprints"] += 1
        if self._pend is not None:
            body = TS_PREFIX_RE.sub("", line, count=1)
            if not EVENT_RE.match(body):
                self._pend.append(body)
                if NOTIF_END_RE.search(body):
                    whole, self._pend = "\n".join(self._pend), None
                    self._notif_line(whole, t)
                elif len(self._pend) > 8:
                    self._pend = None
                return
        if "<Vehicle Destroyed>" in line:
            m = VKILL_RE.search(line)
            if m:
                s["kills"].append({"t": t, "cls": m.group(2), "level": int(m.group(6)),
                                   "reason": m.group(4), "killer": m.group(7),
                                   "by_me": s["me"] is not None and m.group(7) == s["me"]})
                del s["kills"][:-MAX_KILLS]
            return
        if "<Actor Death>" in line:
            m = ACTOR_KILL_RE.search(line)
            if m and s["me"] is not None and m.group(1) == s["me"]:
                s["deaths"].append({"t": t, "killer": m.group(3), "zone": m.group(2)})
                del s["deaths"][:-MAX_DEATHS]
            return
        if "<RequestLocationInventory>" in line:
            m = PLACE_RE.search(line)
            if m and not m.group(2).startswith("INVALID"):
                name = self._place_name(m.group(2)) or m.group(2)
                if name != s["place"]:
                    s["places"].append({"name": name, "t": t})
                    del s["places"][:-MAX_PLACES]
                s["place"] = name
            return
        if "AttachmentReceived>" in line:
            m = OWN_RE.search(line)
            if m:
                s["me"] = m.group(1)          # attachment lines are only ever the player's own
            m, p = ATTACH_RE.search(line), ATTACH_PORT_RE.search(line)
            if m and p:
                self._attach(p.group(1), m.group(1).strip(), ATTACH_ID_RE.search(line))
            return
        if "<CPartyMarkerComponent" in line:
            m = MARKER_RE.search(line)
            if m:
                (self._near.add if m.group(1) == "Streamed in" else self._near.discard)(m.group(2))
            return
        if "OnPlayerSelectedQuantumTarget" in line:
            m = QT_SEL_RE.search(line)
            if m:
                self._qt_code = m.group(1)
                s["qt"] = {"target": self._place_name(m.group(1)) or m.group(1), "fuel": None, "t": t}
            return
        if "Successfully calculated route" in line:
            m = QT_ROUTE_RE.search(line)
            if m:
                if m.group(1) != self._qt_code:
                    self._qt_code = m.group(1)
                    s["qt"] = {"target": self._place_name(m.group(1)) or m.group(1), "fuel": None, "t": t}
                s["qt"]["fuel"] = float(m.group(2))
            return
        if "Handle[" in line and "login" in line.lower():
            m = LOGIN_RE.search(line)
            if m:
                s["me"] = m.group(1)
            return
        if "<Decline invitation>" in line:
            self._close("party", None)
            return
        if "<EndMission>" in line:
            m = MISSION_RE.search(line)
            if m:
                hit = next((c for c in reversed(s["contracts"]) if c.get("mid") == m.group(1)), None)
                if hit and hit["status"] == "active":
                    hit["status"] = END_KIND.get(m.group(2).strip().lower(), "ended")
            return
        if 'Added notification "' in line:
            if NOTIF_RE.search(line):
                self._notif_line(line, t)
            else:
                self._pend = [line]

    def _notif_line(self, line, t):
        m = NOTIF_ML_RE.search(line)
        if not m:
            return
        mm = NOTIF_MISSION_RE.search(line)
        mid = mm.group(1) if mm else None
        parts = [p for p in (clean(x) for x in m.group(1).split("\n")) if p]
        self._notice(": ".join(parts), t, mid, mm.group(2) if mm else None, parts, m.group(1))

    def _attach(self, port, cls, idm):
        """An item lands on a port. It leaves the port it was on before (a drawn rifle leaves wep_stocked_3,
        a helmet hung on the hook leaves the hand), and whatever held this port before is gone from it."""
        lo = self._st["loadout"]
        iid = idm.group(1) if idm else None
        old = self._port_item.pop(port, None)
        if old is not None and old != iid:
            self._item_port.pop(old, None)
        if iid is not None:
            prev = self._item_port.get(iid)
            if prev is not None and prev != port and self._port_item.get(prev) == iid:
                lo.pop(prev, None)
                del self._port_item[prev]
            self._item_port[iid] = port
            self._port_item[port] = iid
        lo[port] = cls

    def _ask(self, kind, who, what, t):
        asks = self._st["asks"]
        hit = next((a for a in asks if a["open"] and (a["kind"], a["who"], a["what"]) == (kind, who, what)), None)
        if hit:                   # the same prompt again (a contract shared twice) refreshes it
            asks.remove(hit)
        asks.append({"kind": kind, "who": who, "what": what, "t": t, "open": True})
        del asks[:-MAX_ASKS]

    def _close(self, kind, test, every=False):
        """Close the newest open ask of `kind` that passes `test` (any when None), or all of them."""
        for a in reversed(self._st["asks"]):
            if a["open"] and a["kind"] == kind and (test is None or test(a)):
                a["open"] = False
                if not every:
                    return

    def _contract_for(self, mid):
        cs = self._st["contracts"]
        return (next((c for c in reversed(cs) if real_mid(mid) and c.get("mid") == mid), None)
                or next((c for c in reversed(cs) if c["status"] == "active"), None))

    def _objective(self, kind, body, t, mid, oid):
        c = self._contract_for(mid)
        if not c:
            return
        opt = body.upper().startswith("OPTIONAL:")
        text = body[len("OPTIONAL:"):].strip() if opt else body
        objs = c["objectives"]
        o = (next((o for o in objs if oid and o["id"] == oid), None)
             or next((o for o in objs if o["text"] == text), None))
        if o is None:
            o = {"id": oid, "text": text, "optional": opt}
            objs.append(o)
            del objs[:-MAX_OBJECTIVES]
        o.update(text=text, state=OBJ_STATE[kind])
        m = DROP_RE.match(text)
        if m:
            a, b, cargo, to = int(m.group(1)), int(m.group(2)), m.group(3).strip(), m.group(4).strip()
            d = next((d for d in c["drops"] if (d["cargo"], d["to"]) == (cargo, to)), None)
            if d is None:
                c["drops"].append({"cargo": cargo, "a": a, "b": b, "to": to})
            else:
                d.update(a=a, b=b)
        live = [o for o in objs if o["state"] == "active"]
        c["objective"] = (live or objs)[-1]["text"]     # none left open: the last one, with its final count

    def _party(self, title, body):
        """Party notices: two lines, a title and the line naming who."""
        p = self._st["party"]
        if title in PARTY_GONE:
            p.update(members=[], leader=None)
            self._near.clear()
            return True
        if title in ("You have joined", "You have created") and body == "party":
            p.update(members=[], leader=self._st["me"] if title == "You have created" else None)
            if title == "You have joined":
                self._close("party", None)
            return True
        rx = MEMBER_RE.get(title)
        m = rx.match(body) if rx and body else None
        if not m:
            return False
        name = m.group(1).strip()
        known = next((x for x in p["members"] if x.lower() == name.lower()), None)
        if title == "Member Left":
            if known:
                p["members"].remove(known)
            if p["leader"] and p["leader"].lower() == name.lower():
                p["leader"] = None
            return True
        if not known and name.lower() != (self._st["me"] or "").lower():
            p["members"].append(name)
        if title == "New Party Leader":
            p["leader"] = name
        return True

    def _asks(self, txt, parts, t):
        title, body = parts[0], (parts[1] if len(parts) > 1 else "")
        if body == "Party Invite Received: Accept Invitation?":
            self._ask("party", title, None, t)
        elif body == "Friend Request: Add to Friends?":
            self._ask("friend", title, None, t)
        elif body == "Party Launching: Join Party In PU?" and title.startswith("Initiated by "):
            self._ask("launch", title[len("Initiated by "):].strip(), None, t)
        elif title == "Friend Added" and body:
            self._close("friend", lambda a: (a["who"] or "").lower() == body.lower())
        elif title in ("Party Launch Accepted", "Party Launch Failed"):
            self._close("launch", None, every=True)
        elif txt.startswith("Contract Shared: "):
            self._ask("contract", None, txt[len("Contract Shared: "):].strip(), t)
        elif MONEY_ASK_RE.match(title):
            m = MONEY_ASK_RE.match(title)
            self._ask("money", m.group(1).strip(), m.group(2).strip(), t)
        elif MONEY_SENT_RE.match(title):
            who = MONEY_SENT_RE.match(title).group(1).strip()
            self._close("money", lambda a: a["who"] == who)
        else:
            return False
        return True

    def _notice(self, txt, t, mid=None, oid=None, parts=None, raw=None):
        if not txt:
            return
        s = self._st
        s["notices"].append({"t": t, "text": txt, "group": group_of(txt)})
        s["seq"] += 1             # notices are capped, the count of notices seen is not
        del s["notices"][:-MAX_NOTICES]
        parts = parts or [txt]
        if self._asks(txt, parts, t):
            return
        if len(parts) > 1 and self._party(parts[0], parts[1]):
            return
        if len(parts) == 1 and parts[0] in PARTY_GONE and self._party(parts[0], ""):
            return
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
            if status == "active":
                self._close("contract", lambda a: title_match(a["what"], name))
            hit = (next((c for c in reversed(s["contracts"]) if real_mid(mid) and c.get("mid") == mid), None)
                   or next((c for c in reversed(s["contracts"]) if c["name"] == name), None))
            if hit and status != "active":
                hit["status"] = status
            elif status == "active" or not hit:
                hit = {"name": name, "status": status, "objective": None, "t": t, "mid": mid,
                       "objectives": [], "drops": [], "rep": None, "bp": False}
                s["contracts"].append(hit)
                del s["contracts"][:-MAX_CONTRACTS]
            rep = rep_of(raw)
            if rep is not None:
                hit["rep"] = rep
            if "[BP]" in (raw or ""):
                hit["bp"] = True
            return
        m = OBJ_RE.match(txt)
        if m:
            self._objective(m.group(1), m.group(2).strip(), t, mid, oid)
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
