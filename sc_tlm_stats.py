"""Per-recording telemetry summaries.

summarize() reads one recording file in a single pass and returns the index entry for it.
combine() folds several entries into one `sum` for a session.

Rules that hold throughout:
- A key is written only if its group was on. A group that was off is absent, never 0.
- A value that proto3 dropped counts as 0 only when its parent message is present
  (the gauge for vitals, a vehicle frame for flight values). Otherwise it is unknown.
- Durations use the time to the next row, capped at STEP_CAP seconds per step.
- Records (max/min) include the `^` / `_` peaks. Totals are exact, only the series is thinned.
"""
import os
from datetime import datetime, timedelta

import sc_telemetry as tlm

SUMMARY_VERSION = 1
STEP_CAP = 5.0
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
SHIELDS = "vehicle.state.power.shield_generators"
PRESET = "weapons.preset_name"
CLASS_KEY = "vehicle.state.class_name"
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


def _has_prefix(state, prefix):
    return any(k.startswith(prefix) for k in state)


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
        veh = CLASS_KEY in state or _has_prefix(state, VEHICLE)
        if "you" in self.g:
            seated = veh and state.get(LOCO) == "SEATED"
        else:
            seated = veh        # a vehicle frame only exists while seated
        flying = "flight" in self.g and seated and not state.get(LANDED)
        return veh, seated, flying

    def _gauge(self, state, base):
        """Current value of a gauge. 0 when the gauge is present but `current` was dropped,
        None when the gauge isn't there at all."""
        key = base + ".current"
        if key in state:
            v = state[key]
            return v if _num(v) else None
        if base + ".max" in state or _has_prefix(state, base + "."):
            return 0.0
        return None

    # --- one row ---------------------------------------------------------------

    def step(self, t, state, events, peaks):
        if self.prev is not None:
            pt, pstate, pctx = self.prev
            dt = min(max(t - pt, 0.0), STEP_CAP)
            if dt > 0:
                self._durations(pstate, pctx, dt)
        ctx = self._context(state)
        if "events" in self.g:
            for e in events:
                name = EVENT_KEYS.get(e[len(EVENT_PREFIX):] if e.startswith(EVENT_PREFIX) else e)
                if name:
                    self.out[name] += 1
        if "wallet" in self.g:
            self._wallet(t, state)
        if "vitals" in self.g:
            self._vitals_records(state, peaks)
        if "surroundings" in self.g:
            self._env_records(state, peaks)
        if "flight" in self.g:
            self._flight(state, peaks, ctx[0])
        if "ship" in self.g:
            self._warn(state)
        if "weapons" in self.g:
            self._countermeasures(state)
        self.prev = (t, state, ctx)

    # --- time-weighted ---------------------------------------------------------

    def _durations(self, state, ctx, dt):
        o, g = self.out, self.g
        veh, seated, flying = ctx
        if "you" in g:
            k = LOCO_KEYS.get(state.get(LOCO))
            if k:
                o[k] += dt
            k = CREW_KEYS.get(state.get(CREW))
            if k:
                o[k] += dt
        if "vitals" in g:
            if self._gauge(state, GAUGES["oxygen"]) == 0:
                o["t_no_air"] += dt
            if self._gauge(state, GAUGES["hunger"]) == 0:
                o["t_starving"] += dt
            if self._gauge(state, GAUGES["thirst"]) == 0:
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
                    if not state.get(SHIELDS):
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

    def _wallet(self, t, state):
        o = self.out
        bal = state.get(WALLET)
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

    def _vitals_records(self, state, peaks):
        o = self.out
        hp = HEALTH_KEY
        cur = self._gauge(state, GAUGES["health"])
        lo, _ = peaks.get(hp, (None, None))
        low = [v for v in (cur, lo) if _num(v)]
        if low:
            o["min_health"] = min(low + ([o["min_health"]] if "min_health" in o else []))
        if INTOX in state or _has_prefix(state, VITALS):
            v = state.get(INTOX, 0)
            _, hi = peaks.get(INTOX, (None, None))
            for x in (v, hi):
                if _num(x):
                    o["max_intox"] = max(o["max_intox"], x)

    def _env_records(self, state, peaks):
        o = self.out
        if RAD in state or _has_prefix(state, ENV):
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
            if cur in WARN_STATES and cur != self.prev_light.get(light):
                warn[light] = warn.get(light, 0) + 1
            self.prev_light[light] = cur

    def _countermeasures(self, state):
        if not (PRESET in state or _has_prefix(state, "weapons.")):
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
    sums = [e.get("sum") or {} for e in entries]
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
