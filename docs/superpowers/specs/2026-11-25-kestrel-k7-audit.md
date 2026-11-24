# Kestrel K-7: mock vs shipped audit (2026-11-25)

Every feature, panel, key, state and alert of the approved mock (`live-view-tablet.html`) and the recorded
Live View design decisions, checked against `sc_slate.py` after the 2.0.0-beta first-session bug list.
Element list follows the data map (`.superpowers/telemetry-watch/live-view-data-map.md`).

Status: **ported** (live from the stream / log / ship data), **ported, not wired** (drawn as designed, sends
nothing to the game), **not sent on 4.10** (panel shows — until the game sends it), **dropped** (reason given).

The spec of 2026-11-24 listed eject/self-destruct keys, answer-in-game cards, party, QT target and mission
markers as non-goals. The user asked for them back after the first real session. They are restored below.

## Threat strip

| Item | Status | Source / note |
|---|---|---|
| Location, zone, jurisdiction | ported | L place + armistice/jurisdiction notices |
| SC time, PC time | ported | clock |
| MISSILE LOCK / TAKING HITS (combat view) | ported | T annunciators misl/hit |
| TAKING DAMAGE on foot | ported (fixed) | T health falling, 8 s; was never shown on foot |
| DOWNED chip | ported (fixed) | T ACTOR_DOWNED, 60 s |
| Vitals chips (health, O2 on EVA, water, food, intox, injured) | ported (fixed) | T vitals; missing values no longer read as 0 |
| Suit too cold / too hot | ported | T external temperature vs armor catalog (known items only) |
| Ticker | ported (fixed) | each new notice once, then idle; was cycling the last six |
| SCREEN key, bell | ported | peer commit 0d97783 |

## MAIN (seated)

| Item | Status | Source / note |
|---|---|---|
| Map, system and local, YOU marker | ported | M starmap + L place |
| Mission pins and pinned-objective marker | ported (fixed) | place named in the objective text ("above Aberdeen") matched to M |
| DEFENSE outline, turrets, guns per seat | ported | S + T weapons |
| Shields arcs, hull fill | not sent on 4.10 | "Hull · shields: not sent by this game build" row (peer ab54a3c) |
| Damage list under DEFENSE | ported | T annunciators, weapons |
| Combat view (auto + by hand) | ported | T misl/hit |
| Mock "damage takes over the map" card | dropped | demo rows only, superseded by combat view |
| FLIGHT: speed dial per ship, VSI, heading, ALT, GROUND, G, AIR | ported | T instruments + S speeds; ALT in metres (peer) |
| H2 / QT tanks, boost cells | ported | QT "NO DATA" on 4.10 (peer) |
| Flight state row (landed, gear, VTOL, wings, mode) | ported | mode inferred from annunciators on 4.10 (peer) |
| IFCS and POWER lamp grids | ported | T settings + power |
| YOU card rings, body figure, injuries | ported | T vitals + L injuries |
| CABIN / outside lamps and verdict | ported (fixed) | unknown air/pressure/gravity now — instead of "breathable", 0 atm, 0 g |
| Pinned mission card | ported | L contracts (see MSN) |
| NEARBY card | ported | L Vehicle Destroyed |
| Deck: NAV/SCM, gear, VTOL, wings, doors, locks, lights, coupled, power | ported | T states; unknown state shows — |
| Deck: QUANTUM, PING, ATC, HAIL, SET MODE (guns/missiles/scan/mining/salvage) | ported, not wired | S per ship |
| Deck: EJECT per seat | ported, not wired | game seat records + scunpacked (Marauder pilot ejects) |
| Deck: SELF-DESTRUCT with ship timer | ported, not wired | S timer shown on the key |
| Self-destruct countdown + red alarm | dropped until wired | the game logs no start; it ran only when the page sent the action |
| Gunner deck: night vision, leave seat, eject, emergency exit | ported, not wired / NV not sent on 4.10 | |

## MAIN (on foot / EVA)

| Item | Status | Source / note |
|---|---|---|
| BODY card: lamps, figure, armor tiles | ported | L AttachmentReceived + catalog |
| Gear: primary, secondary, sidearm | ported | L ports |
| IN HAND | ported (fixed) | L weapon_attach_hand_right/left; item leaves its old port when moved |
| MAGS / MEDPENS / OXYPENS counts | ported (fixed) | L slot ports, items leave a slot when used or moved |
| Health, suit rating | ported | rating only for catalog armor |
| SUIT cluster: O2 dial, outside temp/press/grav, food/water/temp tanks, heart, status lamps, radiation, intox, wallet | ported (fixed) | unknown readings show — |
| EVA O2 minutes left | ported | measured from the O2 drop, — until measurable |
| LAST SHIP card | ported (fixed) | CSR keeps the last vehicle frame (disk), survives reloads and restarts |
| Emote keys | ported, not wired | |

## SYS / YOU

| Item | Status | Source / note |
|---|---|---|
| Power groups, countermeasure tiles, weapons per seat, systems tiles, settings | ported | S + T |
| YOU vitals boxes, paper doll, carry tiles, suit panel | ported (fixed) | — for unknown |
| THIS SESSION tiles | ported | wallet delta, flown, blueprints (deduped), incapacitated, respawns (— , not sent on 4.10), seat changes |

## MSN

| Item | Status | Source / note |
|---|---|---|
| Active contracts, pin, list | ported | L Contract Accepted |
| Abandoned contracts leave the list | ported (fixed) | L `<EndMission> CompletionType[Abandon]` by mission id |
| Objectives (active, complete, withdrawn, failed, OPTIONAL) | ported (fixed) | L objective notices by MissionId / ObjectiveId |
| Type, giver, standing, rep, blueprint chips | ported (fixed) | C mission index by title + CSR's type rules; L [N Rep] tag |
| Hauling / mining SCU per drop | ported (fixed) | L "Deliver a/b SCU of X to Y" |
| Kills since accepting (bounty / combat) | ported (fixed) | L Vehicle Destroyed, killer = you |
| Package counts, wave notices | dropped | not found in 1,388 LIVE logs |
| Payout | dropped | the log has no per-contract payout; THIS SESSION shows status and rep |
| Map pins for missions | ported (fixed) | place named in the objective text |
| Finished this session | ported | complete / failed / abandoned |

## FEED / bell

| Item | Status | Source / note |
|---|---|---|
| All HUD notices with group filters | ported (fixed) | multi-line notices now parsed (party and most objective notices were dropped before) |
| Answer-in-game cards (party invite, friend request, shared contract, party launch, money) | ported (restored) | L, close on the answer line; open friend/money asks age out after 30 min (no decline line exists) |
| Bell: open answers + 8 recent | ported (restored) | |
| Toasts / ticker flash for new notices and asks | ported (fixed) | once each, past the 40-notice cap (seq) |

## NAV

| Item | Status | Source / note |
|---|---|---|
| Where you are, zone, jurisdiction | ported | L |
| QUANTUM TARGET + fuel estimate | ported (restored) | L Player Selected Quantum Target / Calculate Route |
| Party card, "N near you" | ported (restored) | L party notices + party marker streaming |
| LAST STOPS | ported (fixed) | L place history (survives reload) |
| Party positions on the map | dropped | not logged |

## PPL

| Item | Status | Source / note |
|---|---|---|
| Ships destroyed nearby, silhouettes | ported | L + S per class |
| BY YOU / OTHERS / CRASHED tallies | ported (fixed) | your handle from the log |
| Party card | ported (restored) | L |
| Names seen nearby | ported (restored) | L Vehicle Destroyed killers (other players only) |
| SHIPS YOU DESTROYED, LAST KILLED BY, INCAPACITATED | ported | LAST KILLED BY stays — : current builds log no own death with a killer |

## Server and tools

| Item | Status |
|---|---|
| Eject seats from the game's seat records (sc_slate_eject.py, tools/build_slate_eject.py) | new |
| Last ship kept on disk (slate_last_ship.json in the app folder) | new |
| Contract mission info in the payload | new |
| slate_dev.py --recording / --start / --speed replays a CSR recording | new |
| slate_audit.js: "bad value", "number without data", "js error" checks | new |

## Hardcodes removed

| Was | Now |
|---|---|
| LIVESET hand/mag/gren/med/oxy/util = null | from the log's ports |
| `const OUTSIDE=[[21,'']]` | removed (unused) |
| footEnv temp 0 / press 0 / grav 0 | null → — |
| envData dens 1, press 0, grav 0, breathable when unknown | null → — / "No outside readings" |
| VIT rad 0, intox 0, air OK when unknown | NaN/null → —, lamps read "RAD —", "INTOX —", "AIR —" |
| hrNow 0, SNOW 0, GNOW 1 at start | null → — |
| STATE defaults (master scm, gear up, power on…) | null → — |
| sdStart/sdCancel/sdRender, press(){} stubs | removed / press says "not wired" |
| model intox 0, countermeasures overwrite | null when missing, entries add up |
| Suit verdict "too hot" when the rating is unknown (21 > null) | rating checks skip unknown ratings, verdict reads "Breathable air · suit rating unknown" |
| Ticker cycling the last six notices | each notice once |

## Verified

- Full suite green.
- Browser audit (overflow, clipping, overlaps, bad values, numbers without data, JS errors) clean on: waiting state,
  4.10 takeoff capture, 4.10 landing capture on foot with the real Game.log, and the user's 2026-11-25 recording
  replayed (`slate_dev.py --recording ... --start 1530`), at 1180x820 and 375x812.
