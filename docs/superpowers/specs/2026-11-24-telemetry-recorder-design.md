# Telemetry recorder: design

Date: 2026-11-24. Status: for approval. Approach A and plain UI chosen by the user.

## Why

Star Citizen 4.10 LIVE ships a local telemetry stream: a gRPC server inside the game
client (`sc.game.telemetry.v1.GameTelemetryService`, `127.0.0.1:2942`, cleartext HTTP/2).
It sends about 15 snapshots a second of data the Game.log never records: the aUEC
wallet balance, vitals, flight instruments, ship power, warning lights, weapons and
countermeasures, flight settings. Field-by-field list and observed behaviour:
`docs/telemetry/NOTES.md`, schemas in `docs/telemetry/schema-*.txt`.

CSR is a career dashboard built from logs. The stream only exists while the game runs,
so CSR has to record it during play to turn it into career data. The user wants:

1. CSR to capture all of it, especially what the logs lack.
2. A live view while playing, on the same PC and on a phone.
3. The recordings turned into career stats.
4. Players able to choose what gets recorded, to save disk space.

## Goals

- Record every telemetry session automatically while CSR is open and the game runs.
- Keep the recordings, so a stat added in a later CSR version can be computed from
  sessions recorded today (same principle as re-reading old logs).
- Career stats from recordings on the dashboard, with honest coverage ("12 of 40 sessions recorded").
- A live view on the PC page and on a phone, read-only.
- Recording settings: detail level, retention, groups on/off, disk usage shown.

## Non-goals

- Ship control (`TriggerGameAction`). PTU 5.0 only. Waits for LIVE, then gets its own design.
- Recording while CSR is closed. A "start with Windows" option may come later, separately.
- Replacing any log-derived stat. Logs stay the source for location, kills, missions, cargo, inventory.
- Charts or replay of a single session. Recordings make it possible later. Not in this build.

## Anti-goals (failure even if it works)

- The phone listener exposing anything beyond the live view (career pages, shutdown, restart, import).
- CSR touching the game process. Telemetry is read from CIG's own local port only. No process
  handles, no memory reads (see the `_sc_process_running` comment in `sc_stats.py`).
- A recorder fault taking the dashboard down, or a CSR crash leaving an unreadable recording.
- Unbounded disk growth nobody can see.
- Stats that show 0 for sessions that were never recorded.

## Constraints

- Stdlib only (CSR rule). gRPC and protobuf are hand-rolled. `tools/sc_telemetry.py`
  already proves both against the live game.
- Ships as one portable `CSR.exe` (PyInstaller, `csr.spec`). Data lives in `app_dir()` (`CSR-data`).
- Front-end copy follows the user's voice rules (short, flat, dev-written; see global CLAUDE.md).
- PTU/TP-only fields are recorded (schema-driven), but no stats or UI are built on them until they reach LIVE.
- The schema is read from the channel's `StarCitizen.exe`, never hard-coded, so patches that
  add fields need no code change to be recorded.

## Approaches considered

**A. Recorder inside CSR's server (chosen).** A background thread in `CSR.exe --serve`
next to the existing log watcher. Reuses game detection, the web server, `CSR-data` and
backups. One exe. Records only while CSR is open, and runs inside the dashboard's process
(guarded the way the watcher is).

**B. Separate recorder program.** Runs on its own (e.g. at Windows login), CSR reads its
files. Records even with the dashboard closed. Costs a second program to build, ship and
version-match, its own server or a relay for the live view, and a startup entry, which CSR
has never had. Rejected: the one gain can be had later inside A.

**C. Extend `sc_analyzer.py`.** Rejected: separate older tool, not tied to the career archive, not what players run.

The user picked A.

## Design

### 1. Module layout

- New root module `sc_telemetry.py`: schema reader, gRPC/HTTP/2 client, recorder,
  recording reader, per-session summary, retention. No dependency on `sc_stats.py`.
- `sc_stats.py` gets the wiring only: start/stop, settings card, API routes, pages, stats folding.
- `tools/sc_telemetry.py` becomes `tools/telemetry_probe.py`, a thin CLI over the module
  (keeps `--schema`, `--out`, `--every`).
- `csr.spec`: add `sc_telemetry` to `hiddenimports`.

### 2. Schema

- Read the telemetry `FileDescriptorProto`s from the active channel's `Bin64\StarCitizen.exe`
  (code exists in the probe). The scan of a ~180 MB exe takes a few seconds, so cache the
  parsed schema in `CSR-data/telemetry/schema-<channel>.json`, keyed by exe size + mtime.
- If the exe can't be read, fall back to the last cached schema. No schema at all: don't record, say so on the status.
- Each recording stores the schema hash it was decoded with.

### 3. Lifecycle

- Driven from the watcher loop's existing `_sc_process_running()` check.
- Game running: try to connect to `127.0.0.1:2942` every 5 s until it answers (the port
  opens partway through boot). Connected: record. Stream drops while the game still runs:
  close the current segment, reconnect, keep the same recording.
- Game gone: close the recording.
- Channel: decided when the port first answers, which is a minute or more into boot. By then
  the running channel's `Game.log` has been written for that whole time. Take the channel
  whose `Game.log` was written most recently. Its exe gives the schema, its `build_manifest.id`
  the build. There is no "unknown" channel.
- A recording file is one connected stretch. Normally that is the whole game run. A CSR
  restart, a CSR crash or a stream drop mid-run starts a new file. Several files can belong to
  one session (section 8).
- Port closed for the whole run: no recording, and the status says telemetry is off in the game.

### 3a. Idle auto-exit

`serve()`'s `watchdog()` quits CSR when the console is hidden and no page has polled for
15 minutes. While the recorder is connected to the game, that timer is held. The 15 minutes
start counting once the game closes. Polls from the phone listener count as a page being open.
Reason: the auto-exit exists so CSR never lingers invisibly forever. A running game is a
bounded reason to stay, and quitting mid-session would cut the recording.

### 4. Recording format

File: `CSR-data/telemetry/<channel>/<YYYYMMDD-HHMMSS>.jsonl.gz` (local start time).

- Line 1, header: `{"v":1, "csr":"<version>", "channel", "build" (from build_manifest.id),
  "started" (ISO local), "started_utc", "detail", "groups":[...], "schema":"<hash>"}`.
- Then one line per moment with changes: `{"t": <seconds since start, 2 dp>, "<dotted.path>": value, ...}`.
  Paths are flattened from the decoded frame (`vehicle.state.flight.instruments.speed`,
  `weapons.weapons[3].name`). A path that disappears is written as `"-": ["path", ...]`.
- Events (`events.events[*].type`) are written on the frame they arrive, as `"ev": ["ACTOR_DEAD"]`.
- Proto3 defaults: a missing field means false/0/absent. The recorder treats a missing path
  as removed, so the reader rebuilds full snapshots.
- `altitude` and any float >= 1e38 are stored as `null`.
- Raw values are kept as sent (entity ids in names included). Cleaning happens in stats.

**Detail levels.** "Smooth" fields are floats that change most frames (speed, heading,
altitude, radar altitude, vertical speed, G, boost output/reserves, fuel, heart rate,
temperatures, gravity, pressure, radiation, density). Everything else is written on every change.

| Level | Smooth fields | Measured / estimated size |
|---|---|---|
| Full | every change | ~1.7 MB/h gzipped |
| Normal (default) | at most 2/s | ~0.4 MB/h |
| Light | at most 1 per 5 s | ~0.1 MB/h (estimate) |

Below Full, every smooth field also writes its min and max since its last sample, as
`"<path>^": max` and `"<path>_": min`, but only when they differ from the sampled value. Records
(top speed, max G, lowest oxygen) stay exact at every level.

**Groups** (on/off, default all on except force feedback). A path belongs to the first match:

| Group | Paths |
|---|---|
| Wallet | `actor.state.uec_balance` |
| Vitals | `actor.vitals.*` |
| Surroundings | `actor.environment.*` |
| You | rest of `actor.state.*` |
| Events | `events.*` |
| Flight | `vehicle.state.flight.instruments.*`, `vehicle.state.flight.state.*` |
| Ship systems | rest of `vehicle.*` (identity, power, warning lights, doors, lights, and 5.0 shields/health/master mode) |
| Weapons | `weapons.*` except `weapons.settings` |
| Settings | `vehicle.state.flight.settings.*`, `weapons.settings.*` |
| Turret | `turret.*` |
| Force feedback | `force_feedback.*` |
| Other | anything a future patch adds outside these (recorded, on by default) |

Groups off are never written. The header lists the groups that were on, so stats can tell
"not recorded" from "zero".

**Crash safety.** Write through `gzip` with a sync flush every 5 s, and on close. The reader
accepts a truncated tail and keeps everything up to the last full line.

### 5. Live view

- The recorder keeps the latest decoded snapshot and the last 50 events in memory,
  whatever the recording settings are (groups and detail only affect disk).
- `GET /api/telemetry/live` returns `{state: "off"|"waiting"|"live", snapshot, events, channel, recording}`.
  The page polls it about 4 times a second while visible.
- The live view is a standalone page (`/live`), not a tab inside the generated `CSR.html`. The
  phone listener can serve it on its own, and the dashboard links to it from the sidebar.
  Phone layout of the same page at narrow widths. It reuses the dashboard's CSS tokens.
  Content: ship and seat, speed/G/fuel/boost, warning lights, power, vitals, wallet, recent events.
- UI in this build is plain: built from the dashboard's existing cards, tokens and type scale,
  no new visual language. A Hybrid design pass (design session writes the spec, Build master
  implements) restyles the live page, phone layout, stat cards and settings card afterwards,
  using real recordings. Element ids and class names should stay stable for that pass.
- Shown when telemetry isn't live: what's missing (game not running / telemetry off in the
  game / waiting for the port).

### 6. Phone access

- Setting "Phone access", off by default.
- On: CSR opens a second `ThreadingHTTPServer` on `0.0.0.0:<7879>` that serves only the live
  page, its static assets and `/api/telemetry/live`. Every other path returns 404. The main
  server stays on `127.0.0.1:7878`.
- Access key: 128-bit random token stored in `csr_config.json`. The link is
  `http://<LAN IP>:7879/live?k=<token>`. First valid visit sets a cookie. Wrong or missing key: 403.
  A "New link" button rotates the token.
- The settings card shows the link as a QR code (generated locally, no network) and as text.
- Windows shows its firewall prompt the first time the listener opens. The card says to allow it on private networks.
- Off: the listener is closed.

### 7. Turning telemetry on in the game

- Per channel, CSR checks `<channel>\user.cfg` for `grpc.client.server.enabled = true`.
  The hyphen form doesn't work (verified on LIVE 4.10, see NOTES.md).
- Missing: the recording card says so and offers a button that adds the line, keeping the
  rest of the file. Takes effect on the next game launch. This is the only write CSR makes
  into the game folder, and only on that click.
- The Game.log startup block confirms it (`'GRPC_CLIENT_SERVER_ENABLED' ... set via 'User CFG '`).
  The watcher can read that to show "on" vs "set but not applied yet".

### 8. Matching recordings to sessions

- A recording belongs to the session on the same channel whose time span contains the
  recording's start: session start <= recording start <= session end (+2 min slack for the
  game's drawn-out shutdown). A session still being written has no end yet and takes any
  recording that started after its start.
- One session can have several recordings (restarts, drops). Their summaries are combined
  per session: totals add up, peaks take the max, wallet start/end come from the first and last.
- Matching is done when the dashboard is built, not stored in the archive (section 9 says why).
- Unmatched recordings are kept and listed on the recording card. They count toward disk usage, not toward stats.

### 9. Summaries and stats

- When a recording closes (and at startup for any recording without a current summary),
  compute its summary and store it in a sidecar index, `CSR-data/telemetry/index.json`,
  keyed by recording file name: `{channel, started, ended, build, groups, v, sum}`.
  Not in the archive's session entries: `scan_log` rebuilds those from scratch, `merge_sessions`
  swaps in the live log's copy, and a `PARSER_VERSION` bump re-reads every log. Any of the
  three would drop fields the parser doesn't produce, and a summary whose recording has
  expired could not be rebuilt.
- The dashboard build joins index entries to sessions with the section 8 rule.
- `v` is the summary version. A newer CSR with a higher version recomputes every entry whose
  recording is still on disk, the same way the parser version re-reads logs. Entries whose
  recording has expired keep their old summary.
- Coverage is counted per stat, not per card. A summary lists only the stats it has (a stat
  its version didn't compute, or whose group was off, is absent, never 0). Each stat's
  "from N of M sessions" counts the sessions whose summary has that stat.
- Summary is small (a few KB) and survives retention.
- First set of career stats, folded per account × patch like the rest:

| Area | Stats |
|---|---|
| Wallet | balance at start/end of each session, net change per session, total earned vs spent (sum of rises and drops), biggest single rise and drop, balance over time |
| You | time on foot / EVA / seated, time as pilot / gunner / crew |
| Vitals | times downed, deaths and respawns (events), time out of air (oxygen 0), lowest health, time starving / dehydrated (0), peak radiation, hottest and coldest surroundings, peak intoxication |
| Flight | top speed, max G, distance flown (speed integrated over time), take-offs and landings (`is_landed` transitions), gear cycles, boost time and times boost ran critical, hydrogen and quantum fuel burned, time in NAV vs SCM (inferred from the shields/quantum warning lights; the 5.0 `master_mode` field is recorded but not used until 5.0 is LIVE) |
| Ship systems | warning counts by light (missile, hit, proximity…), time with shields off in flight |
| Weapons | countermeasures used by type (drops in count), weapon presets used |
| Settings | IFCS settings you fly with (share of flight time with ESP, G-safe, coupled, etc.) |

- Placement: telemetry cards on the existing pages they belong to (Economy, Flight & Travel,
  Combat, Activity). Each card states coverage, e.g. "From 12 of 40 sessions with telemetry".
  Sessions without a recording, or with the group off, are left out, never counted as zero.
  Cards use existing card primitives now. The design pass in section 5 covers them too.

### 10. Recording settings card

On the dashboard's settings, one "Telemetry" card:

- Status: game setting found per channel, live / waiting / off, current recording size.
- Record telemetry: on/off (default on).
- Detail: Full / Normal / Light (default Normal).
- Keep recordings: Forever / 1 year / 3 months / 1 month (default Forever). Expired files are
  deleted at startup and at session close. Their summaries stay.
- Groups: checkboxes (section 4 table).
- Disk used by recordings, and a "Delete all recordings" button (confirm first; summaries stay).
- Phone access (section 6).
- Changes apply to the next recording, except Record on/off which applies at once.

### 11. Backup & transfer

- Export: the JSON export gains a `telemetry` key holding `index.json`'s entries (summaries
  only, small), so career stats always travel. A new "Include telemetry recordings" option
  produces a `.zip` holding that JSON plus the recording files.
- Import accepts both, and older JSON exports without the key. Index entries and recordings
  already present (same file name) are skipped.

### 12. Status light

The existing status light / watcher heartbeat also shows the recorder: recording, waiting for the game, or telemetry off.

## Testing

- Unit: protobuf decode (packed floats, negative ints, enums, nested), schema extraction on a
  small synthetic descriptor, change compression and peaks, groups filter, FLT_MAX → null,
  truncated-tail reading, session matching, summary maths, retention.
- Fixtures: the probe gets a `--raw` option that saves the undecoded gRPC messages with
  timestamps. One real LIVE capture (on foot, seated, take-off, landing; a few minutes) is
  committed under `tests/fixtures/telemetry/` together with that build's descriptor bytes.
  The player records it with the probe. Until it exists, tests use a small protobuf encoder
  and synthetic frames.
- Integration: a fake gRPC server in `tests/` (an HTTP/2 framing stub, like the one used to
  test the probe) replays the capture through recorder → file → summary.
- Real game: one session on LIVE, checked against what happened in game.
- Tests live in `tests/` in the existing style (`python -m unittest`).

## Left to the builder

- Internal API shapes beyond what's named here, thread structure, lock use.
- The exact list of smooth fields (start from the table in section 4).
- QR code generator (local, stdlib or inline JS).
- How the recorder reads the watcher's live-channel state.
- Phone port fallback if 7879 is taken.
- Release notes and README text (voice rules apply).
