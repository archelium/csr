# Changelog

All notable changes to **CSR — Citizen Service Record** are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/), and CSR
uses [Semantic Versioning](https://semver.org/) (MAJOR.MINOR.PATCH).


## [1.4.0] — 2026-09-06

### Added
- **Blueprints.** A new section listing every crafting blueprint the game has handed
  you — and every one it hasn't. Patch 4.7 introduced them, and the client announces
  each one as a *Received Blueprint* notification, the only trace they leave in the
  log. CSR collects those across your whole history, counts each blueprint once, and
  shows the library with a category filter, a search box, first-received dates and a
  per-month unlock chart.
  - **What you're missing.** The SC-Wiki publishes its extraction of the game's
    crafting data — about 1,600 blueprints — and CSR fetches it (cached, bundled with
    the exe). An **Owned / Missing** toggle flips the list between what you have and
    what you don't, each missing entry noting how many missions can unlock it, and a
    per-category progress card shows owned against the catalogue total. "Missing"
    means *never seen received in your logs on this PC*, and the catalogue includes
    blueprints that exist in the files but may not be obtainable yet — so it is a
    wish-list, not a to-do list. Every entry links to its wiki page.
  - **The game's own names.** The notification carries whatever your UI displays, so a
    community language pack (StarStrings, ScCompLangPack) leaves its renamed components
    in your log: `Mil/1/D Tundra`, `MIL-2A "XL-1"`, `BlackFire Racing Helmet`. CSR
    matches each back to the real item — by game id first, then by the catalogue's
    spelling, then by the one entry containing every word of the logged name — and
    shows the original name, keeping what your UI said as a search alias.
  - **The collection board.** A collector's question isn't "how far along is Armour"
    but "which set am I one piece from finishing" — so the section now opens with
    **armour sets** (a square per slot, filled or hollow, sorted by pieces to go, with
    Started / Complete / Untouched tabs), **ship weapons as size ladders** (one family
    per row, S1 to S6 — Omnisky 6/6, Deadbolt 2/6) and a **ship-component grid** of
    type × size, each cell owned / in the catalogue. Sets are read off the game's item
    ids — the pieces of one set share a variant number, which holds even where the
    names don't (the ADP set's helmet is the *Balor HCH*); flight suits pair helmet and
    suit by name; weapon families are the class with the size token removed. Single
    pieces aren't sets and stay in the list, which now sits below the board.
  - **Categories** — armor, FPS weapons, magazines & batteries, ship weapons, ship
    components, flight suits, mining & salvage, and for the catalogue also attachments,
    clothing, medical, food, tools and flair — come from the game's item id, so owned
    and missing are classified identically. Every row carries a short *kind* — *Energy
    Pistol*, *Heavy · Arms*, *Laser cannon · S6*, *Cooler · S0* — so the list reads
    without opening the wiki.
  - **Honest about its limits.** There is **no ownership list in the log** — the
    crafting library is fetched from CIG's servers and never written down — so the
    count is a floor, not an inventory. Blueprints received before 4.7, or on a PC
    whose logs were never imported, can't be seen. A duplicate drop is counted once,
    with the repeat marked `×2`. PTU and Tech-Preview run on a copy of your account
    and show their own libraries under those channels.
  - Names that contain a quoted skin — `Atzkav "Mirage" Sniper Rifle` — are read in
    full. A first draft stopped at the inner quote and silently merged every skin of a
    gun into one entry; the difference was 217 blueprints versus the real 229.
  - Language packs also add a `[BP]` badge and, in some versions, wrap the whole
    notification in markup. A first draft anchored on the quote that normally precedes
    the text and lost 15 blueprints to that wrapping. Nothing is anchored on it now, and
    repeats of one notification are deduplicated by its id.
  - Parser version **13**: the first run after updating re-reads your logs.
- **Where you've been** (Flight & Travel). Star Citizen writes every HUD notice to the
  log from patch 4.5 — *Entered microTech Jurisdiction*, *Entering Armistice Zone*,
  *Hangar Request Completed* — and CSR now reads them: **hangars requested**,
  **landing-zone visits** (armistice stays ten or more minutes apart), and the
  **jurisdictions** whose space you entered, with sessions in Pyro's lawless space
  (Ungoverned, Rough & Ready, People's Alliance) called out. Klescher counts too.
- **Contracts by name** (Missions). The *Contract Accepted* / *Complete* / *Failed*
  notices carry the game's own titles — *Combat Gauntlet - Scenario #1*, *Help Protect
  Site* — so the Missions tab now lists what you actually took and finished, alongside
  the category view. Language-pack decorations (`[100 Rep]`, `[BP]*`) are stripped.

### Changed
- **Quantum jumps now count arrivals, not targets.** *Player Selected Quantum Target*
  is an intention; *Quantum Drive Arrived — Final Destination* is a jump. Across the
  2026 archive selections outrun arrivals by about 1.4× (re-targeting, aborted
  spool-ups), and in a session the player recalled as "two jumps" there were three
  arrivals — the one to a mission beacon had slipped their mind, the log had not.
  Expect the figure to drop by roughly a third; targets set are still shown beside it.
- **4.10 log format.** Read against the first 4.10 LIVE sessions (build 12545750),
  every signal CSR depends on was checked line by line against 4.9:
  - **Inventory requests were renamed** — `<InventoryManagementRequest>` is now
    `<Inventory Mgmt Request Queued>`, with the request text itself unchanged. Reloads
    and inventory transfers keyed on the old tag and would have read as zero for every
    4.10 session; both tags are accepted now.
  - **Reloads no longer arrive as `AmmoRepool` requests** — a session with six
    magazines fired had none. They are read from the weapon's magazine port instead
    (see *Corrected*).
  - **Quantum jumps and missions are unchanged.** The first two 4.10 sessions had
    neither, which read as the events being gone; a session with three jumps and two
    completed contracts showed the whole navigation family and the mission lifecycle
    lines exactly as in 4.9.
  - Unchanged and confirmed working in 4.10: sessions and playtime, ships flown,
    weapons drawn and carried, purchases and aUEC spend, fleet size, loot access
    tokens, the machine profile and launch benchmark, per-session frame statistics,
    disconnect and front-end events (a drop to the main menu was detected in the second
    4.10 session with exactly the 4.9 signature), and the HUD notification stream.

### Corrected
- **"Dropped to the main menu" is now "Back to the main menu", and says what it counts.**
  1.3.0 separated a server drop from a deliberate exit by whether the client had written
  its own quit request in the 20 s before the disconnect. That held for the 2025 logs it
  was built on. In every 2026 build the client writes that line about 7 s *after* the
  disconnect, on entering the front end — for drops and deliberate exits alike. Checked
  across all 274 qualifying events in the archive, and against a session where the
  player knew which was which: a deliberate *Exit to menu* from a server queue looked
  identical to a drop. No network-fault line precedes real drops reliably either (14%
  of them). So the count now openly includes deliberate exits followed by a rejoin, and
  the 1.3.0 figures (473 drops, 19.5% of sessions) should be read the same way. If you
  never use *Exit to menu*, nothing changes; since 4.10 lets you switch shards from the
  menu, some of yours will be you.
- **Reloads from 4.10 onward** are read from the magazine landing on the weapon's
  magazine port, counted only when it arrives alone — a zone change re-attaches every
  worn item in a burst of 20-50 lines, magazines included. Checked against a session
  with six known magazines fired: six, exactly. 4.1-4.9 keep their original method.
  Parser version **14**.

### Fixed
- **A slow log-off counted as a return to the menu.** The rule was "the log kept
  growing for two minutes after the disconnect", and a player who sat in the front end
  for 2½ minutes before quitting passed it. A return now counts only if the client
  then **finished joining a server again**. The live watcher confirms on that join too,
  instead of on the log growing.
- **The "latest build" could never be 4.10.** CIG rebranded the client's FileVersion
  from `4.9.188.x` to `1.0.191.x` in 4.10, and CSR picked the numerically largest
  version as the scope's build — so 4.9.188 would have stayed "latest" for as long as
  the archive existed. The build shown is now the most recent session's.

## [1.3.0] — 2026-07-28

### Live crash monitoring
- **CSR now watches your log while you play** and raises an alert on the dashboard
  within seconds of a crash or a real disconnect. The watcher tails only the bytes
  appended since its last check, at **2 s while a session is being written and 15 s
  when it isn't** — keyed off the log's own timestamp, so no process polling. The page
  checks in every 3 s while you're in-game and 6 s otherwise.
- **Crashes are now detected positively, not inferred.** Star Citizen writes its own
  post-mortem into the log, and that marker appears in **0 of 200** clean sessions —
  so a crash is now something CSR *reads* rather than something it guesses from a
  missing shutdown line. A second, independent trigger watches Star Citizen's own
  crash folder, which also holds a screenshot of the moment it happened.
- **Alerts name the fault in the game's own words.** A GPU crash shows CIG's actual
  advice ("consider updating any overlay/recording software…"); an out-of-memory crash
  shows how much RAM was in use against how much you have.
- **Benign disconnect noise is filtered out.** The 30000-series codes the community
  calls "30k errors" are mostly routine — `30010` alone fires ~5× *per session*
  (1,316 times across 240 logs) and `30016` is simply you quitting. Those never alert.
  Codes that do: `30024` back-end unresponsive, `30000`/`64010` timeouts, `70003`
  authentication, `70006` client integrity, and 8 others, each labelled in plain
  English and marked server-side where they are CIG's fault rather than yours.
- **Repeat-crash detection.** The log carries CIG's own crash signature, so an alert
  can say *"you have hit this exact crash 14 times."* Across the development archive,
  72 crashes collapse into 40 distinct signatures.
- **An alert bell in the top bar**, with a count badge. The pop-up alerts interrupt
  once and get out of the way; the bell keeps every one of them, with its time, for as
  long as the page is open — so an alert that fired while you were in-game is still
  there when you come back to the desktop. Opening it clears the badge, and a real
  crash links straight into the crash history.
- **Live monitoring panel** — which logs are being tailed, their size, whether a
  session is currently writing, when each was last checked, the current poll interval
  and how many events have been raised. The watcher used to run entirely silently,
  with no way to tell it was working.

### Dropped to the main menu
- **The thing players describe as "it just booted me to the menu for no reason" is now
  counted.** It has never appeared in any stat — including CSR's — because Star Citizen
  records it as *the player* asking to disconnect: `cause=30016 reason="Remote
  Disconnect - Player requested disconnect"`, the exact line it writes when you quit on
  purpose, with `isRemote=1` either way. It sits inside the routine chatter every
  session produces, so nothing has ever pulled it out.
- **How CSR tells the two apart** — three signals, checked across 1,288 logs:
  1. `gamerules="SC_Default"` — the persistent universe, not the front end or Arena
     Commander, whose disconnects fire constantly and mean nothing.
  2. **No client-side quit request in the 20 s before it.** When you leave on purpose
     the client logs its own intent first (`RequestQuitLobby`, `DisconnectCmd`,
     `CSystem::Quit`); when the server drops you, nothing precedes it. This alone
     removes 240 deliberate exits that otherwise look identical.
  3. The client loads the front end within 15 s **and keeps logging for 2 more
     minutes** — you rejoined and carried on, which is what makes it a drop rather
     than the end of a session.
- A new **Dropped to the menu** group reports the count, the share of sessions hit, and
  the **rate per 10 hours played** — a raw count would make a heavy month always look
  worse than a light one — plus the most recent ones with timestamps and a by-month
  trend. Quiet months stay on the chart at zero, on purpose: that is how you see when
  it started.
- **The live watcher raises it too.** It can't apply the "did play continue?" test in
  the moment, so a drop is parked and only raised once the log grows again — the player
  back in the game. Quit instead and it is silently discarded.

### Taking a crash further
- **Every crash is now listed, not just the last one** — 20 to a page, newest first,
  each with its time, type, crash signature and how much memory was in use. The
  aggregate above it says *what* keeps breaking; this says *when*. The
  "see crash history" link from an alert finally lands on an actual history.
- **Copy a bug report.** One click puts a formatted report on your clipboard — build,
  local time, exception, CIG's crash digest, memory at the moment it died, how many
  times you have hit that exact signature, and your full rig. That is nearly everything
  an Issue Council report needs; you add what you were doing and submit.
- **No fake bug links.** CSR does *not* claim to have matched your crash to a known
  issue, because nothing public supports it: the SC Wiki has one error-code page
  (`Error:30000`) and none for the other nine codes CSR knows, and the Issue Council is
  searched by symptom, not by exception or crash digest. What CSR does instead:
  - Suggests a **search term only where one is meaningful** — *GPU crash*, *out of
    memory*, *freeze watchdog*, *fatal error*. For `EXCEPTION_ACCESS_VIOLATION` it says
    plainly that the exception is too generic to search: it only means the game touched
    memory it shouldn't have, and the 29 in this archive are 29 different bugs.
  - Links to the Issue Council project itself rather than shipping a `?search=` deep
    link — it is an Apollo app whose search parameter could not be confirmed, and a
    button that silently ignores your search is worse than one that doesn't promise it.

### System & Stability
Built from the startup banner the game writes at every launch, and aimed at the question
new players actually ask — *"is it the game, or is it my PC?"*
- **Your machine** — CPU and core count, GPU with video memory, system memory, display
  mode, renderer, GPU driver, Windows build and DLSS availability, exactly as Star
  Citizen saw them. Available for every patch back to 4.1. **Changes over time** gives a
  dated list of when the machine actually changed, plus a full **GPU driver history**.
- **Renderer detection.** Sessions are identified as **DirectX 11** or **Vulkan**, with
  the split shown on the spec sheet — useful while SC's Vulkan rollout is ongoing. Vulkan
  reports its driver through a different mechanism entirely (and with different version
  numbering), and both are now read.
- **Session outcomes** — how each session ended: a normal exit, a **confirmed crash**,
  **died without a report**, or **back-end unresponsive** (CIG's `30024`). Shown as a
  donut plus a *rough sessions by patch* trend, so a bad patch is visible rather than
  remembered.
- **Launch benchmark** — Star Citizen runs a short CPU and GPU test at every launch. CSR
  tracks that score across your history and charts the average per patch.
- **Configuration check** — a short list of things that are *measurably* wrong:
  - **Star Citizen is not using your fastest graphics card.** CSR compares every adapter
    the client enumerated against the one it actually rendered on. This catches laptops
    running on the integrated chip while a discrete GPU sits idle, and the
    software-renderer fallback — the two highest-impact misconfigurations a new player
    can have, and both invisible from inside the game.
  - **Memory below CIG's published figures** (16 GB minimum / 32 GB recommended), and
    video memory under 8 GB.
  - **Upscaling disabled while the driver reports DLSS as available.**
  - **Whether Star Citizen auto-picked your settings** (`AutoDetect`).
- **Your current graphics settings**, read from the game's own profile
  (`attributes.xml`) and shown with the **same names and tiers as the in-game menu** —
  *Shadow Maps: Ultra*, *Detail Textures: High* — rather than the raw numbers the file
  stores. Each level meter is sized to the options *that* setting has, not a flat five,
  because most stop short: detail textures and texture filtering end at High, screen
  space shadows and shader quality at Very High, ground textures offers only Low and
  High. Verified row by row against the Graphics menu.
- **Settings can be changed from CSR**, marked **BETA**:
  - **Re-run Star Citizen's auto-detect** — hands the choice back to the game, which
    picks every setting from the benchmark it runs on your hardware. That is CIG's own
    calibration, across far more machines than CSR will ever see.
  - **Nudge the overall quality preset** one step, to judge for yourself.

  Only those two values are ever written. Every write backs the original file up first,
  changes only the `value=` of entries that already exist (never adds or removes keys),
  writes via a temp file and atomic replace, **refuses while Star Citizen is running**,
  and can be undone from the page — to either the previous state or the original, both
  dated in the UI. The endpoint accepts only those two keys and only integer values.
- **Shareable rig card.** A copy-to-clipboard summary of your hardware, benchmark score
  and stability rate. CSR has no server and never uploads anything — this exists so
  players can post their own numbers and give newcomers something real to compare
  against.
- **"How rough a patch feels is mostly the patch"** — Session Outcomes contrasts your
  best and worst patch directly. On the development machine that is 4.8 at 1.7% against
  4.7 at 33.6%: a 20x swing with nothing changed at the player's end.
- **Plain-English verdict for new players.** Under 10 rated sessions CSR stops showing
  charts it cannot fill and simply says what happened — including how many of the bad
  sessions were server-side and therefore not the player's hardware.

### Changed
- **System & Stability is two tabs**: **Your Machine** (rig, configuration check, frame
  rate, hardware score) and **Stability** (live monitoring, crashes, drops to the menu,
  session outcomes). One tab had grown to eight groups, and you were scrolling past your
  CPU model to find out what crashed you an hour ago. Within Stability the order follows
  the question: what is being watched now, then what broke, then how often it breaks.
- **A heartbeat in the sidebar.** The Stability entry carries a live dot: green while CSR
  is tailing your logs, amber and faster while Star Citizen is actually writing one. It
  is the watcher's only presence outside its own tab, and is hidden entirely on a saved
  copy of the page, where nothing is running behind it.
- **Live monitoring looks alive.** A **sweeping scanner bar** across the top of the card,
  which speeds up and turns amber the moment Star Citizen starts writing. A dot on its
  own read as decoration rather than activity. The channel read-outs and poll figures are
  legible instead of 9.5px mono. Both animations stop under `prefers-reduced-motion`.
- **Times are the clock you played on, not UTC.** Star Citizen stamps every log line in
  UTC but names its rotated logs in local time, so CSR reads the offset off the pair and
  converts once, at scan. *Your last crash* gains a **time**, and the *when you play*
  heatmap moves to the right hours — it was reading 7 hours early on this machine, so a
  10pm session was plotted at 3pm. Because the offset comes from the log itself, a folder
  imported from a friend in another timezone keeps *their* clock.
  - Sessions archived by an older CSR **whose logs have since been deleted** cannot be
    re-read, so they are shifted using the file time recorded at the time — 180 of 1,499
    here. Without that they would sit 7 hours off inside the same chart.
- **Two settings dropped from the configuration check.** `Particles` and
  `PlanetTerrainVirtualTextures` are in `attributes.xml` but have **no row in the
  Graphics menu**, so CSR could neither confirm their tier names nor point you at a
  slider. Everything now listed has a menu row you can go and look at.
- **The simulated-event test buttons are gone** from the Live monitoring panel. They were
  a way to prove the detector worked before it had ever seen a real crash; the
  `/api/test-event` endpoint they called is still there for anyone who wants it.
- **The top bar is narrower and lines up with the content.** Its controls needed more
  width than the 1180px column the cards sit in, so the bar ran wider than everything
  under it. Controls are down from 932px to **789px** and the bar is capped to the same
  column:
  - The status light is **just the light**. "CSR ON" cost 50px to say what a green
    pulsing dot already says; the full state moved to a hover tooltip (*CSR is running —
    click for options*) and to the button's accessible name. Clicking still opens the
    same menu.
  - The patch dropdown reads **Career / 4.1 … 4.9** instead of *Career (all patches) /
    Patch 4.1*. A `<select>` is as wide as its widest option, and the field is captioned
    PATCH directly above, so the word was being paid for in every row. Headings and the
    scope badge still say *Patch 4.9* in full.
  - Every top-bar button is one 34px square. They were 44px while the bell came out at
    40 and the status light at 34 — three sizes of the same thing.
- Contact address is now **support@archelium.com** (was `csr@archelium.com`) — in the
  About modal, the page footer, the "Report a bug" mail template, and the licence,
  privacy and disclaimer documents.
- Memory sizes are reported at their real capacity. Windows logs memory *minus* what the
  hardware reserves, so a 64 GB machine writes `63122MB` and a 16 GB card `16045MB`; both
  now display as the size on the box.

### Fixed
- **Legacy Combat listed your own handle as a weapon.** The kill lines in patches
  4.1–4.3 sometimes name the *player* in the weapon field — a self-inflicted death —
  and the filter that dropped those had the author's handle written into it as a
  literal string. Correct on exactly one machine, and wrong for everybody else since
  v1.0.0: your own handle appeared in the weapon rankings. CSR now collects handles as
  it scans and filters against those.
- **"Session live" kept showing after you had already quit.** A log timestamp cannot tell
  "being written because you're playing" from "being written because Star Citizen is
  shutting down" — and SC's exit is drawn out, so for ~30 s after quitting, *last written
  4 seconds ago* looked exactly like an active session. CSR now **asks Windows whether
  `StarCitizen.exe` exists**, which settles it instantly.
  - This reads **nothing from the game**. It takes the same process-name snapshot Task
    Manager shows, needs no elevation, and deliberately never calls `OpenProcess` — a
    handle *into* the game process is the thing an anti-cheat has an opinion about, and
    CSR has no reason to want one. ~7 ms, cached for 1.5 s.
  - Live monitoring shows four states instead of two: *Session live — reading as you
    play*, *Star Citizen is open — waiting for it to write*, *Star Citizen is closed —
    watching for the next session*, and plain *Watching your logs* when the process list
    cannot be read (a non-Windows build, or the API refusing). In that last case CSR
    falls back to exactly the old timestamp-only behaviour rather than guessing.
  - The poll rate follows the game, so CSR stops checking every 2 s once you quit.
- **Every channel card showed the same "last seen" time.** Not a glitch — the value was
  *when CSR last looked*, and all channels are stat'ed in one pass, so it was identical
  by construction: one global fact printed three times. Each card now shows **when that
  log was last written**, which is what actually differs (`written 00:10` for today,
  `written 26 Jul 04:13` for older), and the check time appears once in the footer. A
  channel with no log on this PC says so instead of showing a size of zero.
- **"seen now ago."** The relative-time helper returns the word *now* for anything under
  a second, which does not take an "ago" suffix. It reads *checked just now*.
- **The drops-per-month chart was unreadable.** It sat in a half-width column, and
  because the bars render into a viewBox that scales to its container, a ~900px chart
  squeezed into ~600px lost its height too and the bars became slivers. It is now full
  width. The *Most recent* list underneath dropped its third column — every row said
  "back to menu", which is what the whole card is about — and shows **the gap since the
  previous drop** instead, because these arrive in clusters.
- **The crash list shrank the card on its last page.** 68 crashes over 4 pages means the
  last holds 8, and the card collapsed under the cursor when you reached it. Short pages
  are padded with empty rows of matching height — verified identical on all four pages.
- **Button rows rendered as a stack of full-width bars.** `.foot-link` carried
  `width:100%` from the sidebar footer it was written for, and an `<a>` styled with it
  came out as a bare underlined link beside a boxed `<button>`. Buttons in a row now size
  to their own label and sit on one line.
- **The multi-account prompt never appeared.** Two separate faults: it switched itself on
  1.2 s after page load, which on a first run is *while the scan terminal covers the
  screen* — it turned on underneath it, invisible — and it only ever ran once per load,
  so it never got a second chance. On top of that, a global handler hid it on **any**
  click, so the first click anywhere buried it until the next reload. It now waits for
  the terminal to close and stays until you answer it.
- **Jumping to a section left the page scrolled where it was.** Every in-page jump used
  smooth scrolling, which is a no-op under reduced-motion settings and in some embedded
  browsers — so it silently did nothing. All jumps are now instant.
- **"See crash history" on a crash alert did nothing**, because it navigated to the tab
  you were already on. It now scrolls to the Crashes group itself. Simulated events no
  longer offer the link at all — they are never written to a log, so they cannot appear
  in the history, and following it would have shown your last *real* crash instead.
- **CSR went blank while a hotfix was being applied.** CIG's own procedure for taking a
  hotfix is to rename `LIVE` to `HOTFIX`, patch through the launcher, then rename it
  back — so a folder saved as `…\StarCitizen\LIVE` genuinely does not exist for the
  length of that window. CSR looked at the saved path, found nothing, and stopped there:
  **no channels at all**, not even PTU, which never moved. It now recognises that the
  parent is your StarCitizen folder and reads the channels from there. Hotfix sessions
  were already folded into LIVE, so nothing splits your career.
- **The Import logs card had a yellow icon** while every other card's was white. The
  three *Backup & transfer* headings used text characters, and `📁` is an emoji — Windows
  draws it from a colour font, so it ignores the CSS colour entirely. All three now use
  the same drawn SVG icons as the rest of the interface.
- **"Choose log folder…" appeared to do nothing.** The Windows folder dialog *was*
  opening — behind the browser window, where you would never see it, while the button sat
  on *Waiting for folder…* looking hung. Three separate ways of forcing it to the front
  were tried and none of them worked: the click happens in the **browser**, so the browser
  is what Windows regards as having earned the right to raise a window — not the CSR
  process reacting to it a moment later.
  - Rather than escalate to something more invasive, CSR now **tells you** the dialog may
    be behind this window the moment it opens one, and offers **"or paste the folder
    path"** in the same breath. That route doesn't involve window focus at all, so it
    works everywhere. Both go through identical import code.
  - The failed approaches are written up in the source so the next attempt starts from
    where this one stopped.

### Corrected
- **Star Citizen *does* log frame rate — the previous release said it didn't.** On level
  unload the client writes average FPS, average frame time, and a nine-bucket frame-time
  histogram; it is present in **165 of 200** clean logs, not just crashed ones. v1.2.0's
  documentation and UI stated flatly that no build had ever logged this. That was wrong,
  and it was the stated reason CSR would not evaluate graphics settings. A new
  **Performance** section reports it (883 sessions on the development machine: 68.2 FPS
  average, 15.4 ms, 0.28% of frames over 50 ms). The settings guidance is still delegated
  to auto-detect, but for a narrower reason: a whole-session average is too coarse to
  judge one change against another, not that the data is absent.
- **The per-setting quality scales are knowable after all.** Earlier development notes in
  this release said they could not be inferred. They can: the stored number is a position
  on one global scale (1 Low, 2 Medium, 3 High, 4 Very High, 5 Ultra) that most settings
  only expose part of — ground textures offers Low and High and stores High as **3**, not
  2, which is what proves it. CSR still writes only the master preset, but now for the
  real reason: setting one slider by hand means deciding what all the others become.
- **"No clean exit" was too blunt** and has been split into **confirmed crashes** (the
  game reported them) and **died without a report** (task-kill, power loss, or a driver
  reset that took the game down before it could write anything). On the development
  archive that is 68 and 41 respectively, previously lumped as 109.

### Notes on honesty
- **Kills are still not in the log — checked against a session with known kills.**
  Three NPC ships and around eight NPCs on foot produced no kill, death or destruction
  event of any kind in 4.10. The mission objective lines that do exist (*Hostiles
  Remaining*, *Waves Defeated*, *Defeat Hostile Ships*) are re-pushed on every change,
  but they tick on spawns and new waves as well as kills, and the number itself is never
  written — so they are not used as a kill count. Grenade throws and medpen use are not
  logged either; only equipping them is.
These were deliberate design choices, not omissions:
- **Frame rate is a whole-session average**, written once per level rather than sampled
  continuously — a session that ran at 90 FPS in space and 30 in a city averages out in
  between. It is reported as such, and never as a live counter.
- **CSR still won't tell you your "optimal" settings.** It can change them for you, but
  it does not claim to know the right answer. A rule-of-thumb recommender (VRAM + RAM +
  resolution to preset) was written and then **discarded after testing**: against the one
  piece of ground truth available — Star Citizen's own auto-detect had chosen preset 5
  for the development machine — the heuristic said 3, and it also put a 4090 at 4K on 3.
  A rule that contradicts CIG's tuning in the only case that can be checked, with no
  frame-rate data to settle the disagreement, is not worth shipping.
- **Only two values are ever written**: the master preset and the auto-detect flag. The
  ~20 individual quality sliders are read for display but never modified — each has its
  own ceiling (this machine sits at 3, 4 and 5 simultaneously under auto-detect), so
  changing one by hand means deciding what the rest should become.
- **No fake bug links.** CSR does not claim to have matched your crash to a known issue,
  because nothing public supports it: the SC Wiki has one error-code page (`Error:30000`)
  and none for the other nine codes CSR knows, and the Issue Council is searched by
  symptom, not by exception or crash digest. A search term is suggested only where one is
  meaningful; `EXCEPTION_ACCESS_VIOLATION` gets none, because it only means the game
  touched memory it shouldn't have and no two are the same bug.
- **Every write is reversible.** The original file is copied to `.csr-backup` before the
  first change, and "undo" restores it byte-for-byte (verified by hash).
- **The launch benchmark is presented as a cross-machine score, not a trend.** Measured
  across 1,283 launches it cannot resolve driver changes (+/-1-2%) or renderer changes
  (the sign flips between patches) against a per-launch spread of ~250 points. The
  per-patch chart is labelled a consistency check, because the measurement cannot support
  the conclusion a downward slope would invite.
- **Rates are suppressed below 10 rated sessions.** One crash in three launches is not
  "33% unstable", so thin scopes show a raw count instead of a percentage, and patches
  with too few sessions are dropped from the trend chart — with the number dropped stated
  on the chart rather than silently shortening it.
- **The launch benchmark is noisy** (the same hardware scores 409-655 across launches),
  so only multi-launch averages are presented, and the spread is always shown next to the
  mean.
- **Back-end outages are labelled as CIG's, not yours.** A bad month of `30024` errors is
  a server-side problem and the UI says so, so nobody concludes they need new hardware.
- **A session still in progress is never counted as a crash.** The live `Game.log` has no
  ending yet; it is excluded rather than scored.

### Upgrading
Replace `CSR.exe` in place, keep the `CSR-data` folder beside it. The first run re-reads
every log once (a minute or so) because the parser now extracts hardware, timing and
session-outcome data it previously ignored; after that, startup and refresh are back to
about a second.

Sessions already in your archive whose original logs Star Citizen has since deleted
**cannot** gain the new fields — there is nothing left to re-read. Those are counted as
*unrated* and shown as such, rather than being quietly assumed to have ended well.

## [1.2.0] — 2026-07-27

### Upgrading
Replace `CSR.exe` in place and start it — settings, cached art and your career
archive live in the `CSR-data` folder beside it and are picked up automatically.
**Don't delete that folder** (from v1.1.0 it holds sessions Star Citizen has since
deleted from your log folder); export a backup first if you want to be safe.

The first run re-reads **every** log on purpose: this release fixes several parsing
bugs, so nothing older is trusted. That happens automatically and only once —
afterwards CSR starts in about a second, because it reuses any log file that hasn't
changed. Coming from **v1.0.0**, which kept no archive, your history is rebuilt from
the logs you still have — and if your saved folder was `…\StarCitizen\LIVE`, CSR now
finds `PTU` / `TECH-PREVIEW` beside it without you re-picking anything.

Expect some numbers to change: patch 4.1 gains reloads and item transfers it never
recorded, and several stats that wrongly showed `0` now correctly show `N/A`.

### Added
- **New FPS loadout metrics.** Weapon cards and the Combat tab now report
  **reloads** (magazine top-ups, from the game's ammo-repool events) and
  **carried** (times a weapon was stowed on your back or holstered), alongside a
  renamed **drawn** count. These come from *client-side* inventory events, so
  unlike kills/K-D they cover your **entire career, every patch**.
- **Looting & inventory.** A new Combat sub-section reports **containers looted**
  (crates and lockers, broken down small / medium / large), **items transferred**
  between inventories, and **corpse loots** — gear stripped off NPC bodies, tagged
  *(approx.)* because it is inferred by subtracting your own worn gear from the
  inventories you opened.
- **Original SC-HUD icon set.** 45 custom single-stroke SVG icons now replace
  every emoji in the dashboard — hero stat tiles, section titles, callouts, the
  export/import/backup/refresh/theme buttons and the nav.
- **Startup and refresh are near-instant.** CSR used to re-read all your logs every
  single launch. It now reuses any file that hasn't changed since it was last parsed,
  so starting the app takes about a second instead of a minute or two. Refresh offers
  **Quick refresh** (same behaviour) and **Full re-scan** for when you want everything
  re-read from scratch. Sessions record which parser version produced them, so a CSR
  update still re-parses your whole history automatically — exactly once. Also
  available as `--quick` on the command line.
- **Patch stepper.** ‹ › buttons beside the Patch dropdown (and the ← / → arrow
  keys) step through patches without opening the list.
- **Import a copied log folder.** *Backup & transfer* now has an **Import logs**
  option: point CSR at a `Game.log` folder copied from another PC and it reads it
  straight off disk. No need to install CSR on the other machine just to press
  Export, and no browser upload — log folders run to hundreds of megabytes. Each
  log still declares its own channel, so copied logs land in the right career.
  Sessions merge and de-duplicate, so importing the same folder twice adds nothing.
- **Grouped sub-sections.** Tabs that cover more than one subject now split into
  clearly numbered groups with their own icon, title and divider, instead of
  relying on a small caption: Combat (ship / on-foot / looting), Legacy Combat
  (ship / on-foot / rivals), Flight & Travel, Economy (spending / cargo) and
  Orgs & Profile.
- **Travel merged into Flight & Travel.** Travel only ever had two stats — fuel
  burn, distance and per-POI visits genuinely aren't in the client log — so it is
  now the second group of the Flight tab, with two new derived figures:
  **jumps per session** and **jumps per hour**. Old links to Travel still resolve.
- **Pick your main account.** CSR guesses your main by session count, which is
  wrong for anyone whose alt has more hours (a reinstall, an org alt, a shared PC).
  A **★** button beside the account picker pins the real one; the choice is stored
  per channel and CSR opens there by default. If you have more than one account,
  a one-time prompt on first run points this out — it never appears for
  single-account users, and never again once you've answered it.
- **CSR logo is now a home button** — returns to Overview and resets channel,
  account and patch to their defaults.
- **CSR status light.** A pulsing indicator at the right of the top bar shows whether
  the app behind the page is running (green), scanning (amber) or gone (grey) — a
  saved `CSR.html` looks identical to a live one, so Refresh and Import used to fail
  with no explanation. Clicking it offers **Shut down** and **Restart**; a restart
  reuses the tab you're in rather than opening a second one. It cannot *start* CSR —
  a web page has no way to launch a program on your PC — so when it's offline the
  menu says what to run instead.
- **The console window hides itself a second after launch.** Everything is driven
  from the browser now, and the page's own flight-recorder terminal already reports
  the scan, so leaving a second window narrating the same thing is just clutter.
  Bring it back any time with **Show console** in the status menu, or start with
  `--console` to keep it. If the console is hidden and no dashboard has checked in
  for 15 minutes, CSR reveals itself and exits rather than lingering invisibly with
  no way to reach it.
- **The console is now a fixed frame, not an endless scroll.** A title box with the
  address and channels stays put, log lines scroll inside their own bordered pane,
  and the progress bar is pinned to the bottom — so the URL and status no longer
  vanish up the window within seconds. Redirected or piped output (scripts, CI) still
  gets plain lines.
- **The loading terminal can no longer trap you.** Refresh first checks the CSR
  app is actually running and explains plainly if it isn't; if the app dies
  mid-scan the terminal shows a FAULT state with a **CLOSE** button and **ESC** to
  return to your dashboard.
- **Slower, funnier scan terminal.** The console stream now crawls about **4.5×
  slower** (a line every ~400–630 ms instead of every 112 ms) so lines can actually
  be read, red warnings pause on screen for 2.3–3.6 s so the joke lands, **no line
  repeats within 30 seconds** (the picker skips a line rather than show it twice), and
  the pool grew from 31 to 81 — including a lot more
  Star Citizen in-jokes. The numbers in those jokes are bounded to plausible
  ranges: real-money (pledge/warbond) amounts show as **$2,000–$60,000** rather
  than fantasy millions, hull integrity after hitting an invisible asteroid is
  **1–4%**, and being stranded puts the nearest station **18–34 Gm** away.
  In-game aUEC figures keep their absurd millions, as intended.

### Changed
- **Overview snapshot re-laid out.** Eight equal tiles wrapped into a lopsided
  6 + 2; it is now four headline tiles above a strip of compact supporting stats.
- **Icons enlarged** throughout the dashboard for legibility.
- **The scan terminal now reads like a real console.** The log feed sits in its own
  bordered sub-window with a labelled frame and a live line counter, so the CSR
  header, telemetry and progress bars stay put while only the text scrolls inside it.
- **The terminal appears when you asked for it, and stays out of the way when you
  didn't.** Making the backend 20× faster meant a quick refresh flashed the terminal
  past showing two lines, so anything you deliberately trigger now holds for at least
  three seconds; longer jobs are unaffected. Startup skips the terminal entirely when
  there is nothing to re-read, instead of flashing it for half a second.
- **Anything CSR downloads is now fetched once and kept.** Ship and weapon art,
  manufacturer logos, your RSI dossier and your org list were re-scraped on every
  rebuild — about 35 network requests — which was the real reason a refresh took
  ~27 seconds even when nothing had changed. None of it moves often, so it is cached
  indefinitely and only refreshed on its own after 30 days. A new **Online data**
  panel in *Backup & transfer* shows when it was last updated and lets you refresh it
  on demand — for when you've joined an org, changed your avatar, or artwork failed to
  load. That also retries images whose earlier download failed; a failure used to be
  cached forever. A lookup that fails now falls back to the cached copy rather than
  blanking your dossier.
- **Cargo & commodities is always shown**, reading `0 — no bulk cargo bought in
  this scope` when empty. It used to disappear entirely, which made the page change
  shape between patches and looked like missing data.

### Fixed
- **Reloads and item transfers were missing from patch 4.1.** Star Citizen changed
  the wording of its inventory log lines between builds — 4.1 writes
  `Request[N] Type[X] Player[…]` where later builds write
  `Queued Request[N] … for 'handle'`. CSR only matched the newer form, so 4.1
  reported **0 reloads and 0 transfers**. It now matches both, recovering 192
  reloads and 3,551 transfers in 4.1 alone.
- **Armour, weapon attachments and props were listed as "tools".** The classifier
  treated anything it didn't recognise as a gun as a tool, so armour pieces, optics,
  laser pointers, mounts, mobiGlas and even books and mugs appeared under
  *Tools & utility*. Held items are now classified three ways (gun / tool / ignore).
  Guns whose class carries a factory attachment suffix (e.g. the Volt Energy SMG)
  are still correctly identified as guns.
- **Gun cards hid the reload figure when it was zero**, which read as if the stat
  were missing. Cards now always show a RELOADS chip — a real count, or **N/A**
  when that patch's game build never logged reloads at all.
- **A channel's career could exceed the sum of its patches.** Tech-Preview *feature*
  branches (`Branch: scp-crafting`, `scp-inventory`) carry no version anywhere, so
  those sessions counted toward the career but landed in no patch bucket — Tech
  Preview showed 22 career sessions against a single 6-session patch. CSR now falls
  back to the client's own FileVersion, and keeps a labelled bucket for anything
  still unversioned. All channels now reconcile exactly.
- **Quantum jumps showed 0 for patches 4.1–4.3.** Star Citizen only began writing a
  player-attributable jump event in 4.4. Older builds log a travel-envelope error
  instead, but it fires for *every* ship in the area — including other players' —
  so it is deliberately not counted. Those patches now read **N/A**, not a false 0.
- **Fleet size showed nothing for patches 4.1–4.7.** The ASOP vehicle count only
  appears in the log from 4.8. Older builds report an "entitlements" figure that
  counts pledge items rather than ships (42, 68, then 209, 227 against a real fleet
  of ~60), so it is not used. Those patches now read **N/A**.
- **CSR re-read every log on every launch.** Startup now reuses log files that
  haven't changed, so relaunching takes about a second instead of a minute or two.
  A CSR update still re-parses everything automatically, exactly once.
- **Tech-Preview builds were listed in the wrong order and without versions.** They
  were sorted after the numbered patches, which put 4.10 *before* previews played five
  months earlier. Patches are now ordered by when you actually played them, and each
  preview shows the build it ran (`1.0.172.14271`) — the only version a feature branch
  has — on the chart, in its tooltip and in the dropdown.
- **The first-run "pick your main account" prompt was killed by any stray click.**
  Dismissing it by clicking elsewhere marked it permanently answered, so it usually
  disappeared within a second of the dashboard loading and never came back. Clicking
  away now merely tucks it aside; only pressing **★**, *Set as main* or *Not now*
  settles it for good.
- **Importing logs opened the loading terminal before you had picked a folder**, so it
  animated over an unanswered dialog and looked like a hang. Picking now happens first,
  and the terminal reports the real phases (scanning → parsing → merging).
- **Importing logs demanded a Star Citizen folder layout.** Any folder containing
  `.log` files at any depth is now accepted — copied folders rarely keep the original
  `LIVE\logbackups` structure. Channel attribution is unaffected: every log names its
  own channel.
- **Tech-Preview feature branches were all labelled "Patch 1.0"**, implying a 1.0
  release that hasn't happened (those builds report `FileVersion 1.0.x`). They are now
  named after the branch they preview — *Crafting preview*, *Inventory preview* and so
  on — which also separates builds that were previously lumped together.
- **The import file picker hid your files.** It filtered to JSON only, which Windows
  labels "Custom Files" — so a folder of `.log` files looked empty unless you switched
  the dialog to *All Files*. Backups now use a plain `.json` filter, and raw logs have
  their own folder-picking route instead of being forced through a file dialog.
- **Tool list included attachments, canisters and refills** — OreBit Mining
  Attachment, Cambio-Lite SRT Canister, ParaMed Refill, mining gadgets and similar.
  Only standalone tools are ranked now.
- **aUEC amounts in the scan terminal were malformed**, mixing hex letters into
  figures and grouping digits wrongly (`1954,938`). They are now real integers with
  correct thousands separators.
- **Two joke lines contradicted themselves** — a Daymar rally could report more
  survivors than racers, and locations were deprecated in patches like `3.87` that
  never existed (SC 3.x ended at 3.24). Both are now bounded.
- **The "press Ctrl+C" hint scrolled off screen.** It was printed before scanning
  started, so the progress output buried it and there was no visible way to quit. It
  is now reprinted once CSR is ready.

## [1.1.0] — 2026-07-26

### Added
- **Backup & multi-PC (data portability).** CSR now keeps a running, de-duplicated
  **archive** of every session it has scanned, so your career survives even if
  Star Citizen deletes old logs. A new **⛃ Backup & transfer** panel lets you
  **Export** your whole career as one file, or **Import** a backup from another PC —
  sessions merge and de-duplicate by fingerprint, so nothing is double-counted.
- **Animated loading terminal.** Onboarding, Refresh and Import now play a
  full-screen Star Citizen "flight-recorder" terminal while CSR works — block-art
  banner, HUD sweep + scanlines, a live scan meter, and blinking status lights
  (PWR / UPLINK / REGISTRY / DECRYPT / PARSE / SYNC) — then dissolves into your
  dashboard.
- **Multi-channel careers.** CSR now reads every test channel you play —
  **PTU, Tech Preview (TP), and EPTU** — and keeps a separate career dashboard
  for each, alongside **LIVE**. A new **Channel** dropdown in the top bar switches
  between them (hidden when you only have one). LIVE is the default. Hotfix (a
  live-service emergency-patch branch) counts toward your LIVE career.
- **Pick-your-StarCitizen-folder onboarding.** Instead of pointing CSR at your
  `LIVE` folder, you now select your **StarCitizen** folder (the one that
  contains `LIVE`, `PTU`, `TECH-PREVIEW`, …) and CSR finds every channel for you.
  Selecting a single `LIVE` folder still works, and CSR discovers its siblings.
- **In-universe "flight-recorder" terminal.** While CSR scans your logs, the
  console now looks the part — a Star Citizen shipboard readout with a block-art
  CSR banner, HUD cyan/amber colours, a live progress bar, and in-universe status
  lines ("ship registry synced", "decrypting flight telemetry", "service record
  compiled"). Degrades to clean plain text when output isn't a real terminal.
- **"Limited data" notice.** Some game builds (seen on certain 4.10 PTU builds)
  log far less than usual — playtime is recorded but weapons/kills/ships/missions
  aren't. CSR now detects those sessions and shows a clear banner on the affected
  channel/patch so an empty-looking session is self-explanatory rather than
  looking like a bug.

### Changed
- **Consistent CSR naming.** The generated page is now `CSR.html` (was
  `sc_stats.html`) and console messages carry a `[CSR]` tag.
- **Account dropdown always shown** (even with a single account, for consistency
  across channels) instead of hiding when there was only one.
- **Channel is now read from each log's own `Executable:` path**, not from the
  folder the log sits in. This is the ground truth for where a session actually
  ran, so attribution is correct even when logs get copied between channel
  folders (the common "copy the install to seed PTU" trick).
- Footer log-count reflects the active channel when more than one exists.

### Fixed
- **Duplicate sessions are deduplicated.** Copying your game folder between
  channels used to risk the same session being counted more than once. Each
  session now carries a fingerprint (start + end timestamp + line count) and
  byte-identical copies collapse to one — no matter how many folders they appear
  in. (On the reference install this removed 2,372 duplicate log copies.)
- Sessions that ran on PTU / TP / Hotfix but whose logs were copied into the LIVE
  folder are no longer mis-counted as LIVE.
- **Account detection on reduced-logging builds.** When a build omits the normal
  login line, CSR now recovers your handle from the local player's own inventory
  records, so those sessions still attribute to your account instead of showing
  as unknown.
- **Ship sub-parts and NPC/AI craft no longer count as ships you've flown.**
  Things like a Drake command module or a Vanduul AI ship used to slip into the
  "ships flown" list (with no thumbnail); they're now filtered out.
- **Better names for brand-new weapons.** Recognises the Hedeby Gunworks
  **Arlington** rifle (4.10), and any still-unknown weapon now shows its
  manufacturer (e.g. "Hedeby Gunworks Ballistic Rifle") instead of a bare,
  plausible-but-wrong generic name. Note: art for items too new to appear in the
  public game-data sources will still be a placeholder until they're published.

### Notes
- **Existing users don't need to re-onboard.** A saved `LIVE` path automatically
  picks up your PTU / TP / Hotfix channels on the next data refresh.

## [1.0.0] — 2026-07-25

Initial public release.

### Added
- Self-contained, offline HTML career dashboard generated from your Star Citizen
  `Game.log` history — Overview, Flight, Combat (Ship + FPS), Missions, Travel,
  Economy, Activity, and Orgs & Profile.
- Per-account and per-patch filtering; light / dark theme toggle.
- Local-app mode (`--serve`): first-run onboarding with a native folder picker,
  and an in-page **Refresh** button to re-scan your logs.
- Ship and weapon artwork fetched from public sources and cached locally so it is
  only downloaded once.
- Packaged as a single portable **CSR.exe** (no install, no admin, no Python
  needed); source is open for anyone to verify or rebuild.
- Runs entirely on your PC — nothing about your account leaves your machine
  except fetching public game artwork and your public RSI profile. Fan-made,
  not affiliated with Cloud Imperium Games or Roberts Space Industries.

[1.2.0]: https://github.com/
[1.1.0]: https://github.com/
[1.0.0]: https://github.com/
