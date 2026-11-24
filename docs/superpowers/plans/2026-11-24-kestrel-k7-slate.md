# Kestrel K-7 Flight Slate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace CSR's basic Live view with the Kestrel K-7 Flight Slate, fed only by telemetry, the live Game.log tail and generated ship data.

**Architecture:** The approved mock's CSS, markup and renderers move into a new module `sc_slate.py`. Renderers read one `model` object that a pure JS function `slateModel(payload, ship)` builds from `/api/telemetry/live`. A new `sc_livelog.py` tails Game.log for places, notices, contracts, kills, injuries and loadout. Ship data is a generated module `sc_slate_ships.py`, served one ship at a time from `/api/telemetry/ship`.

**Tech Stack:** Python 3 stdlib (http.server, re, json, threading), vanilla JS/SVG in one HTML page, `unittest`, Node 22 for the JS model tests.

**Spec:** `docs/superpowers/specs/2026-11-24-kestrel-k7-slate-design.md`. Design source (local, git-excluded): `.superpowers/brainstorm/1287-1795499520/content/live-view-tablet.html` and `.superpowers/telemetry-watch/live-view-data-map.md`.

## Global Constraints

- Same page on `/live` (main server) and the phone server. No new server or port.
- Show only what the stream or logs give. A missing field renders "—" or hides its panel. LIVE 4.10 frames have no shields, hull health or master mode.
- No mock or sample data in shipped code: none of `LtFreeman`, `Xenthos`, `Disturbed164`, `PaleHorse`, `Kaito_SC`, `82,312,450`, `archelium`, `Shallow Fields`, the demo bar (`id="demo"`).
- Page stays light: `slate_html()` under 450 000 characters. Ship data only via `/api/telemetry/ship`. No `data:image` URI over 8 KB.
- Keys show state only. No action is ever sent to the game. No eject or self-destruct keys.
- User-facing text: short, flat, no ", so", no "; " joining clauses, no hedging.
- Do not restructure `serve()`, the status menu, the watchdog or `csr.spec`. In `serve()` the only allowed change is adding `"/api/telemetry/ship"` to the existing telemetry route tuple.
- New modules are plain imports from `sc_stats.py` (PyInstaller finds them). No new data files in `csr.spec`.
- Tests: `py -3 -m unittest discover -s tests` stays green (444 before this plan). Node tests skip when `node` is missing.
- Commits: conventional prefixes (`feat:`, `test:`, `docs:`), no co-author trailer.

## Review Focus

- Telemetry not running (state `idle`/`waiting`, empty `snapshot`): the page shows a "Waiting for the game" panel, no JS errors, keeps polling. Test in Task 4 (model) and Task 5 (markup has the panel).
- LIVE 4.10 frame without shields, `vehicle.state.health`, `master_mode`: model fields are `null`, renderers print "—". Test in Task 4 with the real 4.10 fixture.
- Unknown ship class (new ship, ground vehicle): `/api/telemetry/ship` answers 404, the page draws a size-free generic outline and lists the stream's weapons. Tests in Tasks 1 and 3.
- Game.log rotated or truncated (new session): the tail resets and old contracts, kills and notices are gone. Test in Task 2.
- Phone key on the new ship route: no key or a wrong key gets 403 like `/live`. Test in Task 3.

---

## File Structure

- Create `sc_slate_ships.py` — generated. `SHIPS_JSON` (compact JSON string) and `ship_payload(cls) -> dict | None`.
- Create `tools/build_slate_ships.py` — regenerates `sc_slate_ships.py` from `.superpowers/ship-data/` and the mock's `SPEEDS`/`SDIM` tables.
- Create `sc_livelog.py` — `LogState` (line parser + state) and `LiveTail` (follows the newest channel's Game.log).
- Create `sc_slate.py` — `SLATE_CSS`, `SLATE_BODY`, `SLATE_MODEL_JS` (pure, Node-testable), `SLATE_JS` (renderers, polling) and `slate_html() -> str`.
- Create `tools/slate_dev.py` — dev server: the slate plus a replayed fixture payload, for the browser audit.
- Modify `sc_stats.py` — `LIVE_HTML()` returns the slate; `tlm_api_get` adds `log` and the ship route; phone handler allows the ship route; delete `LIVE_TEMPLATE`.
- Tests: `tests/test_slate_ships.py`, `tests/test_livelog.py`, `tests/test_slate_routes.py`, `tests/test_slate_model.py`, `tests/test_slate_page.py`.

---

### Task 1: Ship data module

**Files:**
- Create: `tools/build_slate_ships.py`, `sc_slate_ships.py` (generated, committed)
- Test: `tests/test_slate_ships.py`

**Interfaces:**
- Produces: `sc_slate_ships.ship_payload(cls: str) -> dict | None`. Keys: `class, name, short, career, role, seats, vtol, wings, mining, salvage, tractor, missiles, turrets, pilotGuns, pilotTurrets, shape, dims, speeds, sys`.
  - `turrets`: list of `[hardpoint, kind ('m'|'r'|'u'|'x'|'p'), mounts, gun, size, slaveable, guns:[[name,size]...], ctl ('pilot'|'gunner'|'both'|null)]` exactly as `ships_live.json` rows.
  - `shape`: `[length_m, width_m, outline[[x,y]...], hardpoints{name:[x,y]}]` or `null`. `dims`: the mock's `SDIM` row or `null`. `speeds`: `[scm, boost, nav]` or `null`.
- Lookup is case-insensitive on `cls`.

- [ ] **Step 1: Write the failing tests**

```python
class ShipDataTests(unittest.TestCase):
    def test_perseus_turrets(self):
        p = sc_slate_ships.ship_payload("RSI_Perseus")
        top = next(t for t in p["turrets"] if t[0] == "hardpoint_turret_manned_top")
        self.assertEqual([g[0] for g in top[6]].count("RSI Medusa Cannon"), 2)
        self.assertEqual(len(top[6]), 4)
        rt = next(t for t in p["turrets"] if t[0] == "hardpoint_turret_remote_top")
        self.assertEqual(rt[7], "both")
    def test_speeds_and_shape(self):
        p = sc_slate_ships.ship_payload("aegs_gladius")
        self.assertEqual(len(p["speeds"]), 3)
        self.assertGreater(len(p["shape"][2]), 10)
    def test_unknown(self):
        self.assertIsNone(sc_slate_ships.ship_payload("NOPE_Ship"))
    def test_module_size(self):
        self.assertLess(len(sc_slate_ships.SHIPS_JSON), 1_200_000)
```

- [ ] **Step 2:** Run `py -3 -m unittest tests.test_slate_ships -v`. Expected: ImportError.
- [ ] **Step 3: Implement `tools/build_slate_ships.py`.** Read `.superpowers/ship-data/ships_live.json`, `ship_shapes.json` (outline + hp rounded to 0.1 m) and the mock's `SPEEDS` and `SDIM` tables (regex out of the mock HTML, as `shapepass.py` does). Key by class. Write `sc_slate_ships.py` with a header comment naming the sources and the command, `SHIPS_JSON = r'''…'''`, a lazily parsed dict and `ship_payload`. Drop the three Apollo Medivac tier rows' missing speeds by copying `RSI_Apollo_Medivac`.
- [ ] **Step 4:** Run the build tool, then the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: ship data for the Kestrel slate`.

### Task 2: Live Game.log state

**Files:**
- Create: `sc_livelog.py`
- Test: `tests/test_livelog.py`

**Interfaces:**
- Produces: `LogState()` with `feed(line: str, t: float) -> None`, `reset() -> None`, `state() -> dict`.
- Produces: `LiveTail(path_fn: Callable[[], str | None])` with `poll() -> None` (reads appended bytes, resets on truncation or a new path) and `state() -> dict`. Module function `get_state(path_fn: Callable[[], str | None]) -> dict` lazily starts one daemon `LiveTail(path_fn)` polling every 2 s on the first call and returns its `state()`. `sc_stats` passes its newest-channel Game.log function (Task 3).
- `state()` shape (all keys always present):
  `{"place": str|None, "jurisdiction": str|None, "armistice": bool|None, "notices": [{"t","text","group"}] (newest last, max 40), "contracts": [{"name","status":"active"|"complete"|"failed","objective":str|None,"t"}], "kills": [{"t","cls","level":1|2,"reason","killer"}] (max 30), "injuries": {"h"|"t"|"la"|"ra"|"ll"|"rl": "min"|"mod"|"sev"}, "loadout": {port: item_class}, "blueprints": int}`
- Notice `group` from the text: `alert` (injury, destroyed, crimestat, hostile, warning), `msn` (Contract, Objective), `money` (aUEC, Awarded, Reward), `people` (party, friend), `ship` (hangar, docking, vehicle), `zone` (Armistice, Jurisdiction, Monitored), else `tip`.

- [ ] **Step 1: Write the failing tests** (lines copied in the formats of the `sc_stats` regexes `NOTIF_RE`, `CONTRACT_NOTIF_RE`, `JURIS_RE`, `PLACE_RE`, `INJURY_RE`, `ATTACH_RE`/`ATTACH_PORT_RE`, `VKILL_RE`, `BLUEPRINT_RE`, `HAUL_RE`):

```python
def test_patterns_match_sc_stats(self):
    for name in ("NOTIF_RE", "CONTRACT_NOTIF_RE", "JURIS_RE", "INJURY_RE", "VKILL_RE", "BLUEPRINT_RE", "PLACE_RE", "HAUL_RE"):
        self.assertEqual(getattr(sc_livelog, name).pattern, getattr(sc_stats, name).pattern, name)
def test_contract_lifecycle(self):
    s = sc_livelog.LogState()
    s.feed(notif('Contract Accepted: Bounty: Test Target'), 1)
    s.feed(notif('New Objective: Neutralize Target'), 2)
    self.assertEqual(s.state()["contracts"][0], {"name": "Bounty: Test Target", "status": "active", "objective": "Neutralize Target", "t": 1})
    s.feed(notif('Contract Complete: Bounty: Test Target'), 3)
    self.assertEqual(s.state()["contracts"][0]["status"], "complete")
def test_armistice_and_place(self): ...      # "Entering Armistice Zone" -> True, "Leaving Armistice Zone" -> False, PLACE_RE line -> place
def test_injury_and_kill(self): ...          # "Moderate Injury Detected - Left Leg - ..." -> injuries["ll"] == "mod"; VKILL_RE line -> kills[-1]["level"] == 2
def test_notice_groups_and_cap(self): ...    # 50 notices -> 40 kept, groups as in the Interfaces list
def test_tail_resets_on_truncate(self):      # Review Focus: rotation
    # write 2 lines, poll, truncate the file and write 1 line, poll -> only the new line's state remains
```

- [ ] **Step 2:** Run `py -3 -m unittest tests.test_livelog -v`. Expected: ImportError.
- [ ] **Step 3: Implement `sc_livelog.py`.** Copy the nine regexes verbatim from `sc_stats.py` (the first test guards drift). `LiveTail.poll` opens the path in binary, seeks to the stored offset, decodes UTF-8 with `errors="replace"`, splits lines, keeps a partial last line. A path change or size below the offset calls `reset()` and reads from 0. Use the line's own time when present, else `time.time()`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: live Game.log state for the Kestrel slate`.

### Task 3: Server wiring

**Files:**
- Modify: `sc_stats.py` (`tlm_api_get`, `_PhoneHandler.do_GET`, the `serve()` route tuple only)
- Test: `tests/test_slate_routes.py`

**Interfaces:**
- Consumes: `sc_slate_ships.ship_payload`, `sc_livelog.get_state`.
- Produces: `live_payload(live: Callable[[], dict]) -> dict` in `sc_stats.py` = `live()` plus `"log": sc_livelog.get_state(newest_log)` where `newest_log` is a zero-argument function returning the newest channel's Game.log path (built from the existing `newest`-channel helper around line 1154). Never raises: a log error gives `"log": None`.
- Produces: `tlm_api_get(path, live, status, on_seen)` also answers `/api/telemetry/ship?c=CLASS` (returns `None` for unknown so callers send 404).
- `GET /api/telemetry/ship?c=X`: 200 JSON payload, 404 `{}` when unknown or `c` missing.

- [ ] **Step 1: Write the failing tests**

```python
def test_live_has_log(self):            # tlm_api_get("/api/telemetry/live", ...) JSON has "log" with the Task 2 keys
def test_live_log_error_is_none(self):  # get_state raising -> "log": None, status 200
def test_ship_route_main(self):         # tlm_api_get("/api/telemetry/ship?c=RSI_Perseus", ...) -> dict with "turrets"
def test_ship_route_unknown(self):      # -> None
def test_phone_ship_route(self):        # phone server: with key 200 + JSON; without key 403; unknown class 404
def test_phone_live_has_log(self):      # phone /api/telemetry/live body has "log"
```

Use the `_PhoneBase` pattern from `tests/test_tlm_phone.py` and patch `sc_livelog.get_state`.

- [ ] **Step 2:** Run `py -3 -m unittest tests.test_slate_routes -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** `tlm_api_get` parses the query with `urllib.parse`. The phone handler's allowed set becomes `("/live", "/api/telemetry/live", "/api/telemetry/ship")`, same key check, and builds the live body through `live_payload`. In `serve()`, add `"/api/telemetry/ship"` to the existing `elif p in (...)` tuple and send 404 when `tlm_api_get` returns `None`. Nothing else in `serve()`.
- [ ] **Step 4:** Run the new tests and `tests.test_tlm_phone`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: live log and ship data routes for the slate`.

### Task 4: Page model (pure JS)

**Files:**
- Create: `sc_slate.py` with `SLATE_MODEL_JS` only (the rest comes in Task 5)
- Test: `tests/test_slate_model.py` (writes the JS plus a harness to a temp dir and runs `node`; skipped without `node`)

**Interfaces:**
- Produces JS `slateModel(payload, ship) -> model` and `annunciatorLevel(v) -> 'off'|'ok'|'min'|'maj'` (`NOMINAL`→ok, `MINOR_ON`/`MINOR`→min, `MAJOR_ON`/`MAJOR`→maj, `OFF`/missing→off). Exported with `if (typeof module !== 'undefined') module.exports = {slateModel, annunciatorLevel}`.
- `model` (every leaf may be `null` when the stream lacks it):
  - `live`: true when `payload.state` is `live` and `snapshot` is non-empty. `wait`: short text for the waiting panel ("Waiting for the game", "Telemetry is off in the game").
  - `seat`: `'pilot'|'gunner'|'crew'|'foot'|'eva'` from `actor.state.locomotion` + `crew_role`.
  - `ship`: `{cls, name, type}` from `vehicle.state` (`class_name`; entity id stripped from `name`). `null` off the seat.
  - `flight`: `{speed, alt (null when ≥1e38), radarAlt, vsi, density, heading, g, gMax, h2, qt, boost:{reserves, critical}, gear, vtol, landed, master}`.
  - `ifcs`: `{key: {enabled, active}}` for every `ifcs_*` Availability in `flight.settings`. `power`: the 9 bools.
  - `ann`: `{pwr, wpn, thr, shld, cool, qdrv, radr, prox, hit, misl}` via `annunciatorLevel`. `threat`: `ann.misl==='maj' || ann.hit==='maj'`.
  - `shields`: `{faces:[F,R,B,L] | null, bubble: number|null}` in %, `hull`: % or `null`.
  - `weapons`: `[[name, size, powered, ready, inPreset]]` (name via `ship` gun names when the entity class matches, else the class with `_S3` → `S3`), `preset`, `gimbalsLocked`, `staggered`, `cm: {decoy, noise}`.
  - `vitals`: `{hp, o2 (0-100), food, water, temp, hr, gravity, pressure, intox}`; `env`: `{temp, rad, breathable}`; `wallet`.
  - `events`: counts of `ACTOR_DOWNED`, `ACTOR_DEAD`, `ACTOR_RESPAWN`, `ACTOR_ENTER_SEAT` in `payload.events`.
  - `log`: `payload.log` or an empty Task 2 state.

- [ ] **Step 1: Write the failing tests**

```python
def test_410_fixture_flight(self):      # decode the middle frame of live-4.10-takeoff.bin (as tests/test_tlm_fixture.py does)
    m = run_model(snapshot_payload)
    self.assertEqual(m["seat"], "pilot"); self.assertEqual(m["ship"]["cls"], "DRAK_Marauder")
    self.assertIsNone(m["flight"]["alt"])            # 3.4e38 = no reference body
    self.assertIsNone(m["hull"]); self.assertIsNone(m["shields"]["faces"])   # Review Focus: not on LIVE 4.10
    self.assertEqual(m["ann"]["wpn"], "min"); self.assertEqual(m["cm"]["decoy"], 48)
def test_410_fixture_on_foot(self):     # last frame of live-4.10-landing.bin -> seat "foot", ship None
def test_waiting(self):                 # {"state":"waiting","snapshot":{}} -> live False, wait text, no exception
def test_threat(self):                  # ann misl MAJOR_ON -> threat True
def test_5_0_shields(self):             # synthetic 5.0 snapshot with vehicle.shields faces + state.health -> faces list, hull %
```

- [ ] **Step 2:** Run `py -3 -m unittest tests.test_slate_model -v`. Expected: FAIL.
- [ ] **Step 3: Implement `SLATE_MODEL_JS`** as a pure function with optional chaining and number guards. Field paths as listed above (verified against the 4.10 capture: `vehicle.state.flight.instruments.*`, `vehicle.state.flight.settings.*`, `vehicle.state.power.*`, `vehicle.state.annunciators.*`, `weapons.weapons[]`, `weapons.countermeasures[]`, `actor.vitals.*`, `actor.state.*`, `actor.environment.*`). 5.0 paths per `docs/telemetry/schema-ptu-5.0.201.txt`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: slate data model from the telemetry payload`.

### Task 5: Slate page — frame, strip, MAIN seated, combat view

**Files:**
- Modify: `sc_slate.py` (add `SLATE_CSS`, `SLATE_BODY`, `SLATE_JS`, `slate_html()`)
- Test: `tests/test_slate_page.py`

**Interfaces:**
- Consumes: `slateModel` (Task 4), `/api/telemetry/live` and `/api/telemetry/ship` (Task 3).
- Produces: `slate_html() -> str` (fonts placeholder `/*__FONTS__*/` kept, replaced by `sc_stats.LIVE_HTML`). JS globals the later tasks extend: `MODEL`, `SHIP` (ship payload or null), `render()`, `poll()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_mock_data(self):
    h = sc_slate.slate_html()
    for s in ("LtFreeman", "Xenthos", "Disturbed164", "PaleHorse", "Kaito_SC", "82,312,450", "archelium", "Shallow Fields", 'id="demo"'):
        self.assertNotIn(s, h)
def test_size(self):
    h = sc_slate.slate_html(); self.assertLess(len(h), 450_000)
    for m in re.finditer(r"data:image[^\"')]+", h): self.assertLess(len(m.group(0)), 8192)
def test_parts_present(self):
    h = sc_slate.slate_html()
    for i in ('id="threat"', 'id="bezel"', 'id="page-ship"', 'id="hudsvg"', 'id="defsvg"', 'id="waitPanel"', 'id="scrBtn"', 'id="bellBtn"'):
        self.assertIn(i, h)
def test_no_action_hooks(self):
    self.assertNotIn("TriggerGameAction", sc_slate.slate_html()); self.assertNotIn("self-destruct", sc_slate.slate_html().lower())
```

- [ ] **Step 2:** Run `py -3 -m unittest tests.test_slate_page -v`. Expected: FAIL.
- [ ] **Step 3: Port from the mock.** Copy the CSS, the KESTREL frame, strip (location from `MODEL.log.place`, zone from `armistice`/`jurisdiction`, SC time, PC time, warning chips, ticker from `log.notices`, SCREEN key, bell), the MAIN seated grid, DEFENSE (`shipGeo`, `SHAPE`/`SDIM`/`SPEEDS` lookups replaced by `SHIP.shape`, `SHIP.dims`, `SHIP.speeds`), FLIGHT cluster `hud()`, combat view (`CV`, `cviewSync` driven by `MODEL.threat`), the YOU ring card, pinned mission (first active `log.contracts`). Replace every demo source (`combat`, `seat`, `VIT()`, `INJ()`, `AS`, `STATE`, random speed ticks, `outT`, `envData`, `PARTY`, `needs`, `feed`) with reads from `MODEL`/`SHIP`. Delete the demo bar, device simulation (`TB`, `PH`, `tbI`, `phI`, `dTab`, `dPh`) and keep the real-size logic: phone layout when `innerWidth < 600`, portrait when height > width, tablet scaling to the 1180×820 minimum. Deck keys become state lamps (gear, VTOL, lights, doors, locks, SCM/NAV, IFCS) with no click handlers. `poll()` fetches `/api/telemetry/live` every 250 ms while live, 2 s otherwise, and `/api/telemetry/ship?c=` once per class change. Show `#waitPanel` with `MODEL.wait` while `!MODEL.live`.
- [ ] **Step 4:** Run the tests. Expected: PASS. Then run `tools/slate_dev.py` (Task 8 Step 1 can be pulled forward) and check MAIN seated renders without console errors.
- [ ] **Step 5: Commit** `feat: Kestrel slate frame, MAIN and combat view`.

### Task 6: On foot, EVA, SYS, YOU, phone

**Files:**
- Modify: `sc_slate.py`
- Test: `tests/test_slate_page.py` (extend)

**Interfaces:**
- Consumes: `MODEL`, `SHIP`, `render()` from Task 5.

- [ ] **Step 1: Write the failing tests:** `id="bodyCard"`, `id="suitsvg"`, `id="lsSil"`, `id="page-sys"`, `id="page-you"`, `class="vgrid"` present. No "40 m from you" in the page.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Port** the on-foot/EVA dash (`bodyCard`, `suitHud`, `lastShipSil`; last ship = the last `MODEL.ship` + flight/shield values seen before `seat` became `foot`/`eva`), injuries from `log.injuries`, loadout names from `log.loadout` (armor class and temperature rating only when known, else name only), SYS (power groups and systems from `SHIP.sys` + `MODEL.power`/`ann`, countermeasure tiles, weapons per seat with `ctl` rules, read-only settings), YOU page (vitals boxes, body figure, session tiles from `MODEL.events` + `wallet` delta since page load), phone reduced layout and its bottom nav.
- [ ] **Step 4:** Run. Expected: PASS. Dev server check of foot and EVA with the landing fixture.
- [ ] **Step 5: Commit** `feat: slate on-foot, EVA, SYS and YOU`.

### Task 7: MSN, FEED, NAV, PPL

**Files:**
- Modify: `sc_slate.py`
- Test: `tests/test_slate_page.py` (extend)

**Interfaces:**
- Consumes: `MODEL.log` (Task 2 shape).

- [ ] **Step 1: Write the failing tests:** `id="page-msn"`, `id="page-feed"`, `id="page-nav"`, `id="page-ppl"` present; empty-state texts present: "No contracts yet", "No notices yet", "No ships destroyed nearby".
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3: Port** MSN (contracts list + pinned card from `log.contracts`), FEED (all `log.notices` with group filters, newest first), NAV (system map with the place marker when `log.place` matches a starmap name, jurisdiction, recent places), PPL (ships destroyed nearby from `log.kills` with silhouettes via `/api/telemetry/ship` cached per class, session tiles). Remove the party list and names-seen cards (non-goal). The bell count = notices since the panel was last opened.
- [ ] **Step 4:** Run. Expected: PASS.
- [ ] **Step 5: Commit** `feat: slate MSN, FEED, NAV and PPL from the live log`.

### Task 8: Switch over, audit, docs

**Files:**
- Create: `tools/slate_dev.py`
- Modify: `sc_stats.py` (`LIVE_HTML`, delete `LIVE_TEMPLATE`), `README.md`, `CHANGELOG.md`
- Test: `tests/test_slate_page.py` (extend), existing `tests/test_tlm_phone.py`

**Interfaces:**
- Consumes: `sc_slate.slate_html()`.
- Produces: `tools/slate_dev.py --fixture takeoff|landing [--port 7895]` serving `/live`, `/api/telemetry/live` (frames replayed in a loop) and `/api/telemetry/ship`.

- [ ] **Step 1: Write the failing test:** `sc_stats.LIVE_HTML()` contains `KESTREL` and not the old template's markers; `hasattr(sc_stats, "LIVE_TEMPLATE")` is False.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** `LIVE_HTML()` returns `sc_slate.slate_html()` with the fonts placeholder filled. Delete `LIVE_TEMPLATE`. Write `tools/slate_dev.py`. README: the Live view section names the Kestrel K-7 slate and what it shows, in the copy rules' voice. CHANGELOG: one entry under the unreleased version.
- [ ] **Step 4:** Run the full suite `py -3 -m unittest discover -s tests`. Expected: all pass. Then open `tools/slate_dev.py` in the browser pane and run the mock's `audit.js` over tablet, phone and desktop sizes for both fixtures. Expected: no overlaps, clipping or cut text.
- [ ] **Step 5: Commit** `feat: Kestrel K-7 Flight Slate replaces the basic Live view`. Then message the "CSR metrics from logs" session with the commit hash.
