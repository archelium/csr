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

## What CSR sends over the internet
Only to fetch **public game data and artwork** so names and images look right.
While it is **building the dashboard**, CSR contacts:
- **robertsspaceindustries.com** — the RSI Ship Matrix (ship names/art) and your
  **public** RSI Citizen profile & organizations page (the same info anyone can
  see by visiting your public profile).
- **api.star-citizen.wiki** / **starcitizen.tools** — item names and weapon images.
- **fonts.googleapis.com / fonts.gstatic.com** — the display fonts (fetched once,
  then cached locally; the generated page embeds them and needs no further access).

While you **view the dashboard**, your browser loads ship, gun, item and place pictures
straight from **media.robertsspaceindustries.com**, **media.starcitizen.tools** and, for
a few items, **cstone.space**. These are plain image requests. Like any web image, those
sites see your IP address. Nothing about you or your stats is in them. Offline, the page
shows icons in place of pictures.

CSR sends **no personal data, no telemetry, and no usage information** to anyone.
Your logs, your handle, and your stats never leave your machine. The generated
`CSR.html` is a normal file on your disk — share it only if you choose to.

## What CSR stores
Locally only, in `%LOCALAPPDATA%\CSR` (or next to the program in the Portable
edition): the generated `CSR.html`, cached game-data files, and
`csr_config.json`. Delete that folder to remove everything.

## A note on your public RSI profile
CSR shows the same public profile and organization data that **anyone** can see on
your RSI page — including affiliate orgs. If you don't want that visible, set those
to private on the RSI website; CSR only reflects what RSI already makes public.

## Contact
Questions: **support@archelium.com**

_Last updated: 2026-11-23 · CSR v1.10.0_
