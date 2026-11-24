# Kestrel K-7 Flight Slate — design

## Intent

CSR's Live view becomes the Kestrel K-7 Flight Slate: a cockpit-style dashboard for a tablet next to the
player, opened from the QR code in CSR's Telemetry card. It is the flagship of the next release.
The look and layout are the approved mock (`.superpowers/brainstorm/1287-1795499520/content/live-view-tablet.html`,
local only, git-excluded). This build makes it real: every number comes from the game.

## What it is

- The page CSR already serves at `/live` (PC) and on the LAN phone server (ports 7890-7899, private key).
  Same page on both. No new server, no new port.
- Data: the telemetry stream CSR records (`/api/telemetry/live`), Game.log lines CSR tails while the game
  runs, and ship data generated from scunpacked and the game's own files.
- Tablet first. Phones get the reduced layout from the mock. Desktop windows use the tablet layout scaled.

## Goals

- MAIN seated: map, DEFENSE (ship outline, shields, hull, guns per seat, turrets), pinned mission, YOU card,
  state keys, FLIGHT cluster (speed dial per ship SCM/boost/NAV, ALT, VSI, G, tanks, boost, IFCS and POWER
  lamps, OUTSIDE).
- Combat view: on with a missile lock or hits, back to calm 1 minute after the last one, COMBAT VIEW ▸ and
  ◂ MAP VIEW by hand.
- On foot and EVA: map, BODY card, SUIT cluster, last ship with silhouette.
- SYS, MSN, FEED, NAV, PPL, YOU pages as in the mock, fed by real data.
- Top strip: location, zone, SC time, PC time, warning chips, ticker, SCREEN key (fullscreen + keep-awake), bell.
- Turret guns per seat from the game's vehicle definitions (pilot, gunner, both).

## Non-goals (this release)

- Buttons that send actions to the game. Keys show state only. No simulated input, ever.
- Self-destruct and eject keys (actions).
- Answer-in-game cards that track the answer line (the FEED lists the notices).
- Party "near you" count, QT target and fuel estimate, map markers for missions.
- Positions of anything: the game does not send them.

## Anti-goals

- Mock or sample data in the shipped page (names, wallet figures, the user's handle).
- An MB-sized page. Ship data loads per ship at runtime.
- Restructuring `serve()`, the status menu, the watchdog or `csr.spec` (the release branch changes them).

## Constraints

- Show only what the stream or logs give. Missing field = "—" or the panel hides. LIVE 4.10 lacks
  shields, hull health and master mode; 5.0 adds them.
- User-facing text: short, flat, no ", so", no "; " joins, no hedging.
- Public repo: no personal data.
- Tests: `py -3 -m unittest discover -s tests`, all green.

## Approaches

1. **Port the mock into CSR modules, adapter in front (chosen).** The mock's renderers read a model built
   from the live payload by one pure function. Least new design, keeps every approved detail.
2. Rewrite the page from scratch against the payload. Cleaner code, loses the iterated layout and audit work.
3. Separate K-7 program with its own server. Duplicates CSR's log parsing, two programs to run. Rejected by the user.

## Data the page gets

- `GET /api/telemetry/live`: recorder state + decoded frame `snapshot` + `events`, plus `log` from the live
  Game.log tail (place, jurisdiction, armistice, notices, contracts, kills, injuries, loadout, blueprints).
- `GET /api/telemetry/ship?c=CLASS`: one ship's data (caps, turrets with per-mount guns and control, pilot guns,
  outline, hardpoints, dimensions, speeds). 404 when unknown.
