#!/usr/bin/env python3
"""
SC Logs Live Analyzer
=====================
A self-contained (stdlib-only) live analyzer for Star Citizen's Game.log.

It tails the log in real time, parses combat and session events, figures out
which handle is *you*, and serves a live web dashboard.

Run:
    python sc_analyzer.py                 # auto-detect log + open browser
    python sc_analyzer.py --log PATH      # point at a specific Game.log
    python sc_analyzer.py --port 8420     # change the web port
    python sc_analyzer.py --no-browser    # don't auto-open the browser

Tested against Star Citizen alpha 4.9 log format (July 2026).
No external dependencies. Python 3.9+.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue, Empty

# --------------------------------------------------------------------------- #
#  Configuration / log path discovery
# --------------------------------------------------------------------------- #

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8420
MAX_EVENTS = 5000  # ring buffer of parsed events kept in memory

# Candidate Game.log locations, tried in order when --log isn't given.
def candidate_log_paths():
    paths = []
    # 1. Whatever StarLogs was configured to watch (reuse their config if present).
    for cfg_name in ("starlogs_config.json",):
        for base in (SCRIPT_DIR, os.path.join(SCRIPT_DIR, "StarLogs-v0.9.1")):
            cfg = os.path.join(base, cfg_name)
            if os.path.isfile(cfg):
                try:
                    with open(cfg, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    active = data.get("active_version", "LIVE")
                    inst = data.get("installations", {})
                    entry = inst.get(active) or next(iter(inst.values()), None)
                    if entry and entry.get("path"):
                        paths.append(os.path.join(entry["path"], "Game.log"))
                    for key in ("LIVE", "PTU"):
                        if key in inst and inst[key].get("path"):
                            paths.append(os.path.join(inst[key]["path"], "Game.log"))
                except Exception:
                    pass
    # 2. Common default install locations.
    for drive in ("C:", "D:", "E:"):
        for chan in ("LIVE", "PTU"):
            paths.append(rf"{drive}\Program Files\Roberts Space Industries\StarCitizen\{chan}\Game.log")
    # 3. The sample Game.log sitting next to this script (for testing).
    paths.append(os.path.join(SCRIPT_DIR, "Game.log"))
    # De-dup while preserving order.
    seen, out = set(), []
    for p in paths:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def discover_log_path(explicit):
    if explicit:
        return explicit
    for p in candidate_log_paths():
        if os.path.isfile(p):
            return p
    return None


# --------------------------------------------------------------------------- #
#  Event model + parsing
# --------------------------------------------------------------------------- #

# Category buckets used by the dashboard filters.
CATEGORIES = [
    "death_pvp",    # you were killed by another player
    "kill_pvp",     # you killed another player
    "death_pve",    # you were killed by an NPC / environment
    "kill_pve",     # you killed an NPC
    "suicide",      # self-inflicted death
    "other_death",  # a death that doesn't involve you
    "vehicle",      # ship / vehicle soft-death or destruction
    "corpse",       # corpsify / bleed-out
    "session",      # login, connect, mission, disconnect, etc.
]

TS_RE = re.compile(r"^<(?P<ts>[^>]+)>")

# --- combat / death ---------------------------------------------------------
ACTOR_DEATH_RE = re.compile(
    r"<Actor Death> CActor::Kill: '(?P<victim>[^']+)' \[\d+\] "
    r"in zone '(?P<zone>[^']*)' "
    r"killed by '(?P<killer>[^']+)' \[\d+\] "
    r"using '(?P<weapon>[^']*)' \[Class (?P<wclass>[^\]]*)\] "
    r"with damage type '(?P<dtype>[^']*)'"
)

VEHICLE_DESTROY_RE = re.compile(
    r"<Vehicle Destruction> CVehicle::OnAdvanceDestroyLevel: "
    r"Vehicle '(?P<vehicle>[^']+)' \[\d+\] "
    r"in zone '(?P<zone>[^']*)'"
    r".*?driven by '(?P<driver>[^']*)' \[\d+\] "
    r"advanced from destroy level (?P<from>\d+) to (?P<to>\d+) "
    r"caused by '(?P<causer>[^']*)' \[\d+\] "
    r"with '(?P<cause>[^']*)'"
)

CORPSE_RE = re.compile(r"Corpsify.*?Player '(?P<player>[^']+)'")

STALL_RE = re.compile(
    r"Actor Stall.*?Player: (?P<player>[^,]+),.*?Type:\s*(?P<stype>\w+)", re.IGNORECASE
)

# --- SC 4.9.187+ NEW death / destruction formats ---------------------------
# CIG replaced (or supplements) the old "<Actor Death> CActor::Kill:" line with
# an actor-state-machine transition. This is what fires for the local player.
# e.g. <[ActorState] Dead> [ACTOR STATE][CSCActorControlStateDead::PrePhysicsUpdate]
#      Actor 'archelium' [id] ejected from zone 'DRAK_Corsair_123' [id]
#      to zone 'ab_mine_stanton4_med_013' [id] due to previous zone being in a
#      destroyed vehicle with detached interior.
ACTOR_STATE_DEAD_RE = re.compile(
    r"<\[ActorState\] Dead>.*?Actor '(?P<actor>[^']+)' \[\d+\](?P<rest>.*)"
)
_DEAD_ZONE_RE = re.compile(
    r"ejected from zone '(?P<fromzone>[^']+)' \[\d+\] to zone '(?P<tozone>[^']+)'"
)
_DEAD_REASON_RE = re.compile(r"due to (?P<reason>.+?)\s*(?:\[Team|$)")

# Fatal vehicle collision (asteroid, terrain, station, etc.). This is what
# destroyed the ship in the July 2026 log — the old "<Vehicle Destruction>"
# line no longer appears for it.
# e.g. <FatalCollision> Fatal Collision occured for vehicle DRAK_Corsair_123
#      [Part: body, Pos: ..., Zone: ab_mine_stanton4_med_013, PlayerPilot: 1]
#      after hitting entity: UNKNOWN [...]. ... Distance: 22.34, Relative Vel: ...
FATAL_COLLISION_RE = re.compile(
    r"<FatalCollision> Fatal Collision occured for vehicle (?P<vehicle>\S+) "
    r"\[Part: (?P<part>[^,]+),.*?Zone: (?P<zone>[^,\]]+), PlayerPilot: (?P<pilot>\d+)\]"
    r"(?:.*?after hitting entity: (?P<hit>\S+))?"
    r"(?:.*?Relative Vel: x: (?P<vx>[-\d.]+), y: (?P<vy>[-\d.]+), z: (?P<vz>[-\d.]+))?"
)

# --- identity ---------------------------------------------------------------
LOGIN_RE = re.compile(r"<Legacy login response>.*?Handle\[(?P<handle>[^\]]+)\]")
CHAR_RE = re.compile(r"<AccountLoginCharacterStatus_Character>.*? name (?P<name>\S+) - state STATE_CURRENT")

# --- session ---------------------------------------------------------------
END_MISSION_RE = re.compile(
    r"<EndMission> Ending mission for player\. MissionId\[(?P<id>[^\]]+)\] "
    r"Player\[(?P<player>[^\]]+)\].*?CompletionType\[(?P<ctype>[^\]]+)\] Reason\[(?P<reason>[^\]]+)\]"
)
DISCONNECT_RE = re.compile(
    r'<Channel Disconnected> cause=(?P<cause>\d+) reason="(?P<reason>[^"]*)".*?'
    r'gamerules="(?P<gamerules>[^"]*)".*?nickname="(?P<nickname>[^"]*)"'
)
# gamerules flips to SC_Default when you enter the persistent universe.
ENTER_PU_RE = re.compile(r'gamerules="SC_Default".*?taskname="([^"]+)"')
QUANTUM_RE = re.compile(r"<Jump Drive (?:State )?Changed>.*?(?:to|state) (?P<state>\w+)", re.IGNORECASE)


def parse_ts(line):
    m = TS_RE.match(line)
    if not m:
        return None
    raw = m.group("ts")
    try:
        # Format: 2026-07-24T09:39:17.329Z
        dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# NPC / environment name heuristics ------------------------------------------
_NPC_MARKERS = (
    "npc", "pu_human", "pu_pilots", "pu_populace", "aimodule", "kopion",
    "quasigrazer", "marok", "ai_", "_ai_", "hostile_", "enemy_", "vanduul",
    "populationcontrol", "hazard", "creature",
)
_NPC_SUFFIX_RE = re.compile(r"_\d{6,}$")


def is_environment(name):
    n = (name or "").strip().lower()
    return n in ("", "unknown", "n/a")


def is_npc(name):
    if not name:
        return False
    n = name.lower()
    if any(m in n for m in _NPC_MARKERS):
        return True
    if _NPC_SUFFIX_RE.search(name):
        return True
    return False


class Analyzer:
    """Parses log lines into normalized events and tracks running stats."""

    def __init__(self):
        self.player = None            # your handle, once detected
        self.events = deque(maxlen=MAX_EVENTS)
        self.seq = 0
        self.lock = threading.Lock()
        self.subscribers = []         # list[Queue] for SSE
        self.stats = {k: 0 for k in (
            "kill_pvp", "death_pvp", "kill_pve", "death_pve",
            "suicide", "vehicle_destroyed", "ships_lost",
        )}
        self.session_start = None
        self.log_path = None
        # Player-death de-dup: SC can report one death via several event types
        # (e.g. FatalCollision -> [ActorState] Dead, or [ActorState] Dead plus a
        # CActor::Kill line). We keep the richest one and never double-count.
        self._last_death = None       # {"ts": dt, "event": dict, "category": str, "source": str}
        self._death_window = 25.0     # seconds

    # priority: a source with better info wins and can upgrade an earlier one.
    _DEATH_SOURCE_PRIORITY = {"state": 1, "collision": 2, "kill": 3}
    _CATEGORY_TO_STAT = {
        "death_pvp": "death_pvp", "death_pve": "death_pve",
        "kill_pvp": "kill_pvp", "kill_pve": "kill_pve", "suicide": "suicide",
    }

    # -- subscriber plumbing (SSE) ------------------------------------------
    def subscribe(self):
        q = Queue()
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def _publish(self, message):
        with self.lock:
            dead = []
            for q in self.subscribers:
                try:
                    q.put_nowait(message)
                except Exception:
                    dead.append(q)
            for q in dead:
                if q in self.subscribers:
                    self.subscribers.remove(q)

    def snapshot(self):
        with self.lock:
            return {
                "player": self.player,
                "log_path": self.log_path,
                "session_start": self.session_start,
                "stats": dict(self.stats),
                "events": list(self.events),
            }

    # -- event emission ------------------------------------------------------
    def _emit(self, ts, category, title, detail, extra=None, live=True):
        self.seq += 1
        ev = {
            "id": self.seq,
            "ts": ts.isoformat() if ts else None,
            "ts_display": ts.astimezone().strftime("%H:%M:%S") if ts else "",
            "category": category,
            "title": title,
            "detail": detail,
        }
        if extra:
            ev.update(extra)
        self.events.append(ev)
        if live:
            self._publish({"type": "event", "event": ev, "stats": dict(self.stats),
                           "player": self.player})
        return ev

    def _adjust_stat(self, category, delta):
        key = self._CATEGORY_TO_STAT.get(category)
        if key:
            self.stats[key] = max(0, self.stats[key] + delta)

    def _register_player_death(self, ts, category, title, detail, extra, source, live):
        """Count/emit one of *your* deaths, de-duplicating across event types."""
        prev = self._last_death
        if prev and ts and prev["ts"] and \
                abs((ts - prev["ts"]).total_seconds()) <= self._death_window:
            # Same death reported again. Upgrade in place if the new source is richer.
            if self._DEATH_SOURCE_PRIORITY.get(source, 0) > \
                    self._DEATH_SOURCE_PRIORITY.get(prev["source"], 0):
                self._adjust_stat(prev["category"], -1)
                self._adjust_stat(category, +1)
                ev = prev["event"]
                ev["category"], ev["title"], ev["detail"] = category, title, detail
                if extra:
                    ev.update(extra)
                prev["category"], prev["source"] = category, source
                if live:
                    self._publish({"type": "update", "event": ev,
                                   "stats": dict(self.stats), "player": self.player})
            return  # drop the redundant duplicate
        # A fresh death.
        self._adjust_stat(category, +1)
        ev = self._emit(ts, category, title, detail, extra=extra, live=live)
        self._last_death = {"ts": ts, "event": ev, "category": category, "source": source}

    def set_player(self, name, live=True):
        if name and self.player != name:
            self.player = name
            if live:
                self._publish({"type": "player", "player": self.player})

    # -- the main line dispatcher -------------------------------------------
    def process_line(self, line, live=True):
        ts = parse_ts(line)

        # identity ----------------------------------------------------------
        m = LOGIN_RE.search(line)
        if m:
            self.set_player(m.group("handle"), live=live)
            if self.session_start is None and ts:
                self.session_start = ts.isoformat()
            self._emit(ts, "session", f"Logged in as {m.group('handle')}",
                       "Legacy login success", live=live)
            return
        m = CHAR_RE.search(line)
        if m and not self.player:
            self.set_player(m.group("name"), live=live)
            return

        # actor death -------------------------------------------------------
        m = ACTOR_DEATH_RE.search(line)
        if m:
            self._handle_actor_death(ts, m, live=live)
            return

        # vehicle destruction (old format) ---------------------------------
        m = VEHICLE_DESTROY_RE.search(line)
        if m:
            self._handle_vehicle(ts, m, live=live)
            return

        # fatal vehicle collision (SC 4.9.187+) ----------------------------
        m = FATAL_COLLISION_RE.search(line)
        if m:
            self._handle_fatal_collision(ts, m, live=live)
            return

        # actor death — new actor-state format (SC 4.9.187+) ---------------
        m = ACTOR_STATE_DEAD_RE.search(line)
        if m:
            self._handle_actor_state_dead(ts, m, live=live)
            return

        # corpse ------------------------------------------------------------
        m = CORPSE_RE.search(line)
        if m:
            who = m.group("player")
            mine = self.player and who == self.player
            self._emit(ts, "corpse",
                       ("You were corpsified" if mine else f"{who} corpsified"),
                       "Bleed-out / corpse state", extra={"mine": bool(mine)}, live=live)
            return

        # mission end -------------------------------------------------------
        m = END_MISSION_RE.search(line)
        if m:
            self._emit(ts, "session",
                       f"Mission ended ({m.group('ctype')})",
                       f"{m.group('reason')} — {m.group('player')}", live=live)
            return

        # entered persistent universe --------------------------------------
        # (first time we see SC_Default gamerules after being in the frontend)
        # handled loosely — only emit once per session via a guard.
        if '"SC_Default"' in line and not getattr(self, "_pu_flagged", False):
            if "ContextEstablisherTaskFinished" in line and "PrepareLoadout" in line:
                self._pu_flagged = True
                self._emit(ts, "session", "Entered the persistent universe",
                           "Loaded into gameplay", live=live)
                return

    # -- handlers -----------------------------------------------------------
    def _handle_actor_death(self, ts, m, live=True):
        victim = m.group("victim")
        killer = m.group("killer")
        zone = m.group("zone")
        weapon = m.group("weapon")
        dtype = m.group("dtype")
        me = self.player

        victim_is_me = bool(me) and victim == me
        killer_is_me = bool(me) and killer == me
        self_kill = victim == killer or dtype.lower() in ("suicide", "selfdestruct")

        weapon_str = _clean_entity(weapon)
        killer_str = _clean_entity(killer)
        victim_str = _clean_entity(victim)
        detail_bits = []
        if weapon_str and weapon_str.lower() not in ("unknown", "none"):
            detail_bits.append(f"weapon: {weapon_str}")
        if dtype and dtype.lower() != "unknown":
            detail_bits.append(f"damage: {dtype}")
        if zone:
            detail_bits.append(f"zone: {_clean_entity(zone)}")
        detail = "  •  ".join(detail_bits)

        if self_kill and victim_is_me:
            self._register_player_death(
                ts, "suicide", "You died (self-inflicted)", detail,
                {"killer": killer_str, "dtype": dtype}, source="kill", live=live)
            return

        if victim_is_me:
            if is_npc(killer) or is_environment(killer):
                who = "environment" if is_environment(killer) else killer_str
                self._register_player_death(
                    ts, "death_pve", f"You were killed by {who}", detail,
                    {"killer": killer_str}, source="kill", live=live)
            else:
                self._register_player_death(
                    ts, "death_pvp", f"KILLED BY {killer_str}", detail,
                    {"killer": killer_str}, source="kill", live=live)
            return

        if killer_is_me:
            if is_npc(victim) or is_environment(victim):
                self.stats["kill_pve"] += 1
                self._emit(ts, "kill_pve", f"You killed {victim_str}", detail,
                           extra={"victim": victim_str}, live=live)
            else:
                self.stats["kill_pvp"] += 1
                self._emit(ts, "kill_pvp", f"You killed {victim_str}", detail,
                           extra={"victim": victim_str}, live=live)
            return

        # A death that doesn't involve you — kept for context, filterable.
        self._emit(ts, "other_death",
                   f"{victim_str} killed by {killer_str}", detail,
                   extra={"killer": killer_str, "victim": victim_str}, live=live)

    def _handle_vehicle(self, ts, m, live=True):
        vehicle = _clean_entity(m.group("vehicle"))
        driver = m.group("driver")
        causer = m.group("causer")
        zone = _clean_entity(m.group("zone"))
        level_to = int(m.group("to"))
        cause = m.group("cause")

        state = "destroyed" if level_to >= 2 else "disabled (soft death)"
        mine = bool(self.player) and driver == self.player
        by = _clean_entity(causer)
        detail_bits = []
        if by and by.lower() not in ("unknown", ""):
            detail_bits.append(f"by: {by}")
        if zone:
            detail_bits.append(f"zone: {zone}")
        if cause:
            detail_bits.append(f"cause: {cause}")
        detail = "  •  ".join(detail_bits)

        if level_to >= 2:
            self.stats["vehicle_destroyed"] += 1
            if mine:
                self.stats["ships_lost"] += 1

        title = f"{'Your ' if mine else ''}{vehicle} {state}".strip()
        self._emit(ts, "vehicle", title, detail,
                   extra={"mine": bool(mine), "level": level_to,
                          "driver": _clean_entity(driver)}, live=live)

    def _handle_fatal_collision(self, ts, m, live=True):
        """<FatalCollision> — ship destroyed by hitting something (SC 4.9.187+)."""
        vehicle = _clean_entity(m.group("vehicle"))
        zone = m.group("zone")
        pilot = m.group("pilot") == "1"          # local player was piloting
        hit = m.group("hit") or ""
        impact_speed = None
        if m.group("vx") is not None:
            try:
                impact_speed = (float(m.group("vx")) ** 2 + float(m.group("vy")) ** 2
                                + float(m.group("vz")) ** 2) ** 0.5
            except ValueError:
                impact_speed = None

        # Remember the collision so a following [ActorState] Dead can name the cause.
        self._last_collision = {"ts": ts, "vehicle": vehicle, "zone": zone,
                                "pilot": pilot}

        detail_bits = []
        target = hit if hit and hit.upper() != "UNKNOWN" else "terrain/asteroid"
        detail_bits.append(f"hit: {target}")
        if zone:
            detail_bits.append(f"zone: {zone}")
        if impact_speed is not None:
            detail_bits.append(f"impact: {impact_speed:.0f} m/s")
        detail = "  •  ".join(detail_bits)

        self.stats["vehicle_destroyed"] += 1
        if pilot:
            self.stats["ships_lost"] += 1
        title = f"{'Your ' if pilot else ''}{vehicle} destroyed (fatal collision)".strip()
        self._emit(ts, "vehicle", title, detail,
                   extra={"mine": pilot, "collision": True}, live=live)

    def _handle_actor_state_dead(self, ts, m, live=True):
        """<[ActorState] Dead> — actor-state death transition (SC 4.9.187+).

        This has no killer field, so we infer the cause from context (a recent
        fatal collision, or the 'destroyed vehicle' reason text)."""
        actor = m.group("actor")
        rest = m.group("rest") or ""
        if not (self.player and actor == self.player):
            return  # only track the local player's death from this line

        reason = ""
        rm = _DEAD_REASON_RE.search(rest)
        if rm:
            reason = rm.group("reason").strip().rstrip(".")
        zm = _DEAD_ZONE_RE.search(rest)
        from_zone = _clean_entity(zm.group("fromzone")) if zm else ""

        # Was this caused by a ship being destroyed (collision or combat)?
        recent_collision = (
            getattr(self, "_last_collision", None)
            and ts and self._last_collision.get("ts")
            and abs((ts - self._last_collision["ts"]).total_seconds()) <= 30
        )
        vehicle_death = "destroyed vehicle" in reason.lower() or recent_collision

        detail_bits = []
        if recent_collision:
            # The ship loss was already counted by the FatalCollision handler.
            title = "You died — ship destroyed by collision"
            detail_bits.append(f"ship: {self._last_collision['vehicle']}")
            if self._last_collision.get("zone"):
                detail_bits.append(f"zone: {self._last_collision['zone']}")
        elif vehicle_death:
            # Ship was destroyed (combat / turrets / etc.) but SC logged no
            # separate destruction line — the death line is the only evidence.
            # Count it as a ship loss so the tally stays accurate, and surface
            # the destruction in the feed.
            title = "You died — ship destroyed"
            if from_zone:
                detail_bits.append(f"ship: {from_zone}")
            self.stats["vehicle_destroyed"] += 1
            self.stats["ships_lost"] += 1
            self._emit(ts, "vehicle",
                       f"Your {from_zone or 'ship'} destroyed".strip(),
                       "destroyed (no separate destruction log — inferred from death)",
                       extra={"mine": True, "inferred": True}, live=live)
        else:
            title = "You died"
            if reason:
                detail_bits.append(reason)
        detail = "  •  ".join(detail_bits)

        # Ship-destruction deaths are environmental/PvE unless a killer is known.
        self._register_player_death(
            ts, "death_pve", title, detail,
            {"reason": reason}, source="state", live=live)


def _clean_entity(name):
    """Trim the trailing numeric spawn id SC appends to entity names."""
    if not name:
        return name
    # e.g. ANVL_Hornet_F7A_Mk2_2214550123456 -> ANVL_Hornet_F7A_Mk2
    return _NPC_SUFFIX_RE.sub("", name).rstrip("_")


# --------------------------------------------------------------------------- #
#  Log tailer
# --------------------------------------------------------------------------- #

class LogTailer(threading.Thread):
    def __init__(self, path, analyzer, poll=0.5):
        super().__init__(daemon=True)
        self.path = path
        self.analyzer = analyzer
        self.poll = poll
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        # Wait for the file to exist (game may not be running yet).
        while not self._stop.is_set() and not os.path.isfile(self.path):
            self.analyzer._publish({"type": "status", "status": "waiting",
                                    "path": self.path})
            time.sleep(2)
        if self._stop.is_set():
            return

        # Backfill: parse the whole existing file first (no live push).
        pos = 0
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    self.analyzer.process_line(line.rstrip("\n"), live=False)
                pos = f.tell()
        except Exception as e:
            print(f"[tailer] backfill error: {e}", file=sys.stderr)

        # Announce ready with the full backfilled snapshot.
        self.analyzer._publish({"type": "reset", **self.analyzer.snapshot()})
        self.analyzer._publish({"type": "status", "status": "connected",
                                "path": self.path})

        # Tail loop with rotation detection.
        while not self._stop.is_set():
            try:
                size = os.path.getsize(self.path)
                if size < pos:
                    # File was truncated / rotated (new game session).
                    pos = 0
                    self.analyzer._publish({"type": "status", "status": "rotated",
                                            "path": self.path})
                with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(pos)
                    new = f.read()
                    pos = f.tell()
                if new:
                    for line in new.splitlines():
                        self.analyzer.process_line(line, live=True)
            except FileNotFoundError:
                self.analyzer._publish({"type": "status", "status": "waiting",
                                        "path": self.path})
                time.sleep(2)
                continue
            except Exception as e:
                print(f"[tailer] read error: {e}", file=sys.stderr)
            time.sleep(self.poll)


# --------------------------------------------------------------------------- #
#  Web server (dashboard + SSE)
# --------------------------------------------------------------------------- #

def make_handler(analyzer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # silence default request logging

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/index"):
                self._send(200, INDEX_HTML)
            elif self.path == "/api/state":
                self._send(200, json.dumps(analyzer.snapshot()),
                           "application/json; charset=utf-8")
            elif self.path == "/events":
                self._stream_events()
            else:
                self._send(404, "not found", "text/plain")

        def _stream_events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = analyzer.subscribe()
            # Send an initial snapshot so a fresh page is fully populated.
            try:
                self._sse({"type": "reset", **analyzer.snapshot()})
                while True:
                    try:
                        msg = q.get(timeout=15)
                        self._sse(msg)
                    except Empty:
                        # keep-alive comment
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                analyzer.unsubscribe(q)

        def _sse(self, obj):
            payload = "data: " + json.dumps(obj) + "\n\n"
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()

    return Handler


# --------------------------------------------------------------------------- #
#  Embedded front-end
# --------------------------------------------------------------------------- #

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SC Logs Live Analyzer</title>
<style>
  :root{
    --bg:#0a0e14; --panel:#111823; --panel2:#0d141e; --line:#1e2a3a;
    --txt:#c7d5e6; --muted:#6b8199; --accent:#f4a92a; --cyan:#42d0e6;
    --red:#ff4d5e; --green:#4ade80; --purple:#b98bff; --orange:#ff9147;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:'Segoe UI',system-ui,sans-serif;background:
    radial-gradient(1200px 600px at 70% -10%, #12202e 0%, var(--bg) 55%);
    color:var(--txt);min-height:100vh}
  header{display:flex;align-items:center;gap:16px;padding:14px 22px;
    border-bottom:1px solid var(--line);background:rgba(13,20,30,.7);
    backdrop-filter:blur(6px);position:sticky;top:0;z-index:5}
  .logo{font-weight:700;letter-spacing:.5px;font-size:18px}
  .logo b{color:var(--accent)}
  .who{color:var(--muted);font-size:13px}
  .who .name{color:var(--cyan);font-weight:600}
  .status{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:12px;
    color:var(--muted)}
  .dot{width:9px;height:9px;border-radius:50%;background:var(--muted);
    box-shadow:0 0 0 0 rgba(0,0,0,0)}
  .dot.ok{background:var(--green);animation:pulse 2s infinite}
  .dot.wait{background:var(--accent)}
  .dot.err{background:var(--red)}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(74,222,128,.5)}
    70%{box-shadow:0 0 0 7px rgba(74,222,128,0)}100%{box-shadow:0 0 0 0 rgba(74,222,128,0)}}
  .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));
    gap:12px;padding:18px 22px}
  .stat{background:linear-gradient(180deg,var(--panel),var(--panel2));
    border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .stat .v{font-size:28px;font-weight:700;line-height:1}
  .stat .l{font-size:11px;text-transform:uppercase;letter-spacing:.6px;
    color:var(--muted);margin-top:6px}
  .stat.kd .v{color:var(--accent)}
  .stat.kill .v{color:var(--green)}
  .stat.death .v{color:var(--red)}
  .stat.ship .v{color:var(--orange)}
  .bar{display:flex;flex-wrap:wrap;gap:8px;padding:0 22px 12px}
  .chip{border:1px solid var(--line);background:var(--panel);color:var(--muted);
    padding:6px 12px;border-radius:20px;font-size:12px;cursor:pointer;
    user-select:none;transition:.15s}
  .chip.active{color:#06121f;font-weight:600}
  .chip[data-c="death_pvp"].active{background:var(--red);border-color:var(--red)}
  .chip[data-c="kill_pvp"].active{background:var(--green);border-color:var(--green)}
  .chip[data-c="death_pve"].active{background:#c2596a;border-color:#c2596a}
  .chip[data-c="kill_pve"].active{background:#6fbf7f;border-color:#6fbf7f}
  .chip[data-c="suicide"].active{background:var(--purple);border-color:var(--purple)}
  .chip[data-c="vehicle"].active{background:var(--orange);border-color:var(--orange)}
  .chip[data-c="corpse"].active{background:#8aa0b8;border-color:#8aa0b8}
  .chip[data-c="session"].active{background:var(--cyan);border-color:var(--cyan)}
  .chip[data-c="other_death"].active{background:#54708c;border-color:#54708c}
  .feed{padding:4px 22px 60px;max-width:1100px}
  .row{display:flex;gap:14px;align-items:flex-start;padding:11px 14px;
    border-left:3px solid var(--line);background:var(--panel);margin-bottom:8px;
    border-radius:0 10px 10px 0;animation:in .25s ease}
  @keyframes in{from{opacity:0;transform:translateY(-6px)}to{opacity:1}}
  .row .time{color:var(--muted);font-variant-numeric:tabular-nums;font-size:12px;
    min-width:64px;padding-top:2px}
  .row .ico{font-size:18px;min-width:22px;text-align:center}
  .row .body{flex:1;min-width:0}
  .row .t{font-weight:600;font-size:14px}
  .row .d{color:var(--muted);font-size:12px;margin-top:2px;word-break:break-word}
  .row.death_pvp{border-left-color:var(--red)}
  .row.death_pvp .t{color:var(--red)}
  .row.kill_pvp{border-left-color:var(--green)}
  .row.kill_pvp .t{color:var(--green)}
  .row.death_pve{border-left-color:#c2596a}
  .row.kill_pve{border-left-color:#6fbf7f}
  .row.suicide{border-left-color:var(--purple)}
  .row.suicide .t{color:var(--purple)}
  .row.vehicle{border-left-color:var(--orange)}
  .row.vehicle .t{color:var(--orange)}
  .row.corpse{border-left-color:#8aa0b8}
  .row.session{border-left-color:var(--cyan)}
  .row.session .t{color:var(--cyan);font-weight:500}
  .row.other_death{border-left-color:#3a4a5c;opacity:.85}
  .empty{color:var(--muted);text-align:center;padding:60px 20px;font-size:14px}
  .path{font-size:11px;color:#4a5d72;padding:0 22px 10px;word-break:break-all}
  .count{color:var(--muted);font-size:12px;margin-left:auto;align-self:center}
  footer{position:fixed;bottom:0;left:0;right:0;padding:6px 22px;font-size:11px;
    color:#3a4a5c;background:rgba(10,14,20,.9);border-top:1px solid var(--line);
    display:flex;gap:16px}
</style>
</head>
<body>
<header>
  <div class="logo"><b>◆</b> SC Logs <b>Live</b> Analyzer</div>
  <div class="who">Tracking: <span class="name" id="player">detecting…</span></div>
  <div class="status"><span class="dot" id="dot"></span><span id="statusText">connecting…</span></div>
</header>

<div class="stats" id="stats">
  <div class="stat kill"><div class="v" id="s_kill_pvp">0</div><div class="l">PvP Kills</div></div>
  <div class="stat death"><div class="v" id="s_death_pvp">0</div><div class="l">PvP Deaths</div></div>
  <div class="stat kd"><div class="v" id="s_kd">—</div><div class="l">PvP K/D</div></div>
  <div class="stat kill"><div class="v" id="s_kill_pve">0</div><div class="l">PvE Kills</div></div>
  <div class="stat death"><div class="v" id="s_death_pve">0</div><div class="l">PvE Deaths</div></div>
  <div class="stat"><div class="v" id="s_suicide">0</div><div class="l">Suicides</div></div>
  <div class="stat ship"><div class="v" id="s_ships_lost">0</div><div class="l">Ships Lost</div></div>
  <div class="stat"><div class="v" id="s_vehicle_destroyed">0</div><div class="l">Ships Destroyed</div></div>
</div>

<div class="bar" id="filters"></div>
<div class="path" id="path"></div>
<div class="feed" id="feed"><div class="empty">Waiting for events… Deaths, kills and destruction will appear here live.</div></div>

<footer>
  <span>◆ Live tail via Server-Sent Events</span>
  <span id="evcount">0 events</span>
</footer>

<script>
const CATS = {
  death_pvp:{label:"PvP Deaths", ico:"💀"},
  kill_pvp:{label:"PvP Kills", ico:"🎯"},
  death_pve:{label:"PvE Deaths", ico:"☠️"},
  kill_pve:{label:"PvE Kills", ico:"🔫"},
  suicide:{label:"Suicides", ico:"⚠️"},
  vehicle:{label:"Ships", ico:"🚀"},
  corpse:{label:"Corpse", ico:"🩸"},
  session:{label:"Session", ico:"📡"},
  other_death:{label:"Other", ico:"•"},
};
const active = new Set(Object.keys(CATS));
const events = [];
let player = null;

// build filter chips
const fbar = document.getElementById('filters');
for(const [c,info] of Object.entries(CATS)){
  const el = document.createElement('div');
  el.className = 'chip active'; el.dataset.c = c;
  el.textContent = info.label;
  el.onclick = ()=>{ el.classList.toggle('active');
    if(active.has(c)) active.delete(c); else active.add(c); render(); };
  fbar.appendChild(el);
}

function setStatus(state, text){
  const dot = document.getElementById('dot');
  dot.className = 'dot ' + (state==='connected'?'ok':state==='waiting'?'wait':state==='error'?'err':'');
  document.getElementById('statusText').textContent = text;
}

function setPlayer(p){
  player = p;
  document.getElementById('player').textContent = p || 'detecting…';
}

function setStats(s){
  if(!s) return;
  for(const k of ['kill_pvp','death_pvp','kill_pve','death_pve','suicide','ships_lost','vehicle_destroyed']){
    const el = document.getElementById('s_'+k); if(el) el.textContent = s[k]??0;
  }
  const kd = s.death_pvp>0 ? (s.kill_pvp/s.death_pvp).toFixed(2)
            : (s.kill_pvp>0 ? s.kill_pvp.toFixed(2) : '—');
  document.getElementById('s_kd').textContent = kd;
}

function rowHtml(ev){
  const info = CATS[ev.category] || {ico:'•'};
  const d = ev.detail ? `<div class="d">${escapeHtml(ev.detail)}</div>` : '';
  return `<div class="row ${ev.category}">
    <div class="time">${ev.ts_display||''}</div>
    <div class="ico">${info.ico}</div>
    <div class="body"><div class="t">${escapeHtml(ev.title)}</div>${d}</div>
  </div>`;
}
function escapeHtml(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function render(){
  const feed = document.getElementById('feed');
  const shown = events.filter(e=>active.has(e.category));
  document.getElementById('evcount').textContent = events.length + ' events';
  if(shown.length===0){
    feed.innerHTML = '<div class="empty">No events match the current filters.</div>';
    return;
  }
  // newest first
  feed.innerHTML = shown.slice().reverse().map(rowHtml).join('');
}

function addEvent(ev){ events.push(ev); if(events.length>5000) events.shift(); }

const es = new EventSource('/events');
es.onmessage = (m)=>{
  const msg = JSON.parse(m.data);
  if(msg.type==='reset'){
    events.length = 0;
    (msg.events||[]).forEach(addEvent);
    setPlayer(msg.player); setStats(msg.stats);
    if(msg.log_path) document.getElementById('path').textContent = '📁 ' + msg.log_path;
    render();
  } else if(msg.type==='event'){
    addEvent(msg.event); setStats(msg.stats);
    if(msg.player) setPlayer(msg.player);
    render();
  } else if(msg.type==='update'){
    // in-place upgrade of an existing event (death de-dup / enrichment)
    const i = events.findIndex(e=>e.id===msg.event.id);
    if(i>=0) events[i] = msg.event; else addEvent(msg.event);
    setStats(msg.stats); render();
  } else if(msg.type==='player'){
    setPlayer(msg.player);
  } else if(msg.type==='status'){
    const map={connected:'Connected — tailing log',waiting:'Waiting for Game.log…',
      rotated:'New session detected',error:'Error'};
    setStatus(msg.status, map[msg.status]||msg.status);
    if(msg.path) document.getElementById('path').textContent = '📁 ' + msg.path;
  }
};
es.onopen = ()=> setStatus('connected','Connected — tailing log');
es.onerror = ()=> setStatus('error','Reconnecting…');
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Star Citizen live log analyzer")
    ap.add_argument("--log", help="path to Game.log (auto-detected if omitted)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--poll", type=float, default=0.5, help="tail poll interval (s)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    log_path = discover_log_path(args.log)
    analyzer = Analyzer()
    analyzer.log_path = log_path or (args.log or "(searching…)")

    if log_path:
        print(f"[analyzer] watching: {log_path}")
    else:
        print("[analyzer] no Game.log found yet — will keep looking.")
        print("            candidates checked:")
        for p in candidate_log_paths():
            print("             -", p)
        # Still tail the first candidate so it picks up once the game starts.
        log_path = candidate_log_paths()[0]

    tailer = LogTailer(log_path, analyzer, poll=args.poll)
    tailer.start()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(analyzer))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[analyzer] dashboard: {url}")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[analyzer] shutting down.")
    finally:
        tailer.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
