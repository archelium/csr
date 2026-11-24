# CSR — Privacy

**Short version: CSR runs entirely on your computer and collects nothing.**

CSR (Citizen Service Record) is a local desktop tool. It reads your own Star
Citizen log files and shows you statistics from them. It is not a web service and
has no account, login, server, database, analytics, or telemetry.

## What CSR reads
- Your local **Star Citizen client logs** (`Game.log` and the `logbackups` folder)
  from the `StarCitizen\LIVE` folder you select.
- The folder path you choose is saved locally in `csr_config.json` so you don't
  have to pick it again.
- The game's **telemetry stream** on your own PC (`127.0.0.1:2942`), while you play.
  Recording it needs one line in your channel's `user.cfg`
  (`grpc.client.server.enabled = true`). CSR adds it only when you click **Enable**,
  and keeps a `.csr-bak` copy of the file first.

## What CSR sends over the internet
Only to fetch **public game data and artwork** so names and images look right.
While it is **building the dashboard**, CSR contacts:
- **robertsspaceindustries.com** — the RSI Ship Matrix (ship names/art) and your
  **public** RSI Citizen profile & organizations page (the same info anyone can
  see by visiting your public profile).
- **api.star-citizen.wiki** / **starcitizen.tools** — item names and weapon images.
- **xparrow-lite.pages.dev** — the list of item icons it hosts. Only on *Update now*,
  since CSR ships with a copy.
- **fonts.googleapis.com / fonts.gstatic.com** — the display fonts (fetched once,
  then cached locally; the generated page embeds them and needs no further access).

While you **view the dashboard**, your browser loads ship, gun, item and place pictures
straight from **media.robertsspaceindustries.com**, **media.starcitizen.tools** and, for
a few items, **cstone.space** and **xparrow-lite.pages.dev**. These are plain image requests. Like any web image, those
sites see your IP address. Nothing about you or your stats is in them. Offline, the page
shows icons in place of pictures.

CSR sends **no personal data, no telemetry, and no usage information** to anyone.
Game telemetry recordings stay on your PC.
Your logs, your handle, and your stats never leave your machine. The generated
`CSR.html` is a normal file on your disk — share it only if you choose to.

## What CSR stores
Locally only, in `%LOCALAPPDATA%\CSR` (or next to the program in the Portable
edition): the generated `CSR.html`, cached game-data files, `csr_config.json`, and
`csr_prefs.json` (your pinned blueprints and main accounts), `slate_last_ship.json` (the
last ship Kestrel saw), and `csr.log` (what CSR
did on its last two runs). Delete that folder to remove everything. If you turn on
**Start with Windows**, CSR adds a `CSR` entry under
`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`. Turning it off
removes it.

Telemetry recordings go to a `telemetry` folder there, one compressed file per session. The
**Space limit** and **Keep recordings** settings delete old ones. **Delete all
recordings** removes them.

## Tablet & phone access
Off by default. When you turn it on, CSR listens on your local network on one port
between 7890 and 7899 and serves the Kestrel K-7 slate only. Every request needs the
private key in the link or QR code. **New link** replaces the key and old links stop
working. Nothing is reachable from outside your network unless you forward that port
on your router.

## A note on your public RSI profile
CSR shows the same public profile and organization data that **anyone** can see on
your RSI page — including affiliate orgs. If you don't want that visible, set those
to private on the RSI website; CSR only reflects what RSI already makes public.

## Contact
Questions: **support@archelium.com**

_Last updated: 2026-11-25 · CSR v2.0.0-beta_
