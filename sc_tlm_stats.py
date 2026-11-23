"""Per-recording telemetry summaries.

summarize() reads one recording file in a single pass and returns the index entry for it.
combine() folds several entries into one `sum` for a session.

Rules that hold throughout:
- A key is written only if its group was on. A group that was off is absent, never 0.
- A value that proto3 dropped counts as 0 only when its parent message is present
  (the gauge for vitals, a vehicle frame for flight values). Otherwise it is unknown.
- Durations use the real time to the next row. A recording is one continuous stream and its
  last row carries the end time, so unchanged stretches count in full.
- Records (max/min) include the `^` / `_` peaks. Totals are exact, only the series is thinned.
"""
import glob
import json
import os
import threading
import zlib
from datetime import datetime, timedelta

import sc_telemetry as tlm

SUMMARY_VERSION = 1
SERIES_MAX = 200
SERIES_MAX_COMBINED = 400

WALLET = "actor.state.uec_balance"
LOCO = "actor.state.locomotion"
CREW = "actor.state.crew_role"
VITALS = "actor.vitals."
ENV = "actor.environment."
INTOX = "actor.vitals.intoxication_level"
RAD = "actor.environment.ambient_radiation_rate"
EXT = "actor.environment.external_temperature"
VEHICLE = "vehicle.state."
LANDED = "vehicle.state.flight.state.is_landed"
GEAR = "vehicle.state.flight.state.gear"
INSTR = "vehicle.state.flight.instruments."
SPEED = INSTR + "speed"
GMETER = INSTR + "g_meter.current"
BOOST = INSTR + "boost.engaged"
BOOST_CRIT = INSTR + "boost.critical"
H2 = INSTR + "hydrogen_fuel"
QT = INSTR + "quantum_fuel"
SETTINGS = "vehicle.state.flight.settings."
ANN = "vehicle.state.annunciators."
LIGHTS = ("pwr", "wpn", "thr", "shld", "cool", "qdrv", "radr", "prox", "hit", "misl")
WARN_STATES = ("MINOR_ON", "MAJOR_ON", "MINOR_FLASHING", "MAJOR_FLASHING")
POWER = "vehicle.state.power."
SHIELDS = POWER + "shield_generators"
PRESET = "weapons.preset_name"
HEALTH_KEY = "actor.vitals.health.current"
CM_SLOTS = 16

GAUGES = {
    "health": "actor.vitals.health",
    "oxygen": "actor.vitals.oxygen",
    "hunger": "actor.vitals.hunger",
    "thirst": "actor.vitals.thirst",
}
LOCO_KEYS = {"ON_FOOT": "t_on_foot", "EVA": "t_eva", "SEATED": "t_seated"}
CREW_KEYS = {"PILOT": "t_pilot", "GUNNER": "t_gunner", "CREW": "t_crew"}
EVENT_KEYS = {"ACTOR_DOWNED": "n_downed", "ACTOR_DEAD": "n_dead",
              "ACTOR_RESPAWN": "n_respawn", "ACTOR_ENTER_SEAT": "n_seat"}
EVENT_PREFIX = "GAME_EVENT_TYPE_"

# combine rules
ADD_KEYS = (
    "uec_earned", "uec_spent", "t_on_foot", "t_eva", "t_seated", "t_pilot", "t_gunner", "t_crew",
    "n_downed", "n_dead", "n_respawn", "n_seat", "t_no_air", "t_starving", "t_dehydrated",
    "dist_m", "takeoffs", "landings", "gear_cycles", "t_boost", "n_boost_critical",
    "h2_used", "qt_used", "t_flying", "t_nav", "t_scm", "t_shields_off_flying",
)
MAX_KEYS = ("uec_max_rise", "uec_max_drop", "max_intox", "max_radiation", "max_ext_temp",
            "top_speed", "max_g")
MIN_KEYS = ("min_health", "min_ext_temp")
DICT_ADD_KEYS = ("warn", "cm_used", "presets", "t_ifcs")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _flags(state):
    """Which parent messages are present in this state, from one pass over the keys."""
    f = set()
    for k in state:
        if k.startswith("actor."):
            if k.startswith("actor.state."):
                f.add("astate")
            elif k.startswith(VITALS):
                f.add("vitals")
                for base in GAUGES.values():
                    if k.startswith(base + "."):
                        f.add(base)
            elif k.startswith(ENV):
                f.add("env")
        elif k.startswith("vehicle."):
            if k.startswith(VEHICLE):
                f.add("veh")
                if k.startswith(POWER):
                    f.add("power")
        elif k.startswith("weapons."):
            f.add("weapons")
    return f


def _parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _thin(series, limit):
    """Keep first, last and the points with the largest step from their predecessor."""
    if len(series) <= limit:
        return list(series)
    steps = sorted(range(1, len(series) - 1),
                   key=lambda i: abs(series[i][1] - series[i - 1][1]), reverse=True)
    keep = set(steps[:limit - 2]) | {0, len(series) - 1}
    return [series[i] for i in sorted(keep)]


class _Acc:
    def __init__(self, groups, started):
        self.g = set(groups)
        self.started = started
        self.out = {}
        o = self.out
        g = self.g
        if "wallet" in g:
            self.series = []
            self.bal = None
            o.update(uec_earned=0, uec_spent=0, uec_max_rise=0, uec_max_drop=0)
        if "you" in g:
            o.update({k: 0.0 for k in (*LOCO_KEYS.values(), *CREW_KEYS.values())})
        if "events" in g:
            o.update({k: 0 for k in EVENT_KEYS.values()})
        if "vitals" in g:
            o.update(t_no_air=0.0, t_starving=0.0, t_dehydrated=0.0, max_intox=0)
        if "flight" in g:
            o.update(top_speed=0, max_g=0, dist_m=0.0, takeoffs=0, landings=0, gear_cycles=0,
                     t_boost=0.0, n_boost_critical=0, h2_used=0.0, qt_used=0.0,
                     t_flying=0.0)
            if "ship" in g:
                o.update(t_nav=0.0, t_scm=0.0)
        if "ship" in g:
            o["warn"] = {}
            if "flight" in g:
                o["t_shields_off_flying"] = 0.0
        if "weapons" in g:
            o["cm_used"] = {}
            o["presets"] = {}
        if "settings" in g and "flight" in g:
            o["t_ifcs"] = {}
        self.prev_landed = None
        self.prev_gear = None
        self.prev_crit = None
        self.prev_h2 = None
        self.prev_qt = None
        self.prev_cm = None
        self.prev_light = {}
        self.prev = None     # (t, state, ctx) of the previous row

    # --- per-row context -------------------------------------------------------

    def _context(self, state):
        flags = _flags(state)
        veh = "veh" in flags
        if "you" in self.g:
            seated = veh and state.get(LOCO) == "SEATED"
        else:
            seated = veh        # a vehicle frame only exists while seated
        flying = "flight" in self.g and seated and not state.get(LANDED)
        return flags, seated, flying

    def _gauge(self, state, flags, base):
        """Current value of a gauge. 0 when the gauge is present but `current` was dropped,
        None when the gauge isn't there at all."""
        key = base + ".current"
        if key in state:
            v = state[key]
            return v if _num(v) else None
        return 0.0 if base in flags else None

    # --- one row ---------------------------------------------------------------

    def step(self, t, state, events, peaks):
        if self.prev is not None:
            pt, pstate, pctx = self.prev
            dt = max(t - pt, 0.0)
            if dt > 0:
                self._durations(pstate, pctx, dt)
        ctx = self._context(state)
        if "events" in self.g:
            for e in events:
                name = EVENT_KEYS.get(e[len(EVENT_PREFIX):] if e.startswith(EVENT_PREFIX) else e)
                if name:
                    self.out[name] += 1
        if "wallet" in self.g:
            self._wallet(t, state, ctx[0])
        if "vitals" in self.g:
            self._vitals_records(state, peaks, ctx[0])
        if "surroundings" in self.g:
            self._env_records(state, peaks, ctx[0])
        if "flight" in self.g:
            self._flight(state, peaks, "veh" in ctx[0])
        if "ship" in self.g:
            self._warn(state)
        if "weapons" in self.g:
            self._countermeasures(state, ctx[0])
        self.prev = (t, state, ctx)

    # --- time-weighted ---------------------------------------------------------

    def _durations(self, state, ctx, dt):
        o, g = self.out, self.g
        flags, seated, flying = ctx
        veh = "veh" in flags
        if "you" in g:
            k = LOCO_KEYS.get(state.get(LOCO))
            if k:
                o[k] += dt
            k = CREW_KEYS.get(state.get(CREW))
            if k:
                o[k] += dt
        if "vitals" in g:
            if self._gauge(state, flags, GAUGES["oxygen"]) == 0:
                o["t_no_air"] += dt
            if self._gauge(state, flags, GAUGES["hunger"]) == 0:
                o["t_starving"] += dt
            if self._gauge(state, flags, GAUGES["thirst"]) == 0:
                o["t_dehydrated"] += dt
        if "flight" in g:
            if veh:
                speed = state.get(SPEED)
                if _num(speed):
                    o["dist_m"] += speed * dt
                if state.get(BOOST) is True:
                    o["t_boost"] += dt
            if flying:
                o["t_flying"] += dt
                if "ship" in g:
                    if state.get(ANN + "shld") == "OFF" and state.get(ANN + "qdrv") != "OFF":
                        o["t_nav"] += dt
                    else:
                        o["t_scm"] += dt
                    if "power" in flags and not state.get(SHIELDS):
                        o["t_shields_off_flying"] += dt
                if "settings" in g:
                    ifcs = o["t_ifcs"]
                    for k, v in state.items():
                        if v is True and k.startswith(SETTINGS):
                            name = k[len(SETTINGS):]
                            if name.endswith(".active"):
                                name = name[:-len(".active")]
                            if "." not in name:
                                ifcs[name] = ifcs.get(name, 0.0) + dt
        if "weapons" in g:
            name = state.get(PRESET)
            if isinstance(name, str) and name:
                o["presets"][name] = o["presets"].get(name, 0.0) + dt

    # --- wallet ----------------------------------------------------------------

    def _wallet(self, t, state, flags):
        o = self.out
        bal = state.get(WALLET)
        if WALLET not in state and "astate" in flags:
            bal = 0     # proto3 drops a zero balance
        if not _num(bal):
            return
        if self.bal is None:
            o["uec_start"] = bal
            self._point(t, bal)
        elif bal != self.bal:
            diff = bal - self.bal
            if diff > 0:
                o["uec_earned"] += diff
                o["uec_max_rise"] = max(o["uec_max_rise"], diff)
            else:
                o["uec_spent"] -= diff
                o["uec_max_drop"] = max(o["uec_max_drop"], -diff)
            self._point(t, bal)
        self.bal = bal
        o["uec_end"] = bal

    def _point(self, t, bal):
        if self.started is not None:
            iso = (self.started + timedelta(seconds=t)).isoformat(timespec="seconds")
            self.series.append([iso, bal])

    # --- records ---------------------------------------------------------------

    def _vitals_records(self, state, peaks, flags):
        o = self.out
        hp = HEALTH_KEY
        cur = self._gauge(state, flags, GAUGES["health"])
        lo, _ = peaks.get(hp, (None, None))
        low = [v for v in (cur, lo) if _num(v)]
        if low:
            o["min_health"] = min(low + ([o["min_health"]] if "min_health" in o else []))
        if INTOX in state or "vitals" in flags:
            v = state.get(INTOX, 0)
            _, hi = peaks.get(INTOX, (None, None))
            for x in (v, hi):
                if _num(x):
                    o["max_intox"] = max(o["max_intox"], x)

    def _env_records(self, state, peaks, flags):
        o = self.out
        if RAD in state or "env" in flags:
            _, hi = peaks.get(RAD, (None, None))
            for x in (state.get(RAD, 0), hi):
                if _num(x):
                    o["max_radiation"] = max(o.get("max_radiation", x), x)
        lo, hi = peaks.get(EXT, (None, None))
        for x in (state.get(EXT), lo, hi):
            if _num(x):
                o["max_ext_temp"] = max(o.get("max_ext_temp", x), x)
                o["min_ext_temp"] = min(o.get("min_ext_temp", x), x)

    # --- flight ----------------------------------------------------------------

    def _flight(self, state, peaks, veh):
        o = self.out
        if not veh:
            self.prev_landed = self.prev_gear = self.prev_crit = None
            self.prev_h2 = self.prev_qt = None
            return
        for path, key in ((SPEED, "top_speed"), (GMETER, "max_g")):
            _, hi = peaks.get(path, (None, None))
            for x in (state.get(path), hi):
                if _num(x):
                    o[key] = max(o[key], x)
        landed = bool(state.get(LANDED))
        if self.prev_landed is not None:
            if self.prev_landed and not landed:
                o["takeoffs"] += 1
            elif landed and not self.prev_landed:
                o["landings"] += 1
        self.prev_landed = landed
        gear = state.get(GEAR)
        if gear == "RETRACTED" and self.prev_gear not in (None, "RETRACTED"):
            o["gear_cycles"] += 1
        self.prev_gear = gear
        crit = state.get(BOOST_CRIT) is True
        if crit and self.prev_crit is False:
            o["n_boost_critical"] += 1
        self.prev_crit = crit
        for path, attr, key in ((H2, "prev_h2", "h2_used"), (QT, "prev_qt", "qt_used")):
            v = state.get(path, 0.0)
            if not _num(v):
                continue
            prev = getattr(self, attr)
            if prev is not None and v < prev:
                o[key] += prev - v
            setattr(self, attr, v)

    def _warn(self, state):
        warn = self.out["warn"]
        for light in LIGHTS:
            cur = state.get(ANN + light)
            if cur in WARN_STATES and self.prev_light.get(light) in ("NOMINAL", "OFF"):
                warn[light] = warn.get(light, 0) + 1
            self.prev_light[light] = cur

    def _countermeasures(self, state, flags):
        if "weapons" not in flags:
            self.prev_cm = None
            return
        cur = {}
        for i in range(CM_SLOTS):
            typ = state.get("weapons.countermeasures[%d].type" % i)
            if not isinstance(typ, str):
                continue
            amt = state.get("weapons.countermeasures[%d].amount" % i, 0)
            cur[typ] = cur.get(typ, 0) + (amt if _num(amt) else 0)
        used = self.out["cm_used"]
        for typ, amt in cur.items():
            used.setdefault(typ, 0)
            if self.prev_cm is not None and typ in self.prev_cm and amt < self.prev_cm[typ]:
                used[typ] += self.prev_cm[typ] - amt
        self.prev_cm = cur

    # --- result ----------------------------------------------------------------

    def result(self):
        o = self.out
        if "wallet" in self.g and self.started is not None:
            o["uec_series"] = _thin(self.series, SERIES_MAX)
        return _round(o)



def _round(o):
    out = {}
    for k, v in o.items():
        if isinstance(v, float):
            out[k] = round(v, 6 if k in ("h2_used", "qt_used") else 3)
        elif isinstance(v, dict):
            out[k] = {kk: round(vv, 3) if isinstance(vv, float) else vv for kk, vv in v.items()}
        else:
            out[k] = v
    return out


def summarize(path):
    header, rows = tlm.read_recording(path)
    if not rows:
        return None
    started = _parse_iso(header.get("started"))
    groups = [g for g in header.get("groups") or () if isinstance(g, str)]
    acc = _Acc(groups, started)
    last_t = 0.0
    for t, state, events, peaks in tlm.iter_states(rows):
        if not _num(t):
            continue
        acc.step(t, state, events, peaks)
        last_t = t
    ended = None
    if started is not None:
        ended = (started + timedelta(seconds=last_t)).isoformat(timespec="seconds")
    return {"file": os.path.basename(path), "channel": header.get("channel"),
            "started": header.get("started"), "ended": ended, "build": header.get("build"),
            "groups": groups, "v": SUMMARY_VERSION, "sum": acc.result()}


def combine(entries):
    """One `sum` for a session from several entries (in time order)."""
    ordered = sorted(entries, key=lambda e: (e.get("started") is None, e.get("started") or ""))
    sums = [e.get("sum") or {} for e in ordered]
    out = {}
    for key in ADD_KEYS:
        vals = [s[key] for s in sums if key in s]
        if vals:
            out[key] = sum(vals)
    for key in MAX_KEYS:
        vals = [s[key] for s in sums if key in s]
        if vals:
            out[key] = max(vals)
    for key in MIN_KEYS:
        vals = [s[key] for s in sums if key in s]
        if vals:
            out[key] = min(vals)
    starts = [s["uec_start"] for s in sums if "uec_start" in s]
    ends = [s["uec_end"] for s in sums if "uec_end" in s]
    if starts:
        out["uec_start"] = starts[0]
    if ends:
        out["uec_end"] = ends[-1]
    series = [p for s in sums for p in s.get("uec_series") or ()]
    if any("uec_series" in s for s in sums):
        out["uec_series"] = _thin(series, SERIES_MAX_COMBINED)
    for key in DICT_ADD_KEYS:
        parts = [s[key] for s in sums if key in s]
        if parts:
            merged = {}
            for d in parts:
                for k, v in d.items():
                    merged[k] = merged.get(k, 0) + v
            out[key] = merged
    return _round(out)


# --- index, retention, matching, career ----------------------------------------------

INDEX_NAME = "index.json"
_index_lock = threading.Lock()
CAREER_SERIES_MAX = 400
MATCH_SLACK = 120       # seconds after a session's end that a recording may still start
OPEN_WINDOW = 60        # a session ending this close to now may still be running


def tlm_dir(app_dir):
    d = os.path.join(app_dir, "telemetry")
    os.makedirs(d, exist_ok=True)
    return d


def recording_files(root):
    return sorted(glob.glob(os.path.join(root, "*", "*.jsonl.gz")))


def load_index(root):
    try:
        with open(os.path.join(root, INDEX_NAME), "r", encoding="utf-8") as f:
            idx = json.load(f)
    except (OSError, ValueError):
        return {}
    return idx if isinstance(idx, dict) else {}


def save_index(root, idx):
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, INDEX_NAME)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f)
    os.replace(tmp, path)


def _skipped(path, skip):
    return os.path.basename(path) in skip or path in skip


def _summarize_safe(path):
    """Index entry for a recording, an `empty` marker when it has no rows, None when it
    can't be read this run."""
    try:
        entry = summarize(path)
        if entry is not None:
            return entry
        header, _rows = tlm.read_recording(path)
    except (OSError, ValueError, EOFError, zlib.error):
        return None
    if not header:      # unreadable, not just empty
        return None
    return {"file": os.path.basename(path), "channel": header.get("channel"),
            "started": header.get("started"), "v": SUMMARY_VERSION, "empty": True}


def _current(idx, name):
    cur = idx.get(name)
    return cur is not None and cur.get("v", 0) >= SUMMARY_VERSION


def refresh_index(root, skip=frozenset()):
    """Summarize recordings that are new or have an older summary version. Saves and returns
    the index. Entries whose file is gone stay as they are."""
    with _index_lock:
        idx = load_index(root)
        changed = False
        for path in recording_files(root):
            if _skipped(path, skip):
                continue
            name = os.path.basename(path)
            if _current(idx, name):
                continue
            entry = _summarize_safe(path)
            if entry is not None:
                idx[name] = entry
                changed = True
        if changed:
            save_index(root, idx)
        return idx


def _file_started(path, idx):
    name = os.path.basename(path)
    try:
        return datetime.strptime(name.split(".")[0], "%Y%m%d-%H%M%S")
    except ValueError:
        return _parse_iso((idx.get(name) or {}).get("started"))


def _delete_indexed(idx, path):
    """Delete a recording once its summary is in the index. Keeps the file when it can't be
    summarized. Returns (deleted, index changed)."""
    name = os.path.basename(path)
    changed = False
    if not _current(idx, name):
        entry = _summarize_safe(path)
        if entry is None:
            return False, False
        idx[name] = entry
        changed = True
    try:
        os.remove(path)
    except OSError:
        return False, changed
    return True, changed


def apply_retention(root, keep_days, now, skip=frozenset()):
    """Delete recordings older than keep_days. Index entries stay. Returns files deleted."""
    if not keep_days:
        return 0
    cutoff = now - timedelta(days=keep_days)
    n = 0
    with _index_lock:
        idx = load_index(root)
        changed = False
        for path in recording_files(root):
            if _skipped(path, skip):
                continue
            started = _file_started(path, idx)
            if started is not None and started < cutoff:
                done, ch = _delete_indexed(idx, path)
                n += done
                changed = changed or ch
        if changed:
            save_index(root, idx)
    return n


def disk_usage(root):
    total = 0
    for path in recording_files(root):
        try:
            total += os.path.getsize(path)
        except OSError:
            pass
    return total


def delete_all_recordings(root, skip=frozenset()):
    n = 0
    with _index_lock:
        idx = load_index(root)
        changed = False
        for path in recording_files(root):
            if _skipped(path, skip):
                continue
            done, ch = _delete_indexed(idx, path)
            n += done
            changed = changed or ch
        if changed:
            save_index(root, idx)
    return n


def match(sessions, idx, now=None):
    """Assign index entries to sessions. Returns ({id(session): [entries]}, unmatched).
    Times are local naive on both sides."""
    now = now or datetime.now()
    rows = []
    latest = {}
    for s in sessions:
        start = s.get("start")
        if start is None:
            continue
        ch = s.get("channel") or "LIVE"
        rows.append((s, ch, start, start + timedelta(seconds=s.get("dur") or 0)))
        if ch not in latest or start > latest[ch][0]:
            latest[ch] = (start, s)
    windows = []
    for s, ch, start, end in rows:
        is_open = abs((now - end).total_seconds()) <= OPEN_WINDOW and latest[ch][1] is s
        windows.append((s, ch, start, None if is_open else end + timedelta(seconds=MATCH_SLACK)))
    matched = {}
    unmatched = []
    for entry in sorted(idx.values(), key=lambda e: e.get("started") or ""):
        if entry.get("empty"):
            continue
        started = _parse_iso(entry.get("started"))
        best = None
        if started is not None:
            ch = entry.get("channel") or "LIVE"
            for s, wch, start, limit in windows:
                if wch == ch and start <= started and (limit is None or started <= limit):
                    if best is None or start > best[1]:
                        best = (s, start)
        if best is None:
            unmatched.append(entry)
        else:
            matched.setdefault(id(best[0]), []).append(entry)
    return matched, unmatched


def attach(sessions, idx, now=None):
    """Copies of sessions with `_tlm` where recordings matched. Inputs are not changed."""
    matched, unmatched = match(sessions, idx, now)
    out = []
    for s in sessions:
        entries = matched.get(id(s))
        if entries:
            s = dict(s)
            s["_tlm"] = combine(entries)
        out.append(s)
    return out, unmatched


# career folding. Same per-key rules as combine().

def new_acc():
    return {"n_tlm": 0, "vals": {}, "n": {}, "series": [], "at": {}}


def fold_tlm(acc, s_tlm, at=None):
    """Fold one session's combined summary into the career accumulator. `at` is the
    session's start: uec_start and uec_end come from the earliest and latest session.
    Without it, fold order decides."""
    if s_tlm is None:
        return
    acc["n_tlm"] += 1
    vals, cnt = acc["vals"], acc["n"]
    ats = acc["at"]
    for key, v in s_tlm.items():
        if key == "uec_series":
            acc["series"].extend(v)
            continue
        if key in DICT_ADD_KEYS:
            cur = vals.setdefault(key, {})
            for k, x in v.items():
                cur[k] = cur.get(k, 0) + x
        elif key in ADD_KEYS:
            vals[key] = vals.get(key, 0) + v
        elif key in MAX_KEYS:
            vals[key] = max(vals[key], v) if key in vals else v
        elif key in MIN_KEYS:
            vals[key] = min(vals[key], v) if key in vals else v
        elif key in ("uec_start", "uec_end"):
            prev = ats.get(key)
            if key not in vals:
                take = True
            elif at is None or prev is None:
                take = key == "uec_end"
            else:
                take = at < prev if key == "uec_start" else at >= prev
            if take:
                vals[key] = v
                ats[key] = at
        else:
            continue
        cnt[key] = cnt.get(key, 0) + 1
    # flight time of the sessions that have IFCS time, the base for IFCS shares
    if "t_ifcs" in s_tlm and "t_flying" in s_tlm:
        vals["t_flying_ifcs"] = vals.get("t_flying_ifcs", 0) + s_tlm["t_flying"]
        cnt["t_flying_ifcs"] = cnt.get("t_flying_ifcs", 0) + 1


def finalize_tlm(acc, sessions):
    rounded = _round(acc["vals"])
    stats = {k: {"v": v, "n": acc["n"][k]} for k, v in rounded.items()}
    return {"n_sessions": sessions, "n_tlm": acc["n_tlm"], "stats": stats,
            "wallet_series": _thin(sorted(acc["series"], key=lambda p: p[0]),
                                   CAREER_SERIES_MAX)}
