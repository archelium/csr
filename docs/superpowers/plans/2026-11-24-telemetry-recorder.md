# Telemetry Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CSR records Star Citizen's local telemetry stream while the game runs, shows it live on the PC and a phone, and turns the recordings into career stats.

**Architecture:** The spec's single `sc_telemetry.py` is split in three for focus (no behaviour change). Three new stdlib modules beside `sc_stats.py`: `sc_telemetry.py` (protobuf schema from the exe, HTTP/2 gRPC client, recording writer/reader, recorder thread), `sc_tlm_stats.py` (per-recording summaries, sidecar index, retention, session matching, career folding), `sc_qr.py` (QR code as SVG). `sc_stats.py` gets wiring only: recorder start, watchdog hold, config, API routes, the standalone `/live` page, a phone-only second listener, settings card, stat cards, backup.

**Tech Stack:** Python 3.9+ stdlib only (`socket`, `struct`, `gzip`, `json`, `mmap`, `http.server`, `secrets`, `zipfile`, `unittest`). PyInstaller one-file exe. Vanilla JS in the generated page.

**Spec:** `docs/superpowers/specs/2026-11-24-telemetry-recorder-design.md`. Field notes: `docs/telemetry/NOTES.md`. Working prototype: `tools/sc_telemetry.py`.

## Global Constraints

- Stdlib only. No pip dependencies, at runtime or in tests.
- Data lives under `sc_stats.app_dir()`. Telemetry files under `<app_dir>/telemetry/`.
- Server: `127.0.0.1:2942`, path `/sc.game.telemetry.v1.GameTelemetryService/TelemetryStream`, empty request.
- Game setting line: `grpc.client.server.enabled = true` (the hyphen form doesn't work).
- Detail levels: `full` (every change), `normal` (smooth values at most every 0.5 s, default), `light` (at most every 5 s).
- Retention options: forever (default), 365, 90, 30 days.
- Groups default: all on except `force_feedback`.
- Phone listener: `0.0.0.0`, port 7879 (fall back to 7880–7889), off by default, live view only.
- Idle auto-exit (15 min, console hidden) is held while the recorder is connected.
- PTU/TP-only fields are recorded but get no stats or UI (user rule: wait for LIVE).
- UI is plain: existing cards, tokens, type scale. Keep element ids stable for a later design pass.
- UI copy and CHANGELOG follow the user's voice rules: short, flat, no ", so", no ";" joins, no "X, not Y" unless needed, no hedging.
- Never open a handle to the game process. Read only the local port and files.
- Commit messages: `feat:` / `fix:` / `test:` / `docs:` style, no co-author trailer.
- Tests: `python -m unittest tests.<module> -v` from the repo root.

## Review Focus

1. **Settings wiped by an unrelated save.** `save_config()` overwrites the whole file and `_accept_folder` calls `save_config({"live_path": path})`. Picking a folder must not erase telemetry settings or the phone key. Test in Task 7.
2. **CSR or the game dies mid-recording.** A recording cut off without `close()` must still read up to its last full line and summarize. Test in Task 3.
3. **Game running with telemetry off.** The recorder retries every 5 s quietly (no console spam, no busy loop) and reports `off` after 3 minutes of refusals. Test in Task 6.
4. **Local-time matching.** Session `start` is local wall-clock (parser 9+). Recording `started` must be local too, or every match is off by the timezone. Test in Task 5.
5. **Phone listener scope.** Any path other than `/live`, `/api/telemetry/live` returns 404, including `/api/shutdown`. Wrong or missing key returns 403. Test in Task 9.

---

### Task 1: Telemetry core module (schema, decode, gRPC client) and probe

Move the probe's proven code into an importable module, add schema caching and a raw-capture option.

**Files:**
- Create: `sc_telemetry.py`
- Create: `tools/telemetry_probe.py` (replaces `tools/sc_telemetry.py`, delete that file)
- Create: `tests/tlm_helpers.py` (protobuf encoder, synthetic descriptor, fake gRPC server)
- Create: `tests/test_tlm_core.py`
- Modify: `csr.spec` (`hiddenimports=["sc_names", "sc_telemetry", "sc_tlm_stats", "sc_qr"]`)

**Interfaces:**
- Produces:
  - `sc_telemetry.PKG = "sc.game.telemetry.v1"`, `STREAM_PATH`, `RESP_TYPE = PKG + ".TelemetryStreamResponse"`, `HOST = "127.0.0.1"`, `PORT = 2942`
  - `wire_fields(b: bytes) -> Iterator[tuple[int, int, object]]`
  - `descriptor_blobs(exe_path: str) -> list[tuple[str, bytes]]`
  - `class Schema`: `from_blobs(blobs) -> Schema`, `from_exe(path) -> Schema`, `to_json() -> dict`, `from_json(d) -> Schema`, `decode(type_name: str, b: bytes) -> dict`, `dump(out)`, attribute `hash: str` (first 12 hex of sha1 over the telemetry blobs in file order)
  - `load_schema(exe_path: str, cache_dir: str, channel: str) -> Schema | None`: cache file `schema-<channel>.json` holding `{"size", "mtime", "schema"}`. Exe unreadable or no telemetry blobs: return the cached schema if any, else None.
  - `class GrpcStream(host, port, path, request=b"", timeout=5.0)`: `.messages() -> Iterator[bytes]`, `.close()`. Raises `ConnectionError` on GOAWAY, RST_STREAM, trailers, EOF.
  - `tests.tlm_helpers`: `enc_varint(n)`, `enc_field(no, wire, payload)`, `make_descriptor() -> list[tuple[str, bytes]]` (a small `types.proto` + `api.proto` pair mirroring the real names for `TelemetryStreamResponse.actor/vehicle/events`, `ActorFrame`, `ActorVitals`, `ActorState`, `Gauge`, `GameEvent`, `VehicleFrame`, `VehicleState`, `VehicleFlightInstruments`, enums `ActorLocomotionState`, `GameEvent.GameEventType`), `FakeGrpcServer(frames: list[bytes], gap=0.0)` context manager exposing `.port`.

- [ ] **Step 1: Write failing tests** in `tests/test_tlm_core.py`:
  - `test_decode_nested_and_enums`: frame with `actor.vitals.health={current:80,max:100}`, `actor.state.locomotion=1`, `uec_balance=1000000` decodes to `{"actor":{"vitals":{"health":{"current":80.0,"max":100.0}},"state":{"locomotion":"ON_FOOT","uec_balance":1000000}}}`.
  - `test_decode_negative_int32`: `intoxication_level=-1` (10-byte varint) decodes to `-1`.
  - `test_decode_packed_floats`: repeated float field packed `[0.5, 1.0]` decodes to `[0.5, 1.0]`.
  - `test_enum_prefix_stripped`: `GAME_EVENT_TYPE_ACTOR_DOWNED` → `"ACTOR_DOWNED"`.
  - `test_schema_json_roundtrip`: `Schema.from_json(s.to_json()).decode(...)` equals `s.decode(...)` and `hash` is preserved.
  - `test_load_schema_cache`: write a fake exe file containing the synthetic blobs, `load_schema` twice; second call reads cache (patch `descriptor_blobs` to raise and assert same result). Then delete the exe: cached schema still returned.
  - `test_stream_reassembles_split_messages`: `FakeGrpcServer` sends 3 frames each split across two DATA frames, then trailers; `GrpcStream(...).messages()` yields 3 equal byte strings then raises `ConnectionError("...trailers...")`.
- [ ] **Step 2: Run** `python -m unittest tests.test_tlm_core -v`. Expected: FAIL, `ModuleNotFoundError: sc_telemetry`.
- [ ] **Step 3: Implement `sc_telemetry.py`** by moving the wire, schema and client code from `tools/sc_telemetry.py` (proven against LIVE 4.10), adding `from_blobs`, `to_json`/`from_json`, `hash`, `load_schema`, `GrpcStream.close()`. Move the fake server from the earlier probe test into `tests/tlm_helpers.py`.
- [ ] **Step 4: Create `tools/telemetry_probe.py`**: CLI with the probe's options (`--host --port --exe --every --out --seconds --schema`) plus `--raw PATH`: append each message as `struct.pack("<dI", time.time(), len(msg)) + msg`, and write the exe's telemetry blobs once to `PATH + ".desc"` as `struct.pack("<I", len(name)) + name + struct.pack("<I", len(blob)) + blob` per blob. Delete `tools/sc_telemetry.py`.
- [ ] **Step 5: Run** the tests. Expected: PASS. Run `python -I tools/telemetry_probe.py --schema | head -3`. Expected: `# D:\...\LIVE\Bin64\StarCitizen.exe` then `service ...`.
- [ ] **Step 6: Commit** `sc_telemetry.py tools/ tests/tlm_helpers.py tests/test_tlm_core.py csr.spec docs/telemetry docs/superpowers` — `feat: telemetry core module, probe with raw capture`.

### Task 2: Recording writer

**Files:**
- Modify: `sc_telemetry.py`
- Create: `tests/test_tlm_recording.py`

**Interfaces:**
- Consumes: `Schema.decode` output (nested dict).
- Produces:
  - `flatten(frame: dict) -> dict[str, object]`: dotted paths, lists of dicts as `name[i]`, lists of scalars kept as lists, `events` excluded. Floats `>= 1e38` become `None`.
  - `frame_events(frame: dict) -> list[str]`
  - `GROUPS: list[tuple[str, tuple[str, ...]]]` in spec order (first match wins): `wallet` (`actor.state.uec_balance`), `vitals` (`actor.vitals.`), `surroundings` (`actor.environment.`), `you` (`actor.state.`), `events` (`events.`), `settings` (`vehicle.state.flight.settings.`, `weapons.settings.`), `flight` (`vehicle.state.flight.instruments.`, `vehicle.state.flight.state.`), `ship` (`vehicle.`), `weapons` (`weapons.`), `turret` (`turret.`), `force_feedback` (`force_feedback.`), else `other`. Note `settings` is listed before `flight`/`ship`/`weapons` so its prefixes win.
  - `group_of(path: str) -> str`
  - `DEFAULT_GROUPS = [all group names except "force_feedback"]`
  - `SMOOTH_SUFFIXES = ("speed", "heading", "altitude", "radar_altitude", "vertical_speed_indicator", "atmospheric_density", "hydrogen_fuel", "quantum_fuel", "g_meter.current", "boost.output", "boost.reserves", "heart_rate", "body_temperature.current", "gravity", "pressure", "external_temperature", "ambient_radiation_rate", "stick_shaker", "gunfire")`, `is_smooth(path) -> bool` (path ends with one of them after a `.`, or force-feedback axis floats).
  - `DETAIL_SECONDS = {"full": 0.0, "normal": 0.5, "light": 5.0}`
  - `class RecordingWriter(path: str, header: dict, detail: str, groups: Iterable[str])`: `.add(t: float, flat: dict, events: list[str])`, `.close()`; `.bytes_written` property (compressed file size).

Line format (spec section 4): first line header JSON. Each later line `{"t": round(t,2), path: value, ..., "-": [removed paths], "ev": [events], "<path>^": max, "<path>_": min}`; omit keys that are empty. A line is written only if it has content.

- [ ] **Step 1: Write failing tests:**
  - `test_flatten_paths_and_sentinel`: `{"vehicle":{"state":{"flight":{"instruments":{"altitude":3.4028234663852886e+38,"speed":5.0}}}},"weapons":{"weapons":[{"name":"A"}]}}` → `{"vehicle.state.flight.instruments.altitude": None, "vehicle.state.flight.instruments.speed": 5.0, "weapons.weapons[0].name": "A"}`.
  - `test_group_of`: `actor.state.uec_balance`→`wallet`, `actor.state.locomotion`→`you`, `vehicle.state.flight.settings.ifcs_esp.active`→`settings`, `weapons.settings.staggered_fire`→`settings`, `vehicle.state.power.engines`→`ship`, `vehicle.state.flight.instruments.speed`→`flight`, `foo.bar`→`other`.
  - `test_changes_only`: add the same flat dict at t=0 and t=0.1 → one data line.
  - `test_removed_paths`: t=0 has `vehicle.state.name`, t=1 lacks it → line at t=1 has `"-": ["vehicle.state.name"]`.
  - `test_smooth_sampling_and_peaks_normal`: speed 0,50,300,40 at t=0.0,0.1,0.2,0.3, then 41 at t=0.6 → lines at t=0 (`speed:0`) and t=0.6 (`speed:41`, `speed^:300`, no `speed_` since 0 < min? min of 50,300,40,41 is 40 → `speed_:40`). Non-smooth field changed at t=0.1 is written at t=0.1.
  - `test_full_detail_writes_every_change`: same input with `detail="full"` → 5 lines, no `^`/`_` keys.
  - `test_groups_filter`: groups without `wallet` → no line ever contains `actor.state.uec_balance`, header `groups` lacks `wallet`.
  - `test_events_always_written`: event-only frame → `{"t":..., "ev":["ACTOR_DEAD"]}`.
- [ ] **Step 2: Run** `python -m unittest tests.test_tlm_recording -v`. Expected: FAIL (`flatten` missing).
- [ ] **Step 3: Implement** `flatten`, `frame_events`, `GROUPS`, `group_of`, `is_smooth`, `RecordingWriter`. Writer keeps `last_written: dict`, per smooth path `last_sample_t`, running `hi`/`lo` since last sample. Smooth path is due when `t - last_sample_t >= DETAIL_SECONDS[detail]`. Open with `gzip.open(path, "wt", encoding="utf-8")`. Flush with `fh.flush()` then the underlying `GzipFile.flush(zlib.Z_SYNC_FLUSH)` every 5 s of `t` and on `close()`.
- [ ] **Step 4: Run** tests. Expected: PASS.
- [ ] **Step 5: Commit** — `feat: telemetry recording writer, change-only with peaks`.

### Task 3: Recording reader

**Files:**
- Modify: `sc_telemetry.py`
- Modify: `tests/test_tlm_recording.py`

**Interfaces:**
- Produces:
  - `read_recording(path: str) -> tuple[dict, list[dict]]`: header and data rows. Truncated gzip or a half-written last line: return everything before it. Missing or empty file: `({}, [])`.
  - `iter_states(rows: list[dict]) -> Iterator[tuple[float, dict, list[str], dict]]`: yields `(t, state, events, peaks)` where `state` is the full flat state after applying the row (removals applied), `peaks` holds that row's `^`/`_` keys stripped of the marker as `{path: (lo, hi)}`.

- [ ] **Step 1: Write failing tests:**
  - `test_roundtrip_rebuilds_state`: write 4 frames, read back, last `state` equals the last flat frame for non-smooth paths.
  - `test_truncated_file_readable` (Review Focus 2): write 100 rows with a sync flush, copy the file's first `size - 7` bytes to a new path, `read_recording` returns the header and at least 90 rows without raising.
  - `test_half_line_dropped`: append `{"t": 9.9, "x"` raw to an uncompressed-equivalent stream via a fresh gzip member → that row is dropped, earlier rows kept.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Read with `gzip.open(path, "rt")` line by line inside `try/except (EOFError, OSError, zlib.error)`. Skip lines that fail `json.loads`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** — `feat: telemetry recording reader, tolerant of cut-off files`.

### Task 4: Per-recording summary

**Files:**
- Create: `sc_tlm_stats.py`
- Create: `tests/test_tlm_summary.py`

**Interfaces:**
- Consumes: `sc_telemetry.read_recording`, `iter_states`.
- Produces:
  - `SUMMARY_VERSION = 1`
  - `summarize(path: str) -> dict | None`: `{"file": basename, "channel", "started", "ended" (local ISO, started + last t), "build", "groups", "v": SUMMARY_VERSION, "sum": {...}}`. None for an empty recording.
  - `combine(entries: list[dict]) -> dict`: one `sum` for a session from several entries, rules below.

`sum` keys, each present only if its group was on (absent ≠ 0). Durations in seconds use the time to the next row, capped at 5 s per step:

| Group | Key | Rule | Combine |
|---|---|---|---|
| wallet | `uec_start`, `uec_end` | first / last non-null balance | first of first / last of last |
| wallet | `uec_earned`, `uec_spent` | sum of rises / sum of drops (positive number) | add |
| wallet | `uec_max_rise`, `uec_max_drop` | largest single step | max |
| wallet | `uec_series` | `[[iso_local, balance], ...]` on each change, thinned to ≤ 200 points (keep first, last, largest steps) | concatenate, thin to 400 |
| you | `t_on_foot`, `t_eva`, `t_seated`, `t_pilot`, `t_gunner`, `t_crew` | time in each `locomotion` / `crew_role` | add |
| events | `n_downed`, `n_dead`, `n_respawn`, `n_seat` | count of `ACTOR_DOWNED`, `ACTOR_DEAD`, `ACTOR_RESPAWN`, `ACTOR_ENTER_SEAT` | add |
| vitals | `t_no_air` | time with `oxygen.current == 0` | add |
| vitals | `min_health` | lowest `health.current` incl. `_` peaks | min |
| vitals | `t_starving`, `t_dehydrated` | time with hunger / thirst `current == 0` | add |
| vitals | `max_intox` | highest `intoxication_level` | max |
| surroundings | `max_radiation`, `max_ext_temp`, `min_ext_temp` | extremes incl. peaks | max / max / min |
| flight | `top_speed`, `max_g` | highest speed, `g_meter.current`, incl. `^` peaks | max |
| flight | `dist_m` | Σ speed × dt (dt capped 5 s) while a vehicle is present | add |
| flight | `takeoffs`, `landings` | `is_landed` true→false / false→true (missing = false) | add |
| flight | `gear_cycles` | `gear` transitions into `RETRACTED` | add |
| flight | `t_boost`, `n_boost_critical` | time `boost.engaged`, `boost.critical` false→true | add |
| flight | `h2_used`, `qt_used` | Σ drops in `hydrogen_fuel` / `quantum_fuel` (tank fractions) | add |
| flight | `t_flying`, `t_nav`, `t_scm` | seated in a vehicle and not landed. NAV = `annunciators.shld == "OFF"` and `annunciators.qdrv != "OFF"`, else SCM (needs `ship` group too, else omit nav/scm) | add |
| ship | `warn` | `{light: count}` transitions into `MINOR_ON`, `MAJOR_ON`, `MINOR_FLASHING`, `MAJOR_FLASHING` from any other state | add per key |
| ship | `t_shields_off_flying` | `power.shield_generators` false while flying | add |
| weapons | `cm_used` | `{DECOY: n, NOISE: n}` Σ drops in `amount` per type | add per key |
| weapons | `presets` | `{preset_name: seconds}` | add per key |
| settings | `t_ifcs` | `{setting: seconds active while flying}` for `ifcs_*` `.active` and bool settings | add per key |

- [ ] **Step 1: Write failing tests** with hand-built recordings (use `RecordingWriter` with `detail="full"`):
  - `test_wallet`: balances 1000, 1500, 1200, 1200, 2000 → `uec_start 1000, uec_end 2000, uec_earned 1300, uec_spent 300, uec_max_rise 800, uec_max_drop 300`.
  - `test_flight_takeoff_landing_distance`: landed 2 s, flying at 100 m/s for 10 s, landed → `takeoffs 1, landings 1, dist_m 1000 (±1), t_flying 10 (±0.1)`.
  - `test_peaks_count_for_records`: normal-detail recording where a 9.8 G spike falls between samples → `max_g == 9.8`.
  - `test_nav_inference`: shld OFF + qdrv NOMINAL for 4 s flying, then shld NOMINAL for 6 s → `t_nav 4, t_scm 6`.
  - `test_warn_transitions`: prox NOMINAL→MINOR_ON→MAJOR_ON→NOMINAL→MAJOR_ON → `warn == {"prox": 3}`.
  - `test_group_off_means_absent`: groups without `vitals` → no `t_no_air`, `min_health` keys.
  - `test_cut_off_recording_summarizes` (Review Focus 2): the truncated file from Task 3 summarizes without error.
  - `test_combine`: two entries → adds, max, min, first/last wallet per the table.
- [ ] **Step 2: Run** `python -m unittest tests.test_tlm_summary -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `summarize` (one pass over `iter_states`) and `combine`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** — `feat: telemetry per-recording summaries`.

### Task 5: Index, retention, matching, career folding

**Files:**
- Modify: `sc_tlm_stats.py`
- Create: `tests/test_tlm_index.py`

**Interfaces:**
- Produces:
  - `tlm_dir(app_dir: str) -> str` (`<app_dir>/telemetry`, created)
  - `recording_files(root: str) -> list[str]` (all `*/*.jsonl.gz`)
  - `load_index(root) -> dict[str, dict]`, `save_index(root, idx)` (`index.json`, keyed by file basename, atomic write via temp + `os.replace`)
  - `refresh_index(root, skip: set[str] = frozenset()) -> dict`: summarize files missing from the index or with `v < SUMMARY_VERSION`. Entries whose file is gone are kept. `skip` holds the file being recorded.
  - `apply_retention(root, keep_days: int | None, now: datetime, skip=frozenset()) -> int`: delete recordings whose `started` is older. Index entries stay.
  - `disk_usage(root) -> int`, `delete_all_recordings(root, skip=frozenset()) -> int`
  - `match(sessions: list[dict], idx: dict) -> tuple[dict[int, list[dict]], list[dict]]`: maps `id(session)` to its entries, plus unmatched entries. Rule: same `channel`; `session.start <= started <= session.start + dur + 120 s`. A session whose end is within 60 s of now and is its channel's latest session counts as still open (any later `started` matches).
  - `attach(sessions: list[dict], idx: dict) -> tuple[list[dict], list[dict]]`: shallow copies of matched sessions with `"_tlm": combine(entries)`, others unchanged; plus unmatched. Never mutates the inputs (the archive is saved from them).
  - `new_acc() -> dict`, `fold_tlm(acc: dict, s_tlm: dict | None)`, `finalize_tlm(acc: dict, sessions: int) -> dict`: per key, career value by the combine rule plus `n` (sessions that had it). Output: `{"n_sessions": sessions, "n_tlm": sessions with any summary, "stats": {key: {"v": value, "n": n}}, "wallet_series": [...]}`.

- [ ] **Step 1: Write failing tests:**
  - `test_refresh_and_version_bump`: two recordings → 2 entries. Bump `SUMMARY_VERSION` via patch → both recomputed. Delete one file, bump again → its entry keeps the old `v`.
  - `test_retention_keeps_summaries`: recordings 40 and 10 days old, `keep_days=30` → 1 file deleted, index still has 2 entries.
  - `test_match_local_time` (Review Focus 4): session `start=datetime(2026,11,24,19,50,16)` (local, naive), `dur=3600`; header `started="2026-11-24T19:51:40"` → matched. Header `started_utc` alone is never used for matching.
  - `test_match_restart_two_segments`: two recordings inside one session → one session, combined `dist_m` is the sum.
  - `test_match_csr_opened_late`: recording starts 50 min after session start → matched.
  - `test_attach_does_not_mutate`: input session dicts have no `_tlm` after `attach`.
  - `test_coverage_per_stat`: session A has `max_g` (v2 summary), session B only `uec_end` (v1, file expired) → `stats.max_g.n == 1`, `stats.uec_end.n == 2`, `n_tlm == 2`.
- [ ] **Step 2: Run** `python -m unittest tests.test_tlm_index -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** — `feat: telemetry index, retention, session matching, career totals`.

### Task 6: Recorder thread

**Files:**
- Modify: `sc_telemetry.py`
- Create: `tests/test_tlm_recorder.py`

**Interfaces:**
- Consumes: `GrpcStream`, `load_schema`, `RecordingWriter`, `flatten`, `frame_events`.
- Produces: `class Recorder(root: str, get_settings: Callable[[], dict], game_running: Callable[[], bool | None], pick_channel: Callable[[], tuple[str, str] | None], on_close: Callable[[str], None], host=HOST, port=PORT)`:
  - `start()`, `stop()` (daemon thread).
  - `status() -> dict`: `{"state": "idle"|"waiting"|"live"|"off", "reason", "channel", "file", "bytes", "since", "frames"}`. `idle` = game not running. `waiting` = game running, port not answering yet. `off` = refused for 180 s straight while the game runs (`reason: "port"`), or no schema from the exe or cache (`reason: "schema"`, no connect attempted). `live` = frames arriving.
  - `record` is re-read every second while live: switching it off closes the open file at once, switching it on opens a new one. `detail` and `groups` changes apply from the next file.
  - `live() -> dict`: `{"state", "channel", "t" (epoch of last frame), "snapshot" (latest decoded frame, nested, events removed), "events": [{"t", "type"}] (last 50)}`.
  - `connected` property: True while a stream is open.
  - `get_settings()` returns `{"record": bool, "detail": str, "groups": list[str]}`.
  - `pick_channel()` returns `(label, channel_dir)`. Schema from `<channel_dir>/Bin64/StarCitizen.exe` via `load_schema(..., cache_dir=root, channel=label)`. Build from `<channel_dir>/build_manifest.id` `Data.Version`.
  - New file per connected stretch: `<root>/<label>/<YYYYMMDD-HHMMSS>.jsonl.gz` (local time). Header: `{"v":1, "csr": <passed in via get_settings()["csr"]>, "channel", "build", "started" (local ISO, seconds), "started_utc", "detail", "groups", "schema": schema.hash}`.
  - `record=False`: live view still works, no file.
  - Calls `on_close(path)` after each file closes.
  - Retry connect every 5 s while the game runs. Stream error: close file, retry. Game gone: close file, go `idle`. Never raises out of the thread.

- [ ] **Step 1: Write failing tests** using `FakeGrpcServer` and a fake clock-free setup (short gaps):
  - `test_records_and_closes_on_game_exit`: game_running True → server streams 20 frames → game_running False → one file exists, `on_close` called once with its path, `read_recording` has a header with `channel == "LIVE"`.
  - `test_reconnect_starts_new_file`: server drops after 5 frames, a second server on the same port streams 5 more → two files, both reported to `on_close`.
  - `test_record_off_still_live`: `record=False` → no files, `live()["snapshot"]` has `actor`.
  - `test_off_after_refusals` (Review Focus 3): no server, game running, patch the refusal window to 0.3 s and retry interval to 0.05 s → `status()["state"] == "off"` within 1 s, connect attempts ≤ 25, nothing written to stdout.
  - `test_waiting_then_live`: server starts 0.3 s after the game → `waiting` then `live`.
  - `test_record_toggle_applies_now`: live with `record=True`, flip to False → file closed and `on_close` called within 1.5 s while frames keep arriving.
  - `test_no_schema`: `pick_channel` points at a dir with no exe and no cache → `state "off"`, `reason "schema"`, zero connect attempts.
- [ ] **Step 2: Run** `python -m unittest tests.test_tlm_recorder -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Make the 5 s retry and 180 s refusal window module constants (`RETRY_S`, `OFF_AFTER_S`) so tests can patch them. Guard the whole loop body with `try/except Exception` and a 5 s back-off.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** — `feat: telemetry recorder thread`.

### Task 7: Wire the recorder into CSR's server

**Files:**
- Modify: `sc_stats.py` (config helpers near `save_config` ~line 473; `_accept_folder` ~line 11849; `serve()` ~line 11699 incl. `watchdog()` ~11751, `do_GET` ~11810, `do_POST` ~11869; `/api/status` payload)
- Create: `tests/test_tlm_wiring.py`

**Interfaces:**
- Consumes: `Recorder`, `sc_tlm_stats.refresh_index/apply_retention/disk_usage/delete_all_recordings/tlm_dir`.
- Produces:
  - `update_config(**kv) -> dict`: load, merge top-level keys, save. `_accept_folder` uses it instead of `save_config({"live_path": ...})` (Review Focus 1).
  - `tlm_settings() -> dict`: config `"tlm"` merged over defaults `{"record": True, "detail": "normal", "keep_days": None, "groups": DEFAULT_GROUPS, "phone": False, "phone_key": None}`.
  - `tlm_channel_now() -> tuple[str, str] | None`: the `_CHANNEL_DIRS` entry whose `Game.log` mtime is newest.
  - `user_cfg_state(channel_dir) -> dict`: `{"line": bool}` (exact key `grpc.client.server.enabled` with value `true` or `1`, case-insensitive), `{"applied": bool}` (`GRPC_CLIENT_SERVER_ENABLED` or `grpc-client-server-enabled` named in the first 300 lines of `Game.log`).
  - `enable_user_cfg(channel_dir) -> tuple[bool, str]`: copy `user.cfg` to `user.cfg.csr-bak` if it exists, append `grpc.client.server.enabled = true` with CRLF, keep every other line. No-op if the line is already there. Creates `user.cfg` if missing. The game only reads the file at launch, so this is allowed while it runs.
  - Routes:
    - `GET /api/telemetry/live` → `recorder.live()`
    - `GET /api/telemetry/status` → `{"settings", "recorder": recorder.status(), "channels": {label: user_cfg_state}, "disk": int, "unmatched": [entries], "phone": {"on", "url"}}`
    - `POST /api/telemetry/settings` body subset of `record, detail, keep_days, groups, phone` → validates (`detail` in DETAIL_SECONDS, `keep_days` in `None, 365, 90, 30`, `groups` ⊆ group names) → saves → returns status
    - `POST /api/telemetry/enable` `{"channel"}` → `enable_user_cfg`
    - `POST /api/telemetry/delete-all` → `delete_all_recordings` (skip the open file)
  - `/api/status` gains `"tlm": recorder.status()`.
  - On recorder `on_close`: `refresh_index`, `apply_retention`, then `do_generate(quick=True)` in a background thread. At startup: same, before the first build.
  - Watchdog: each 30 s tick, if `recorder.connected`, set `state["seen"] = time.time()`.

- [ ] **Step 1: Write failing tests:**
  - `test_update_config_preserves` (Review Focus 1): config has `tlm.phone_key="abc"`; call `_accept_folder` path logic (factor it into `remember_folder(path)` and test that) → `tlm.phone_key` still `"abc"`.
  - `test_user_cfg_state`: files with `grpc-client-server-enabled = 1` → `line False`; `grpc.client.server.enabled = true` → `line True`.
  - `test_enable_user_cfg`: existing `g_language = english` → file now has both lines with CRLF, `.csr-bak` equals the original.
  - `test_settings_validation`: `detail="ultra"` rejected, `keep_days=7` rejected, `groups=["wallet","nope"]` rejected.
  - `test_tlm_channel_now`: two channel dirs, newer `Game.log` mtime wins.
- [ ] **Step 2: Run** `python -m unittest tests.test_tlm_wiring -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the helpers and routes. Start the recorder next to `start_watcher()`. Pass `get_settings=lambda: {**tlm_settings(), "csr": CSR_VERSION}`, `game_running=_sc_process_running`, `pick_channel=tlm_channel_now`.
- [ ] **Step 4: Run** the new tests and the full suite `python -m unittest discover tests -v`. Expected: all PASS.
- [ ] **Step 5: Commit** — `feat: telemetry recorder runs inside CSR, settings and status API`.

### Task 8: Standalone live page

**Files:**
- Modify: `sc_stats.py` (new `LIVE_HTML()` beside `BOOT_HTML`/`ONBOARD_HTML`; route `GET /live`; sidebar link in the nav block ~line 8130)

**Interfaces:**
- Consumes: `GET /api/telemetry/live`.
- Produces: `LIVE_HTML() -> str`, a self-contained page using the dashboard's CSS tokens (copy the `:root` token block, don't link the dashboard). Element ids: `#tlmState`, `#tlmShip`, `#tlmFlight`, `#tlmWarn`, `#tlmPower`, `#tlmVitals`, `#tlmWallet`, `#tlmEvents`. The page polls every 250 ms while `document.visibilityState === "visible"`, every 2 s otherwise. It reads the API path from `location` so it works on both listeners.

Content (spec section 5): ship (`class_name`, seat role), speed, G (current / limit), hydrogen and quantum fuel bars, boost reserves with a `CRITICAL` flag, warning lights not `NOMINAL`/`OFF`, power switches, health / oxygen / hunger / thirst / heart rate / body temperature, wallet, last 10 events. Altitude shown only when not null. Narrow screens (< 600 px): one column, wallet and warnings first.

States shown when not live (copy per voice rules):
- `idle`: "Star Citizen isn't running."
- `waiting`: "Game is starting. Waiting for telemetry."
- `off`: "Telemetry is off in the game. Turn it on in CSR's Telemetry settings."

- [ ] **Step 1:** Add `LIVE_HTML()` and the `/live` route, and a nav entry `<a href="/live" target="_blank" id="navLiveView"><span>📡</span> Live view</a>` after Stability.
- [ ] **Step 2: Verify in the browser pane** with `FakeGrpcServer` replaying synthetic frames on port 2942 (or the real game): open `http://127.0.0.1:7878/live`, `read_page` shows speed and wallet values updating. Resize to 375×812: single column, no horizontal scroll. Screenshot both.
- [ ] **Step 3: Commit** — `feat: live telemetry page`.

### Task 9: Phone listener and QR code

**Files:**
- Create: `sc_qr.py`, `tests/test_qr.py`
- Modify: `sc_stats.py` (`PhoneServer` start/stop inside `serve()`)
- Create: `tests/test_tlm_phone.py`

**Interfaces:**
- Produces:
  - `sc_qr.qr_svg(text: str, scale: int = 4) -> str`: byte mode, ECC level M, smallest version 1–10 that fits, best mask by the standard penalty rules, 4-module quiet zone. Also `rs_ecc(data: list[int], n: int) -> list[int]` and `format_bits(ecc: str, mask: int) -> str` for tests.
  - In `sc_stats.py`: `lan_ip() -> str` (UDP `connect(("192.0.2.1", 9))`, read `getsockname()`, no packet sent; fallback `127.0.0.1`), `phone_url() -> str | None`, `start_phone_server(port_range=range(7879, 7890))`, `stop_phone_server()`.
  - Phone handler routes: `/live` (`LIVE_HTML()`), `/api/telemetry/live`. Everything else 404. Key check: `?k=<key>` or cookie `csrk=<key>` (set on the first valid `/live` visit, `HttpOnly; SameSite=Strict`). Missing or wrong: 403. Compare with `hmac.compare_digest`. Each valid `/api/telemetry/live` poll sets `state["seen"]`.
  - `POST /api/telemetry/new-link` (main server only): new `secrets.token_urlsafe(16)`, saved via `update_config`.
  - Turning `phone` on in `/api/telemetry/settings` starts the listener and creates a key if none. Off stops it.

- [ ] **Step 1: Write failing tests:**
  - `test_rs_hello_world` (ISO/IEC 18004 worked example, version 1-M): `rs_ecc([32,91,11,120,209,114,220,77,67,64,236,17,236,17,236,17], 10) == [196,35,39,119,235,215,231,226,93,23]`.
  - `test_format_bits`: `format_bits("M", 0) == "101010000010010"`, `format_bits("L", 4) == "110011000101111"`.
  - `test_svg_size`: `qr_svg("http://192.168.100.100:7879/live?k=" + "x"*22)` is an `<svg` with a viewBox width `(4*v+17+8)*scale` for some version v in 1..10.
  - `test_phone_scope` (Review Focus 5): start the phone server on a free port with key `"k1"`. `GET /live?k=k1` → 200 and `Set-Cookie`. `GET /api/telemetry/live` with that cookie → 200. `GET /api/shutdown?k=k1`, `/`, `/api/status`, `/api/export` → 404. `GET /live?k=bad` → 403. `GET /api/telemetry/live` without key → 403.
- [ ] **Step 2: Run** `python -m unittest tests.test_qr tests.test_tlm_phone -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** — `feat: phone access to the live view, private link and QR code`.

### Task 10: Telemetry settings card

**Files:**
- Modify: `sc_stats.py` (card markup and JS in the Backup & transfer modal area ~line 8245, or a new `⚙` modal beside `#dataBtn` if that modal is export-only; follow whichever the existing modal code supports with less change)

**Interfaces:**
- Consumes: `/api/telemetry/status`, `/api/telemetry/settings`, `/api/telemetry/enable`, `/api/telemetry/delete-all`, `/api/telemetry/new-link`, `sc_qr.qr_svg` (rendered server-side into the status payload as `phone.qr_svg`).
- Produces: a "Telemetry" card, ids `#tlmCard`, `#tlmRecord`, `#tlmDetail`, `#tlmKeep`, `#tlmGroups`, `#tlmDisk`, `#tlmDeleteAll`, `#tlmPhone`, `#tlmQr`, `#tlmNewLink`, `#tlmEnable-<channel>`.

Card content, copy per voice rules:
- Status line per channel: "On in the game" / "Line added. Takes effect next launch." / "Off in the game" + button "Turn on" (two-step confirm like `.setact`, uses `confirmBtn`).
- Recorder: "Recording · 2.1 MB" / "Waiting for the game" / "Not running" / "Telemetry off in the game".
- Record telemetry: toggle. Detail: Full / Normal / Light with the per-hour sizes (1.7 MB, 0.4 MB, 0.1 MB). Keep recordings: Forever / 1 year / 3 months / 1 month. Groups: checkboxes with names Wallet, Vitals, Surroundings, You, Events, Flight, Ship systems, Weapons, Settings, Turret, Force feedback, Other.
- "Recordings use 412 MB." + "Delete all recordings" (confirm: "Deletes every recording. Career stats stay.").
- Unmatched recordings: count and file names, if any.
- Phone access toggle. On: QR code, the link as text, "New link" button, and "Windows will ask to allow CSR on your network. Allow it on private networks."
- Footnote: "Changes apply to the next recording."
- Status light (spec section 12): the existing heartbeat tooltip / watch panel gets one line from `/api/status` `tlm`: "Telemetry: recording" / "waiting for the game" / "off in the game" / "no schema". Hidden while `idle`.

- [ ] **Step 1:** Build the card, its JS and the status-light line.
- [ ] **Step 2: Verify in the browser pane:** change Detail to Light, reload, it stays Light (`csr_config.json` has `"detail": "light"`). Toggle phone on: QR and link appear. Turn it off: `curl http://<lan-ip>:7879/live` fails to connect. Screenshot the card.
- [ ] **Step 3: Commit** — `feat: telemetry settings card`.

### Task 11: Career stat cards

**Files:**
- Modify: `sc_stats.py`: `blank_patch()` (~4106) adds `"tlm": sc_tlm_stats.new_acc()`; `fold()` (~4180) calls `sc_tlm_stats.fold_tlm(agg["tlm"], s.get("_tlm"))`; `finalize()` (~4876) sets `agg["tlm"] = sc_tlm_stats.finalize_tlm(agg["tlm"], agg["sessions"])`; `build_from_sessions()` (~5491) runs `sessions, unmatched = sc_tlm_stats.attach(sessions, load_index(tlm_dir(app_dir())))` first and stores `unmatched` for `/api/telemetry/status`; JS section renderers `secEconomy`, `secFlight`, `secCombat`, `secActivity`.
- Modify: `tests/test_tlm_index.py` (fold/finalize through `build_account` with two sessions)

**Interfaces:**
- Consumes: `finalize_tlm` output shape from Task 5, per scope as `v.tlm` in JS.
- Produces: JS helper `tlmCard(title, sub, tiles, v)` that renders nothing when `v.tlm.n_tlm === 0`, and a coverage line "From N of M sessions with telemetry" (N = largest `n` among the card's stats). A tile whose stat has a smaller `n` shows "(N sessions)" in its label. Absent stats render no tile.

Cards (spec section 9), each a `group(...)` on its page using `miniTile` and existing chart helpers:
- Economy, "Wallet": balance now (`uec_end`), earned, spent, net, biggest rise, biggest drop, balance-over-time line from `wallet_series`.
- Flight & Travel, "Flight records": top speed (m/s), max G, distance flown (km), take-offs, landings, gear cycles, boost time, times boost ran out, hydrogen used (tanks), quantum fuel used (tanks), NAV vs SCM split bar.
- Flight & Travel, "How you fly": share of flight time per IFCS setting from `t_ifcs` (bars).
- Combat, "Warnings & countermeasures": warnings per light (missile, hit, proximity first), decoys and noise used.
- Activity, "On foot & in seat": time on foot / EVA / seated split bar, pilot / gunner / crew split bar.
- Activity, "Survival": times downed, deaths, time out of air, lowest health, time starving, time dehydrated, peak radiation, peak intoxication, hottest / coldest surroundings.
- Flight & Travel, "Weapons": presets used by time (from `presets`), next to "How you fly".

- [ ] **Step 1: Write failing test** `test_build_account_folds_tlm`: two sessions with `_tlm` sums (`max_g` 5 and 9, `dist_m` 100 and 200) → career `tlm.stats.max_g == {"v": 9, "n": 2}`, `dist_m.v == 300`, and the archive input dicts are unchanged.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the Python folding, then the JS cards.
- [ ] **Step 4: Run** the full suite. Expected: PASS. **Verify in the browser pane** with recordings from Task 12 or a synthetic recording copied into `CSR-data/telemetry/LIVE/`: Economy shows the Wallet card with a coverage line. A patch filter with no recorded sessions shows no telemetry cards. Screenshot.
- [ ] **Step 5: Commit** — `feat: career stats from telemetry`.

### Task 12: Backup & transfer

**Files:**
- Modify: `sc_stats.py` (`/api/export` ~line 11833, `/api/import` handler, Backup & transfer modal ~8245)
- Create: `tests/test_tlm_backup.py`

**Interfaces:**
- Produces:
  - `export_payload(include_recordings: bool) -> tuple[bytes, str, str]` (body, content type, filename). JSON: the archive object plus `"telemetry": list(index.values())`. With recordings: zip with `CSR-archive-<date>.json` and `telemetry/<channel>/<file>`. `GET /api/export?tlm=1` returns the zip.
  - `import_telemetry(entries: list[dict], files: dict[str, bytes]) -> int`: adds index entries and recording files not already present (same basename). Returns count added.
  - `/api/import` accepts the existing JSON body (with or without `"telemetry"`). New `POST /api/import-zip` takes the raw zip bytes (read `Content-Length` bytes, no utf-8 decode).
  - Modal: checkbox "Include telemetry recordings (412 MB)".
- [ ] **Step 1: Write failing tests:** `test_json_export_has_summaries`, `test_zip_roundtrip` (export zip from dir A, import into empty dir B → same index keys and files), `test_old_export_imports` (JSON without `"telemetry"` imports fine), `test_import_skips_existing`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** — `feat: telemetry in backup and transfer`.

### Task 13: Real capture, real-game check, release notes

Needs the player in game.

**Files:**
- Create: `tests/fixtures/telemetry/live-4.10.bin`, `tests/fixtures/telemetry/live-4.10.bin.desc`
- Create: `tests/test_tlm_fixture.py`
- Modify: `CHANGELOG.md` (new `## [Unreleased]` section), `README.md` (Telemetry section under "What's in the dashboard" and a line in Get started)
- Modify: `docs/telemetry/NOTES.md` (anything new seen)

- [ ] **Step 1: Capture.** The player runs `python tools/telemetry_probe.py --raw tests/fixtures/telemetry/live-4.10.bin --seconds 240` while: on foot, sitting in a ship, take-off, boost, NAV mode, landing, leaving the seat. Keep it under 2 MB. Check it holds no other player's name (only the user's own data is in the stream).
- [ ] **Step 2: Write `tests/test_tlm_fixture.py`:** load the schema from the `.desc` file, replay the capture through `FakeGrpcServer` → `Recorder` → `summarize`. Assert `takeoffs >= 1`, `landings >= 1`, `top_speed > 0`, `uec_start == uec_end` (no trades during capture), `n_seat >= 1`.
- [ ] **Step 3: Run** the full suite. Expected: PASS.
- [ ] **Step 4: Real game, end to end.** Start CSR from source (`python sc_stats.py --serve`), play 10+ minutes on LIVE with a flight. Check: status shows Recording, `/live` updates on the PC and on a phone via the QR code, closing the game closes the file, after the refresh the Wallet and Flight records cards show this session ("From 1 of N sessions"). Close the dashboard tab during play with the console hidden: CSR is still running 20 minutes later and the recording is intact.
- [ ] **Step 5: Release notes.** CHANGELOG `### Added`: telemetry recording, live view, phone access, career stats, settings card. Voice rules apply (example tone: "**Telemetry.** CSR records the game's telemetry while you play. Turn it on in Telemetry settings, it adds one line to user.cfg."). README: what's recorded, how to turn it on, disk use per hour, the phone link is private.
- [ ] **Step 6: Commit** — `feat: telemetry fixture test, release notes`.
