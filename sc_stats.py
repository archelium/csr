#!/usr/bin/env python3
"""
CSR — Citizen Service Record  —  career dashboard for Star Citizen
==================================================================
Scans your entire Game.log history (the LIVE\\logbackups folder + the current
Game.log), extracts everything that's actually recorded, groups it by major
patch (4.1, 4.2, ... 4.9), and writes a single self-contained, interactive
web page: CSR.html  (no server, no dependencies — just open it).

What it CAN report (things SC writes to the client log):
  * Playtime — total, per patch, per month, sessions, avg/longest, active days
  * Ships flown  (from vehicle control-token events)  + your most-flown
  * Missions — completed / failed / abandoned, completion rate
  * Deaths (ship-destruction deaths — the only kind SC logs in 2026 builds)
  * Quantum-travel activity, hour-of-day / day-of-week play patterns
  * Most-drawn weapons & tools + reloads & carry (loadout preference)

What it CANNOT (SC no longer writes these to the client log):
  * Kills, K/D, accuracy, shots fired, damage — all server-side now.

Usage:
    python sc_stats.py                 # auto-detect logs, write CSR.html
    python sc_stats.py --open          # also open it in your browser
    python sc_stats.py --logs "D:\\...\\LIVE\\logbackups"
"""

import argparse
import glob
import html
import json
import os
import re
import sys
import time
import shutil
import subprocess
import threading
import webbrowser
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone

import sc_names

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CSR_VERSION = "1.4.0"
CSR_CONTACT = "support@archelium.com"      # publisher/contact (archelium.com)
# Bump whenever scan_log() learns to extract something new or fixes an extraction bug.
# A quick refresh reuses archived sessions only when they were parsed by THIS version,
# so improved parsing always re-reads old logs instead of silently keeping stale numbers.
PARSER_VERSION = 15


# --------------------------------------------------------------------------- #
#  Terminal UI  —  an in-universe "flight-recorder" console
#
#  Colours + block art dress the scan output up like a Star Citizen shipboard
#  terminal. Pure stdlib (ANSI escapes + Unicode blocks). Everything degrades to
#  clean plain text when stdout isn't a real terminal (pipes, the captured output
#  of a redirected run) or when NO_COLOR is set — so nothing ever breaks.
# --------------------------------------------------------------------------- #

# SC-HUD palette (truecolor; mirrors the dashboard --cyan / --amber)
_CY = "\033[38;2;91;209;230m"      # cyan   (primary)
_AMB = "\033[38;2;240;180;60m"     # amber  (accent / caution)
_GRN = "\033[38;2;120;222;150m"    # green  (ok)
_RED = "\033[38;2;235;110;110m"    # red    (alert)
_DIM = "\033[38;2;120;140;158m"    # slate  (secondary)
_WHT = "\033[38;2;228;237;244m"    # near-white (emphasis)
_BOLD = "\033[1m"
_RST = "\033[0m"

_ANSI = None


def _console_ready():
    """True only if we may emit the full ANSI + Unicode-block experience: stdout
    is a real terminal, colour isn't disabled, AND the console can encode UTF-8
    (block art). Enables VT processing on Windows. Cached. When False, every
    helper falls back to plain ASCII, so nothing ever raises or mojibakes."""
    global _ANSI
    if _ANSI is not None:
        return _ANSI
    # belt-and-suspenders: never let a stray glyph crash a run — keep the
    # console's encoding but replace anything it can't encode instead of raising.
    for _st in (sys.stdout, sys.stderr):
        try:
            _st.reconfigure(errors="replace")
        except Exception:
            pass
    ok = False
    try:
        ok = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    except Exception:
        ok = False
    if ok:
        # require UTF-8 out (the block art needs it); try to switch, else bail
        enc = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "")
        if enc not in ("utf8", "utf8mb4"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                ok = False
        if ok and os.name == "nt":
            try:
                import ctypes
                k = ctypes.windll.kernel32
                h = k.GetStdHandle(-11)
                mode = ctypes.c_uint()
                k.GetConsoleMode(h, ctypes.byref(mode))
                k.SetConsoleMode(h, mode.value | 0x0004)  # VIRTUAL_TERMINAL_PROCESSING
            except Exception:
                ok = False
    _ANSI = bool(ok)
    return _ANSI


def _paint(s, col):
    return f"{col}{s}{_RST}" if _console_ready() else s


def _g(fancy, plain):
    """Pick a Unicode glyph when the console supports it, else an ASCII stand-in."""
    return fancy if _console_ready() else plain


def clog(msg, col=_CY, tag="CSR", stream=None):
    """One themed console line:  [CSR] <msg>."""
    line = f"{_paint('[' + tag + ']', col)} {msg}"
    if _TUI["on"] and stream is None:
        tui_log(line)
        return
    print(line, file=stream or sys.stdout)


def cstep(msg):
    clog(f"{_paint(_g('▸', '>'), _CY)} {msg}")


def csub(msg):
    """A dim, indented sub-line under a step."""
    line = f"      {_paint(msg, _DIM)}"
    if _TUI["on"]:
        tui_log(line)
        return
    print(line)


def cok(msg):
    clog(f"{_paint(_g('✔', 'OK'), _GRN)} {_paint(msg, _WHT)}", _GRN)


def cwarn(msg):
    clog(f"{_paint(_g('▲', '!'), _AMB)} {msg}", _AMB)


def cerr(msg):
    clog(f"{_paint(_g('✖', 'x'), _RED)} {msg}", _RED, stream=sys.stderr)


def csr_banner():
    """The boot banner: 'CSR' in block art beside the in-universe title."""
    art = [
        " ██████╗███████╗██████╗ ",
        "██╔════╝██╔════╝██╔══██╗",
        "██║     ███████╗██████╔╝",
        "██║     ╚════██║██╔══██╗",
        "╚██████╗███████║██║  ██║",
        " ╚═════╝╚══════╝╚═╝  ╚═╝",
    ]
    side = [
        (_WHT + _BOLD, "CITIZEN SERVICE RECORD"),
        (_DIM, "─" * 30),
        (_CY, f"flight-recorder terminal   ·   v{CSR_VERSION}"),
        (_DIM, "your logs never leave this machine"),
        (_DIM, "fan-made · not affiliated with CIG / RSI"),
        (_AMB, f"uplink · {CSR_CONTACT.split('@')[-1]}"),
    ]
    if not _console_ready():
        print(f"\n  CSR - Citizen Service Record - flight-recorder terminal - v{CSR_VERSION}\n")
        return
    print()
    for i, row in enumerate(art):
        col, txt = side[i]
        print(f"  {_CY}{row}{_RST}   {col}{txt}{_RST}")
    print()


# --------------------------------------------------------------------------- #
#  Console TUI — a fixed frame instead of an endless scroll
# --------------------------------------------------------------------------- #
# The scan used to spool hundreds of lines up the window, so the header and the URL
# vanished within seconds. This keeps a StarLogs-style layout: a title box that stays
# put, a bordered pane whose contents scroll internally, and a status bar pinned to
# the bottom. It only engages on a real ANSI terminal — piped or redirected output
# falls straight through to plain lines, which is what the tests and CI see.
_TUI = {"on": False, "lines": [], "url": "", "chan": "", "status": "", "prog": None}
TUI_ROWS = 12                                  # log lines kept in the pane


def _vis_len(s):
    """Length of a string as it appears on screen (ANSI colour codes are invisible)."""
    return len(re.sub(r"\x1b\[[0-9;]*m", "", s))


def _fit(s, w):
    """Clip to w visible columns, keeping colour codes intact and closing them off."""
    if _vis_len(s) <= w:
        return s
    out, seen = [], 0
    i = 0
    while i < len(s) and seen < w - 1:
        if s[i] == "\x1b":
            j = s.find("m", i)
            if j == -1:
                break
            out.append(s[i:j + 1])
            i = j + 1
            continue
        out.append(s[i])
        seen += 1
        i += 1
    clipped = "".join(out) + "…"
    return clipped + (_RST if "" in clipped else "")


def tui_frame(w, h, url, chan, lines, status, prog, color=True):
    """Render the whole console as a list of rows. Pure — no I/O — so it can be
    unit-tested at any size without a terminal."""
    C = (lambda s, c: f"{c}{s}{_RST}") if color else (lambda s, c: s)
    w = max(46, min(w, 160))
    inner = w - 2
    rows = []

    def box_top(label, right=""):
        lab = f" {label} "
        pad = inner - len(lab) - (len(right) + 2 if right else 0)
        r = f"┌{C(lab, _CY)}{'─' * max(0, pad)}"
        if right:
            r += f"{C(' ' + right + ' ', _DIM)}"
        return r + "┐"

    def box_bot(right=""):
        if not right:
            return "└" + "─" * inner + "┘"
        lab = f" {right} "
        return "└" + "─" * max(0, inner - len(lab) - 2) + C(lab, _DIM) + "──┘"

    def row(s):
        return "│" + s + " " * max(0, inner - _vis_len(s)) + "│"

    rows.append(box_top(f"CSR · CITIZEN SERVICE RECORD", f"v{CSR_VERSION}"))
    rows.append(row(" " + C(url or "starting…", _CY) +
                    ("   " + C(chan, _DIM) if chan else "")))
    rows.append("└" + "─" * inner + "┘")

    body = max(3, min(TUI_ROWS, h - 9))          # pane height: buffer size, or less if the window is short
    keep = lines[-body:]
    rows.append(box_top("TELEMETRY"))
    for ln in keep:
        rows.append(row(" " + _fit(ln, inner - 2)))
    for _ in range(body - len(keep)):
        rows.append(row(""))
    rows.append(box_bot(f"{len(keep)}/{TUI_ROWS} lines"))

    if prog:
        done, total, label = prog
        frac = min(done / max(total, 1), 1.0)
        barw = max(10, inner - 34)
        fill = int(barw * frac)
        bar = C("█" * fill, _CY) + C("░" * (barw - fill), _DIM)
        rows.append(" " + bar + C(f" {frac * 100:3.0f}%", _AMB) +
                    C(f"  {label} {done}/{total}", _DIM))
    else:
        rows.append(" " + C(status or "", _DIM))
    rows.append(" " + C("Ctrl+C to power down · or use the status light in the page", _DIM))
    return rows


def tui_enable(url, chan):
    _TUI.update(on=_console_ready(), url=url, chan=chan)
    if _TUI["on"]:
        sys.stdout.write("\x1b[?25l")          # hide the cursor while we own the screen
        tui_render()


def tui_disable():
    if _TUI["on"]:
        _TUI["on"] = False
        try:
            sys.stdout.write("\x1b[?25h\n")    # cursor back
            sys.stdout.flush()
        except Exception:
            pass


def tui_log(line):
    _TUI["lines"].append(line)
    del _TUI["lines"][:-TUI_ROWS]
    tui_render()


def tui_render():
    if not _TUI["on"]:
        return
    try:
        size = shutil.get_terminal_size((90, 26))
        rows = tui_frame(size.columns, size.lines, _TUI["url"], _TUI["chan"],
                         _TUI["lines"], _TUI["status"], _TUI["prog"])
        out = ["\x1b[H"]                        # home, then clear each line as we draw
        for r in rows:
            out.append(r + "\x1b[K\n")
        out.append("\x1b[J")                    # wipe anything below the frame
        sys.stdout.write("".join(out))
        sys.stdout.flush()
    except Exception:
        _TUI["on"] = False                      # never let drawing break the scan


def cprogress(done, total, label="parsing telemetry"):
    """In-place HUD progress bar during the scan (plain periodic prints if no TTY)."""
    total = max(total, 1)
    frac = min(done / total, 1.0)
    if _TUI["on"]:
        # the frame owns the bottom bar — just update it and redraw in place
        _TUI["prog"] = None if done >= total else (done, total, label)
        tui_render()
        return
    if not _console_ready():
        if done % 400 == 0 or done >= total:
            print(f"[CSR]   {label}: {done}/{total}")
        return
    width = 26
    fill = int(frac * width)
    bar = _CY + "█" * fill + _DIM + "░" * (width - fill) + _RST
    pct = f"{_AMB}{int(frac * 100):3d}%{_RST}"
    sys.stdout.write(f"\r  {_paint('▐', _DIM)}{bar}{_paint('▌', _DIM)} {pct}  "
                     f"{_DIM}{label} {done}/{total}{_RST}   ")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


# Live scan progress the served /api/status endpoint reports to the browser's
# loading terminal, so its bar reflects real work (0..1) instead of a fake creep.
_PROGRESS = {"frac": 0.0, "phase": "idle", "done": 0, "total": 0}


# --------------------------------------------------------------------------- #
#  Console window (Windows) — CSR is driven from the browser, so the black box
#  behind it is just clutter once the scan is done. It can be hidden and brought
#  back on demand; everything it printed is still there when you show it again.
# --------------------------------------------------------------------------- #
_CONSOLE_HIDDEN = False


def _console_hwnd():
    if os.name != "nt":
        return None
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetConsoleWindow()
        return h or None
    except Exception:
        return None


def console_visible():
    """Ask Windows whether our console window is actually on screen."""
    h = _console_hwnd()
    if not h:
        return None
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsWindowVisible(h))
    except Exception:
        return None


def show_console(visible):
    """Show/hide our own console window. Returns True if the state was applied."""
    global _CONSOLE_HIDDEN
    h = _console_hwnd()
    if not h:
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        # SW_HIDE / SW_SHOWNORMAL — SHOWNORMAL also un-minimises, so a console the
        # user had minimised before hiding comes back properly rather than staying gone
        u.ShowWindow(h, 1 if visible else 0)
        if visible:
            u.SetForegroundWindow(h)
        _CONSOLE_HIDDEN = not visible
        return True
    except Exception:
        return False


def set_progress(frac, phase, done=0, total=0):
    _PROGRESS.update(frac=max(0.0, min(1.0, frac)), phase=phase, done=done, total=total)


# --------------------------------------------------------------------------- #
#  Paths — split read-only bundled resources from a writable app-data dir so
#  the same code works when run as a script AND when frozen into a PyInstaller
#  one-file exe (where the script dir is a read-only temp extraction).
# --------------------------------------------------------------------------- #

def is_frozen():
    return getattr(sys, "frozen", False)


def resource_dir():
    """Where bundled read-only data lives (PyInstaller _MEIPASS, else script dir)."""
    return getattr(sys, "_MEIPASS", SCRIPT_DIR)


def _writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        t = os.path.join(path, ".wtest")
        open(t, "w").close()
        os.remove(t)
        return True
    except Exception:
        return False


_APP_DIR = None


def app_dir():
    """Writable dir for config, caches and the generated page.

    The exe is self-contained/portable: it keeps its data in a CSR-data folder
    NEXT TO itself — delete that folder to wipe it, or run the whole thing from a
    USB stick with no trace left on the host. If the exe sits somewhere read-only
    (e.g. Program Files), it falls back to %LOCALAPPDATA%\\CSR."""
    global _APP_DIR
    if _APP_DIR:
        return _APP_DIR
    if is_frozen():
        beside = os.path.join(os.path.dirname(sys.executable), "CSR-data")
        _APP_DIR = beside if _writable(beside) else \
            os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "CSR")
    else:
        _APP_DIR = SCRIPT_DIR
    os.makedirs(_APP_DIR, exist_ok=True)
    return _APP_DIR


CONFIG_NAME = "csr_config.json"


def config_path():
    return os.path.join(app_dir(), CONFIG_NAME)


def load_config():
    try:
        return json.load(open(config_path(), encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    try:
        json.dump(cfg, open(config_path(), "w", encoding="utf-8"), indent=2)
    except Exception as e:
        cerr(f"could not save config: {e}")


def cache_file(name):
    """A cache path in app_dir, seeded once from a bundled copy if present
    (so a frozen exe ships with game-data caches but writes fresh ones)."""
    dst = os.path.join(app_dir(), name)
    if not os.path.exists(dst):
        src = os.path.join(resource_dir(), name)
        if os.path.isfile(src) and os.path.abspath(src) != os.path.abspath(dst):
            try:
                import shutil
                shutil.copyfile(src, dst)
            except Exception:
                pass
    return dst


# Resolvers loaded once in main()/serve() with app-data cache paths.
SHIPS = sc_names.ShipIndex()
ITEMS = sc_names.ItemIndex()
BPS = sc_names.BlueprintIndex()

# --------------------------------------------------------------------------- #
#  Log discovery
# --------------------------------------------------------------------------- #

# Star Citizen release channels: (folder name, display label), in priority order.
# Career stats default to LIVE; PTU / TP / etc. are kept as separate datasets and
# reachable via the channel dropdown. EPTU = Evocati/early PTU; TECH-PREVIEW = TP.
SC_CHANNELS = [
    ("LIVE", "LIVE"),
    ("PTU", "PTU"),
    ("EPTU", "EPTU"),
    ("TECH-PREVIEW", "TP"),
    ("HOTFIX", "HOTFIX"),
]

# Channels folded into another for career purposes. HOTFIX is a live-service
# emergency-patch branch (real live play on a temporary build), so its sessions
# count toward the LIVE career rather than showing as a separate channel.
CHANNEL_ALIAS = {"HOTFIX": "LIVE"}


def _channel_label(folder_name):
    up = (folder_name or "").upper()
    for folder, label in SC_CHANNELS:
        if up == folder:
            return label
    return up or "LIVE"


def _channel_files(folder):
    """(files, live) for one channel folder (…/LIVE): its logbackups + Game.log."""
    lb = os.path.join(folder, "logbackups")
    files = glob.glob(os.path.join(lb, "*.log")) if os.path.isdir(lb) else []
    live = os.path.join(folder, "Game.log")
    return files, (live if os.path.isfile(live) else None)


def _has_subchannels(path):
    """True if `path` contains recognised channel subfolders (LIVE/PTU/…)."""
    for folder, _ in SC_CHANNELS:
        f, l = _channel_files(os.path.join(path, folder))
        if f or l:
            return True
    return False


def _is_sc_folder(path):
    """True if `path` is a channel folder (has logbackups/Game.log) OR a
    StarCitizen root that contains channel subfolders (LIVE/PTU/…)."""
    f, l = _channel_files(path)
    return bool(f or l) or _has_subchannels(path)


def _sc_root(path):
    """Canonical StarCitizen root for a picked path. If it already holds channel
    subfolders it IS the root; if it's a single channel folder (…/LIVE) the root
    is its parent (so siblings like PTU/TECH-PREVIEW get discovered too)."""
    if _has_subchannels(path):
        return path
    f, l = _channel_files(path)
    if f or l:
        return os.path.dirname(os.path.normpath(path))
    # A saved channel folder can VANISH during normal play. CIG's own procedure for
    # applying a hotfix is to rename LIVE to HOTFIX, patch through the launcher, then
    # rename it back — so a path stored as …\StarCitizen\LIVE does not exist for the
    # length of that window. Without this, CSR found NO channels at all during it, not
    # even PTU, which never moved. Climb to the parent when the parent is a root.
    parent = os.path.dirname(os.path.normpath(path))
    if parent and parent != path and _has_subchannels(parent):
        return parent
    return path


def _channels_under(root):
    """Ordered (label, files, live) for every channel under `root` (LIVE first).
    `root` may also itself be a lone channel folder."""
    found, seen = [], set()
    f, l = _channel_files(root)
    if f or l:                                    # root is itself a channel folder
        lab = _channel_label(os.path.basename(os.path.normpath(root)))
        found.append((lab, f, l))
        seen.add(lab)
    for folder, label in SC_CHANNELS:
        if label in seen:
            continue
        f, l = _channel_files(os.path.join(root, folder))
        if f or l:
            found.append((label, f, l))
            seen.add(label)
    pr = {label: i for i, (_, label) in enumerate(SC_CHANNELS)}
    found.sort(key=lambda t: pr.get(t[0], 99))    # LIVE first (dedup priority)
    return found


def find_logs_recursive(root, cap=20000):
    """Every *.log anywhere under `root`, for importing a folder someone copied off
    another PC. They rarely preserve the `StarCitizen\\LIVE\\logbackups` layout — it
    might be a bare pile of logs, or nested a few folders deep — so structure isn't
    required. Channel attribution doesn't need it either: each log names its own
    channel in its `Executable:` line, so LIVE/PTU/TP still separate correctly."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.lower().endswith(".log"):
                out.append(os.path.join(dirpath, fn))
                if len(out) >= cap:
                    return out
    return out


def resolve_channels(configured=None, explicit_logs=None, strict=False):
    """Return an ordered list of (label, files, live) — one entry per SC channel.

    `configured` is the folder the user picked in onboarding; it may be the
    StarCitizen root (containing LIVE / PTU / TECH-PREVIEW) OR a single channel
    folder (…/LIVE) — both work. `explicit_logs` (a --logs logbackups path)
    forces a single LIVE channel. Channels come back LIVE-first so cross-folder
    duplicate sessions dedup under LIVE downstream.

    `strict` disables the auto-detect fallback and looks ONLY at `configured`.
    Importing a folder copied from another PC must never silently fall back to
    THIS machine's install — that would rescan your own logs and report an import
    of zero, which looks like the copied folder was empty."""
    if explicit_logs and os.path.isdir(explicit_logs):
        live = os.path.join(os.path.dirname(os.path.normpath(explicit_logs)), "Game.log")
        return [("LIVE", glob.glob(os.path.join(explicit_logs, "*.log")),
                 live if os.path.isfile(live) else None)]

    roots = []
    if configured and os.path.isdir(configured):
        roots.append(_sc_root(configured))
    if strict:
        for root in roots:
            found = _channels_under(root) if root else None
            if found:
                return found
        return []
    for cfg in (os.path.join(SCRIPT_DIR, "starlogs_config.json"),
                os.path.join(SCRIPT_DIR, "StarLogs-v0.9.1", "starlogs_config.json")):
        if os.path.isfile(cfg):
            try:
                for entry in json.load(open(cfg, encoding="utf-8")).get("installations", {}).values():
                    p = entry.get("path")
                    if p and os.path.isdir(p):
                        roots.append(_sc_root(p))
            except Exception:
                pass
    for drive in ("C:", "D:", "E:"):
        base = rf"{drive}\Program Files\Roberts Space Industries\StarCitizen"
        roots.append(base)
        roots.append(os.path.join(base, "StarCitizen"))           # some installs nest it twice

    for root in roots:
        if root and os.path.isdir(root):
            found = _channels_under(root)
            if found:
                return found
    return []


# --------------------------------------------------------------------------- #
#  Graphics settings  —  read (never written) from the channel's own profile
# --------------------------------------------------------------------------- #

SETTINGS_REL = os.path.join("user", "client", "0", "Profiles", "default", "attributes.xml")
_ATTR_RE = re.compile(r'<Attr\s+name="([^"]+)"\s+value="([^"]*)"')
#: the settings worth surfacing. Translated into tier names on the JS side (_SETSCALE):
#: the numbers are positions on one global 1–5 scale, but each setting exposes its own
#: subset of it in the menu, so the mapping is per-setting.
_SETTING_KEYS = (
    # "SysSpec" is the MASTER preset — the only quality value CSR will ever write. The
    # per-feature entries below are read for display only: each has its own ceiling
    # (this machine sits at 3, 4 and 5 simultaneously under AutoDetect), and writing one
    # would mean deciding what the rest should become.
    "SysSpec", "AutoDetect", "Width", "Height", "WindowMode", "MotionBlur",
    "Upscaling", "UpscalingTechnique", "UpscalingModel",
    "SysSpec_TextureQuality", "SysSpec_TextureDetail", "SysSpec_TextureFiltering",
    "SysSpec_TextureGround", "SysSpec_ShadowMaps", "SysSpec_ShadowScreenSpace",
    "SysSpec_ObjectDetail", "SysSpec_ObjectViewDistance",
    "SysSpec_PlanetVolumetricClouds",
    "SysSpec_GasCloud", "SysSpec_Fog", "SysSpec_PostProcessing",
    "SysSpec_Shading", "SysSpec_VideoComms", "SysSpec_WaterSim", "SysSpec_WaterCaustics",
    # Deliberately absent: SysSpec_Particles, SysSpec_PlanetTerrainVirtualTextures and
    # the legacy no-underscore SysSpecPlanetVolumetricClouds. attributes.xml stores
    # them, but the Graphics menu has no row for any of them — so CSR can neither name
    # their tiers with confidence nor point you at a slider to change them.
)
#: settings live beside the install, NOT in the log folder, so they exist only for
#: channels on THIS machine — never for logs imported from another PC.
_SETTINGS_CACHE = {}


def read_settings(channel_dir):
    """Parsed attributes.xml for one channel folder, or None. Read-only, always:
    CSR never writes game config — altering files under an anti-cheat is not a risk
    worth taking for a stats tool."""
    if not channel_dir:
        return None
    path = os.path.join(channel_dir, SETTINGS_REL)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = dict(_ATTR_RE.findall(fh.read()))
    except OSError:
        return None
    out = {k: raw[k] for k in _SETTING_KEYS if k in raw}
    return out or None


def _channel_dir(files, live):
    """The channel's install folder, from its live Game.log or logbackups path."""
    if live:
        return os.path.dirname(os.path.normpath(live))
    if files:
        return os.path.dirname(os.path.dirname(os.path.normpath(files[0])))
    return None


#: channel label -> install folder, so a write can find the file again later
_CHANNEL_DIRS = {}


# --------------------------------------------------------------------------- #
#  Live event watcher  —  tails each channel's Game.log while you play and
#  raises a crash / disconnect event the dashboard can show within seconds.
# --------------------------------------------------------------------------- #

#: Star Citizen's crash artefacts. The folder is rewritten on every CTD and holds a
#: screenshot of the moment it happened, so it doubles as an independent trigger.
def crashes_dir():
    base = os.environ.get("LOCALAPPDATA")
    return os.path.join(base, "Star Citizen", "Crashes") if base else None


_WATCH = {
    "on": False,
    "tails": {},        # log path -> byte offset already scanned
    "events": [],       # newest last; bounded
    "seq": 0,
    "crash_stamp": None,   # mtime of the Crashes folder, for the second trigger
    "live": False,         # a game session is currently writing
    "checked": {},         # channel -> {ts, size, exists} so the UI can show activity
    "started": None,
    "raised": 0,           # total events raised since start
    "tests": 0,            # synthetic events fired from the dashboard
    "lock": threading.Lock(),
    "boot_pending": {},  # channel -> (time seen, is_test); see _scan_chunk
    "game": None,        # is StarCitizen.exe running? None until first checked
}
WATCH_FAST = 2.0        # seconds between checks while a session is writing
WATCH_IDLE = 15.0       # …and while nothing is running
WATCH_LIVE_GAP = 30     # log untouched for this long => session no longer live

#  ---- is Star Citizen actually running? ------------------------------------------
#  A log's timestamp alone can't answer this. The game's shutdown is drawn out — it
#  writes its closing blocks, then the process lingers — so for ~30 s after you quit,
#  "last written 4 s ago" still looks exactly like an active session. Asking Windows
#  for the process list settles it immediately.
#
#  This reads NOTHING from the game. It takes the same process-name snapshot Task
#  Manager shows, needs no elevation, and deliberately never calls OpenProcess — a
#  handle INTO StarCitizen.exe is the thing an anti-cheat has an opinion about, and
#  CSR has no reason to want one. The cost of a snapshot is well under a millisecond.
SC_EXE = "starcitizen.exe"
_TH32CS_SNAPPROCESS = 0x00000002
_PROC_CACHE = {"ts": 0.0, "on": None}      # (checked at, running?) — None = unknown
_PROC_TTL = 1.5                            # a snapshot is cheap, but not free


def _proc_api():
    """(kernel32, PROCESSENTRY32W), built once and cached.

    The struct has to be created ONCE rather than per call: ctypes binds argtypes to
    the exact class object, so a second — even byte-identical — class definition makes
    every later call raise ArgumentError. Cached separately from the answer so a
    failure to build it is remembered as "can't ask" rather than retried every poll."""
    if "api" in _PROC_CACHE:
        return _PROC_CACHE["api"]
    api = None
    try:
        import ctypes
        from ctypes import wintypes

        class _PE32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # the default restype is a 32-bit int, which truncates a 64-bit HANDLE
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32W)]
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32W)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        api = (ctypes, k32, _PE32W)
    except Exception:
        api = None
    _PROC_CACHE["api"] = api
    return api


def _sc_process_running():
    """True/False if Windows could be asked, None if it couldn't (non-Windows, or the
    API refused). None means 'don\'t know' and every caller falls back to log mtime."""
    now = time.time()
    if now - _PROC_CACHE["ts"] < _PROC_TTL:
        return _PROC_CACHE["on"]
    _PROC_CACHE["ts"] = now
    api = _proc_api() if os.name == "nt" else None
    if api is None:
        _PROC_CACHE["on"] = None
        return None
    ctypes, k32, _PE32W = api
    try:
        snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            _PROC_CACHE["on"] = None
            return None
        try:
            e = _PE32W()
            e.dwSize = ctypes.sizeof(_PE32W)
            found = False
            ok = k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.szExeFile.lower() == SC_EXE:
                    found = True
                    break
                ok = k32.Process32NextW(snap, ctypes.byref(e))
        finally:
            k32.CloseHandle(snap)
        _PROC_CACHE["on"] = found
        return found
    except Exception:
        _PROC_CACHE["on"] = None
        return None

        class _PE32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_wchar * 260)]

        k32 = _PROC_CACHE.get("k32")
        if k32 is None:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            # the default restype is a 32-bit int, which truncates a 64-bit HANDLE
            k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32W)]
            k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PE32W)]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            _PROC_CACHE["k32"] = k32
        snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            _PROC_CACHE["on"] = None
            return None
        try:
            e = _PE32W()
            e.dwSize = ctypes.sizeof(_PE32W)
            found = False
            ok = k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.szExeFile.lower() == SC_EXE:
                    found = True
                    break
                ok = k32.Process32NextW(snap, ctypes.byref(e))
        finally:
            k32.CloseHandle(snap)
        _PROC_CACHE["on"] = found
        return found
    except Exception:
        _PROC_CACHE["on"] = None
        return None
MAX_EVENTS = 40


def _push_event(kind, severity, title, detail, extra=None):
    """Record an event for the dashboard. Deduped on (kind,title) within 60s so a
    crash seen by BOTH the log tail and the Crashes folder is reported once."""
    now = time.time()
    with _WATCH["lock"]:
        for e in reversed(_WATCH["events"]):
            if e["kind"] == kind and e["title"] == title and now - e["ts"] < 60:
                return None
            if now - e["ts"] > 60:
                break
        _WATCH["seq"] += 1
        ev = {"id": _WATCH["seq"], "ts": now, "kind": kind, "severity": severity,
              "title": title, "detail": detail, "seen": False}
        if extra:
            ev.update(extra)
        _WATCH["events"].append(ev)
        _WATCH["raised"] += 1
        del _WATCH["events"][:-MAX_EVENTS]
        return ev


def _scan_chunk(text, channel, test=False):
    """Raise events for anything noteworthy in freshly-appended log text.

    `test` marks synthetic events from the dashboard's buttons. They are never
    written to a log and so never reach the crash history — the flag lets the UI
    say so instead of offering a link to a record that will not contain them."""
    if CRASH_MARK in text:
        c = parse_crash_report(text) or {"exception": "UNKNOWN"}
        exc = c.get("exception") or "UNKNOWN"
        label, actionable, advice = CRASH_KIND.get(
            exc, ("Crash", False, "Star Citizen stopped unexpectedly."))
        _push_event("crash", "bad", f"{label} — {channel}",
                    c.get("gpu_msg") or advice,
                    {"exception": exc, "digest": c.get("digest"), "crash": c,
                     "actionable": actionable, "test": test})
    for code, reason in DISCO_RE.findall(text):
        if code in BENIGN_NET_CODES:
            continue
        meaning = NET_CODE_MEANING.get(code)
        _push_event("net", "warn" if meaning else "info",
                    f"{meaning or 'Disconnected (code ' + code + ')'} — {channel}",
                    f"Star Citizen reported code {code}: {reason.strip() or 'no reason given'}."
                    + (" That is a server-side fault — nothing on your PC caused it."
                       if code in NET_SERVER_SIDE else ""),
                    {"code": code, "test": test})
    # Dropped to the main menu. Live, the "did play continue?" test can't be applied
    # yet — so the drop is only PARKED here. watch_tick() raises it once the log has
    # grown again, which is the player back in the game. If they had simply quit, the
    # log stops and the parked drop is discarded when the next session rotates it.
    # A parked return is confirmed by the client finishing a join to a PU server in a
    # later chunk — the player is back in. Checked before parking, so a fresh event in
    # this chunk cannot confirm itself.
    _pend = _WATCH["boot_pending"].get(channel)
    if _pend and all(k in text for k in PU_JOIN):
        _WATCH["boot_pending"].pop(channel, None)
        _push_event("boot", "warn", f"Back to the main menu — {channel}",
                    "Star Citizen returned you to the front end mid-session and you carried "
                    "on. It logs this as you asking to disconnect whether you did or the "
                    "server dropped you — since the 2026 builds the log cannot tell them apart.",
                    {"test": _pend[1]})
    if BOOT_FRONTEND in text and BOOT_DISCO_RE.search(text):
        cut = BOOT_DISCO_RE.search(text).start()
        if not any(k in text[max(0, cut - 4000):cut] for k in BOOT_INTENT):
            _WATCH["boot_pending"][channel] = (time.time(), test)


def watch_tick():
    """One pass over every watched log. Cheap: stat, then read only new bytes."""
    live = False
    now = time.time()
    # Asked once per tick and shared by every channel. None = couldn't be determined,
    # in which case the log's own timestamp is the only evidence and is used alone.
    game = _sc_process_running()
    _WATCH["game"] = game
    for label, cdir in list(_CHANNEL_DIRS.items()):
        path = os.path.join(cdir, "Game.log")
        try:
            st = os.stat(path)
        except OSError:
            _WATCH["checked"][label] = {"ts": now, "size": 0, "exists": False,
                                        "idle": None, "mtime": None}
            continue
        _WATCH["checked"][label] = {"ts": now, "size": st.st_size, "exists": True,
                                    "idle": round(now - st.st_mtime),
                                    "mtime": st.st_mtime}
        # A fresh timestamp only means a session is live if the game is still there.
        # Without this the ~30 s of shutdown writes read as "reading as you play"
        # after you have already quit — which is exactly what it looked like.
        if now - st.st_mtime < WATCH_LIVE_GAP and game is not False:
            live = True
        off = _WATCH["tails"].get(path)
        if off is None:
            # first sight of this log: start at the end, so starting CSR mid-session
            # doesn't replay an hour of history as "new" alerts
            _WATCH["tails"][path] = st.st_size
            continue
        if st.st_size < off:               # log rotated / new session started
            off = 0
        if st.st_size == off:
            continue
        try:
            with open(path, "rb") as fh:
                fh.seek(off)
                chunk = fh.read(st.st_size - off)
        except OSError:
            continue
        _WATCH["tails"][path] = st.st_size
        # A parked drop is confirmed by the log growing again well after it: the
        # player rejoined. Checked BEFORE this chunk is scanned, so a fresh drop in
        # this same chunk doesn't confirm itself.
        # A parked return that never sees a server join again was a log-off: forget it.
        _pend = _WATCH["boot_pending"].get(label)
        if _pend and now - _pend[0] >= 1200:
            _WATCH["boot_pending"].pop(label, None)
        _scan_chunk(chunk.decode("utf-8", "replace"), label)
    # second, independent trigger: SC's crash folder being rewritten
    cd = crashes_dir()
    if cd:
        try:
            stamp = os.path.getmtime(cd)
        except OSError:
            stamp = None
        if stamp and _WATCH["crash_stamp"] and stamp > _WATCH["crash_stamp"]:
            shot = os.path.join(cd, "screenshot.jpg")
            _push_event("crash", "bad", "Star Citizen crashed",
                        "A crash report was just written by the game.",
                        {"screenshot": os.path.isfile(shot)})
        if stamp:
            _WATCH["crash_stamp"] = stamp
    _WATCH["live"] = live
    return live


def watch_loop():
    cd = crashes_dir()
    _WATCH["started"] = time.time()
    try:
        _WATCH["crash_stamp"] = os.path.getmtime(cd) if cd and os.path.isdir(cd) else None
    except OSError:
        _WATCH["crash_stamp"] = None
    while _WATCH["on"]:
        try:
            live = watch_tick()
        except Exception:
            live = False                   # a watcher fault must never kill the app
        time.sleep(WATCH_FAST if live else WATCH_IDLE)


def start_watcher():
    if _WATCH["on"]:
        return
    _WATCH["on"] = True
    threading.Thread(target=watch_loop, daemon=True).start()


def watch_events(include_seen=False):
    with _WATCH["lock"]:
        return [dict(e) for e in _WATCH["events"] if include_seen or not e["seen"]]


def ack_events(upto_id=None):
    with _WATCH["lock"]:
        for e in _WATCH["events"]:
            if upto_id is None or e["id"] <= upto_id:
                e["seen"] = True


def watch_status():
    """What the watcher is doing, for the dashboard. Cheap enough to ride along on
    every /api/status poll."""
    now = time.time()
    # `ago` is when CSR last looked, which is the same instant for every channel — they
    # are all stat'ed in one pass. It belongs to the watcher, not to a channel, so it is
    # reported once below instead of being printed identically on each card.
    chans = [{"channel": k,
              "kb": round(v["size"] / 1024),
              "idle": v["idle"],
              "mtime": v.get("mtime"),      # epoch seconds; the per-channel fact
              "exists": v["exists"]}
             for k, v in sorted(_WATCH["checked"].items())]
    last = max((v["ts"] for v in _WATCH["checked"].values()), default=now)
    return {"on": _WATCH["on"], "live": _WATCH["live"],
            "poll": WATCH_FAST if _WATCH["live"] else WATCH_IDLE,
            "raised": _WATCH["raised"], "tests": _WATCH["tests"],
            # true / false / null — null means Windows couldn't be asked, and the UI
            # then says nothing about the game rather than guessing from the log
            "game": _WATCH.get("game"),
            "ago": round(now - last, 1) if _WATCH["checked"] else None,
            "uptime": round(now - _WATCH["started"]) if _WATCH["started"] else 0,
            "channels": chans}


#: Synthetic log text for the dashboard's test buttons. Fired through the SAME
#: _scan_chunk() the real tailer uses, so a passing test exercises the actual
#: parsing, classification and filtering — not a shortcut that only proves the
#: toast renders. 'benign' is the important one: it must produce NOTHING.
_TEST_CHUNKS = {
    "crash": (
        "Cloud Imperium Games public crash handler taking over...\n"
        "Exception STATUS_CRYENGINE_GPU_CRASH(0x2BADFF59) addr=0x1 digest=csrtest0001\n"
        "Is fatal error: No\nIs GPU crash: Yes\n"
        "- GPU crash message: GPU CRASH (async): simulated event from CSR's test button. [x]\n"
        "Is Timeout: No\nIs out of system memory: No\nIs out of video memory: No\n"
        "Process Memory Status: 14000MB working set size, 38000MB commit size (39000MB peak), 9000MB left\n"
        "System Memory Status: 63122MB total physical, 21000MB free physical, 75922MB commit limit\n"
        "All crash related data successfully handed over to crash info collector process\n"),
    "oom": (
        "Cloud Imperium Games public crash handler taking over...\n"
        "Exception STATUS_CRYENGINE_OUT_OF_SYSMEM(0xE0000008) addr=0x1 digest=csrtest0002\n"
        "Is out of system memory: Yes\n"
        "Process Memory Status: 31000MB working set size, 61000MB commit size (62000MB peak), 100MB left\n"
        "System Memory Status: 63122MB total physical, 900MB free physical, 75922MB commit limit\n"
        "All crash related data successfully handed over to crash info collector process\n"),
    "net": '<t> [Notice] <Channel Disconnected> cause=30024 reason="Back-end services are unresponsive"\n',
    "benign": ('<t> [Notice] <Channel Disconnected> cause=30010 reason="Nub destroyed"\n' * 4 +
               '<t> [Notice] <Channel Disconnected> cause=30016 reason="Remote Disconnect - Player requested disconnect"\n'
               '<t> [Notice] <Channel Disconnected> cause=30028 reason="Remote Disconnect - player inactive"\n'),
}


def fire_test_event(kind):
    """Push synthetic log text through the real scanner. Returns events raised."""
    chunk = _TEST_CHUNKS.get(kind)
    if chunk is None:
        return None
    before = _WATCH["raised"]
    _WATCH["tests"] += 1
    # A counter in the channel name keeps the 60-second de-dup from silently
    # swallowing a second press, and makes clear on screen that it isn't real.
    fire_test_event.n = getattr(fire_test_event, "n", 0) + 1
    _scan_chunk(chunk, f"TEST #{fire_test_event.n}", test=True)
    return _WATCH["raised"] - before


def sc_is_running():
    """True if a Star Citizen client is up. Writing settings underneath a running
    game is pointless — it holds them in memory and rewrites the file on exit — so
    every write is refused while it's open."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq StarCitizen.exe", "/NH"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "StarCitizen.exe" in (out.stdout or "")
    except Exception:
        return False                       # can't tell — don't block the user


def settings_backup_path(channel_dir):
    """Pristine copy — the state before CSR ever touched the file. Written once."""
    return os.path.join(channel_dir, SETTINGS_REL) + ".csr-backup"


def settings_prev_path(channel_dir):
    """Rolling copy — the state immediately before the LAST change. Rewritten every
    time, so a mis-click can be undone without discarding every later change too."""
    return os.path.join(channel_dir, SETTINGS_REL) + ".csr-prev"


def settings_restore_points(channel_dir):
    """What can be rolled back to, for the UI."""
    out = {}
    if not channel_dir:
        return out
    for key, path in (("prev", settings_prev_path(channel_dir)),
                      ("original", settings_backup_path(channel_dir))):
        try:
            out[key] = {"when": datetime.fromtimestamp(
                os.path.getmtime(path)).strftime("%d-%b-%Y %H:%M")}
        except OSError:
            pass
    return out


def restore_settings(channel_dir, which="prev"):
    """Copy a restore point back over attributes.xml. Same guards as writing."""
    if not channel_dir:
        return False, "no install folder for that channel"
    path = os.path.join(channel_dir, SETTINGS_REL)
    src = settings_prev_path(channel_dir) if which == "prev" \
        else settings_backup_path(channel_dir)
    if not os.path.isfile(src):
        return False, "no backup to restore from"
    if sc_is_running():
        return False, "Star Citizen is running — close it first, or it will overwrite this on exit"
    try:
        # the file being replaced becomes the new undo point, so restore is itself
        # undoable and the user can toggle back if they restored the wrong one
        if os.path.isfile(path):
            shutil.copy2(path, settings_prev_path(channel_dir) + ".swap")
        tmp = path + ".csr-tmp"
        shutil.copy2(src, tmp)
        os.replace(tmp, path)
        swap = settings_prev_path(channel_dir) + ".swap"
        if os.path.isfile(swap):
            os.replace(swap, settings_prev_path(channel_dir))
    except OSError as e:
        return False, f"could not restore: {e}"
    return True, "restored"


def write_settings(channel_dir, changes):
    """Apply {key: value} to a channel's attributes.xml, in place.

    Only the `value=` of existing <Attr> entries is rewritten — no keys are added,
    none removed, and the rest of the file is untouched byte-for-byte. The original
    is copied to <file>.csr-backup first (once, so the backup always represents the
    state before CSR ever touched it) which is what makes this reversible.

    Returns (ok, message, applied_dict)."""
    if not channel_dir:
        return False, "no install folder for that channel", {}
    path = os.path.join(channel_dir, SETTINGS_REL)
    if not os.path.isfile(path):
        return False, "settings file not found", {}
    if sc_is_running():
        return False, "Star Citizen is running — close it first, or it will overwrite this on exit", {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        return False, f"could not read settings: {e}", {}

    applied = {}
    for key, val in changes.items():
        val = str(val)
        pat = re.compile(r'(<Attr\s+name="%s"\s+value=")([^"]*)(")' % re.escape(key))
        m = pat.search(text)
        if not m:
            continue                       # never invent a setting the game didn't write
        if m.group(2) != val:
            applied[key] = [m.group(2), val]
            text = pat.sub(lambda mm: mm.group(1) + val + mm.group(3), text, count=1)
    if not applied:
        return True, "already set — nothing to change", {}

    try:
        backup = settings_backup_path(channel_dir)
        if not os.path.exists(backup):
            shutil.copy2(path, backup)          # pristine, written once
        shutil.copy2(path, settings_prev_path(channel_dir))   # undo point, every time
        # write via a temp file in the same folder, then replace: a crash mid-write
        # can't leave the game with a half-written settings file
        tmp = path + ".csr-tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError as e:
        return False, f"could not write settings: {e}", {}
    return True, f"applied {len(applied)} change(s)", applied


def revert_settings(channel_dir):
    """Restore the backup taken before CSR's first write."""
    if not channel_dir:
        return False, "no install folder for that channel"
    path = os.path.join(channel_dir, SETTINGS_REL)
    backup = settings_backup_path(channel_dir)
    if not os.path.isfile(backup):
        return False, "no CSR backup exists for this channel"
    if sc_is_running():
        return False, "Star Citizen is running — close it first"
    try:
        shutil.copy2(backup, path)
    except OSError as e:
        return False, f"could not restore: {e}"
    return True, "settings restored to the state before CSR changed them"


# --------------------------------------------------------------------------- #
#  Parsing helpers
# --------------------------------------------------------------------------- #

ENV_RE = re.compile(r"pub-sc-alpha-(\d)(\d)(\d)-")      # -> major.minor  (490 = 4.9)
BRANCH_RE = re.compile(r"sc-alpha-(\d+)\.(\d+)\.")       # fallback
# Tech-Preview *feature* branches carry no version at all (`Branch: scp-crafting`,
# `@env_session: 'ptu-scp-crafting-…'`), so the two patterns above find nothing and the
# session used to land in no patch bucket — it still counted toward the career, which is
# why a channel's career could exceed the sum of its patches. Those builds are named by
# their BRANCH rather than by FileVersion: the client reports "1.0.172" on them, and
# listing a "Patch 1.0" next to 4.10 implies a 1.0 release that hasn't happened.
NAMED_BRANCH_RE = re.compile(r"Branch:\s*([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s*$", re.I)
FILEVER_RE = re.compile(r"(?:File|Product)Version:\s*(\d+)\.(\d+)\.")
FULLVER_RE = re.compile(r"(?:File|Product)Version:\s*([\d.]+)")
# the log's `Executable:` line names the channel the session ACTUALLY ran from
# (…\LIVE\Bin64\, …\TECH-PREVIEW\Bin64\, …) — the ground truth for channel
# attribution, immune to logs being copied between channel folders.
EXE_CHANNEL_RE = re.compile(r"[\\/](LIVE|PTU|EPTU|TECH-PREVIEW|HOTFIX)[\\/]Bin64", re.I)
SHIP_RE = re.compile(r"control token for '([A-Za-z][A-Za-z0-9_]+?)_\d{6,}'")
MISSION_RE = re.compile(r"MissionId\[([^\]]+)\].*?CompletionType\[([^\]]+)\]")
ATTACH_RE = re.compile(r"AttachmentReceived> Player\[[^\]]+\] Attachment\[[^,]+,\s*([^,]+),")
# which port an attachment went to: hand = drawn/readied, stocked/sidearm = carried in loadout
ATTACH_PORT_RE = re.compile(r"Port\[([a-zA-Z0-9_]+)\]")
# reload proxy: an AmmoRepool request names the weapon's magazine class in Source[...]
# (the Source[...] group is also what makes this match once per request — see scan_log)
AMMO_RE = re.compile(r"Type\[AmmoRepool\].*?Source\[([a-zA-Z0-9_]+)\]")
# item transfer between inventories; same one-line-per-request rule as AMMO_RE
MOVE_RE = re.compile(r"Type\[Move\].*?Source\[([a-zA-Z0-9_]+)\]")
# looting: accessing an external inventory -> class + instance id (dedup by id)
LOOT_RE = re.compile(r"Requesting access token for User\[[^\]]+\] on Inventory\[([A-Za-z0-9_]+?)_(\d{6,})[,\]]")
# the entity id (3rd field) of anything the player attaches to themselves = gear they OWN
ATTACH_ID_RE = re.compile(r"AttachmentReceived> Player\[[^\]]+\] Attachment\[[^\]]*,\s*(\d{6,})\]")
# words that mark an accessed inventory as wearable gear (a corpse-loot candidate)
_GEAR_WORDS = ("_armor_", "_combat_", "_backpack", "_legs", "_core", "_helmet",
               "_utility_", "_flightsuit", "_arms", "_undersuit", "_pants", "_torso")
QT_RE = "Player Selected Quantum Target - Local"
# A selected target is an intention; the drive ARRIVING is a jump. Across the 2026
# archive selections outrun arrivals ~1.4x (re-targeting, aborted spool-ups), and a
# player who recalled "two jumps" had three arrivals — the one to a mission beacon
# had slipped their mind. Arrivals are the count; selections are kept as context.
QT_ARRIVE = "Quantum Drive Arrived - Arrived at Final Destination"
# HUD notifications (logged since 4.5) — the queue line appears once per notification id.
# Language packs decorate contract titles ([100 Rep], [BP]*, <EM4>…</EM4>); all stripped.
NOTIF_RE = re.compile(r'Added notification "(.*?)" \[(\d+)\]')
JURIS_RE = re.compile(r"^(?:Entered (.+?) Jurisdiction|Journal Entry Added: Jurisdiction: (.+?))\s*$")
CONTRACT_NOTIF_RE = re.compile(r"^Contract (Accepted|Complete|Failed):\s*(.+?)\s*$")
PU_JOIN = ('Context Establisher Done', 'gamerules="SC_Default"', 'establisher="Network"')
LZ_VISIT_GAP = 600           # armistice-zone entries closer than this are one visit


def _notif_clean(text):
    """A HUD notification's text as the player read it, minus markup and language-pack
    decorations: '<EM4>Help Protect Site [500 Rep] [BP]*</EM4>: ' -> 'Help Protect Site'."""
    t = re.sub(r"</?EM\d>", "", text or "").replace("\xa0", " ")
    t = re.sub(r"\[[^\]]*\bRep\]\*?", "", t)
    t = re.sub(r"\[BP\]\*?", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t.rstrip(":").strip()
DEAD_RE = "ActorState] Dead"
LOGIN_RE = re.compile(r"Handle\[([^\]]+)\]")
# Fallback for reduced-logging builds (e.g. some 4.10 PTU) that drop the Handle[…]
# login line: the LOCAL player's own inventory/actor errors name them as the
# container owner. This is local-player-specific (CSCLocalPlayer… class), so it
# won't pick up the OTHER players whose names also appear in the log.
LOCAL_HANDLE_RE = re.compile(r"LocalPlayer\w*.*?owned by '([^']+)'")
CONTRACT_RE = re.compile(r"contract \[([A-Za-z][A-Za-z0-9_]+?)(?:_\d+)?\]")
FLEET_RE = re.compile(r"Retrieved \d+ entitlements out of (\d+) vehic")
# Crafting blueprints (4.7+) reach the log ONLY as HUD notifications, and not in one
# shape: most builds queue each one ("Added notification … [id]") and then log its
# lifecycle (Next / StartFade / Remove) under <UpdateNotificationItem>. The text is
# whatever the player's UI shows: community language packs (StarStrings, ScCompLangPack)
# rename components ("Mil/1/D Tundra", 'MIL-2A "XL-1"'), append a [BP] badge and, in
# some versions, wrap the WHOLE string in <EM4>…</EM4> — so the quote is followed by
# markup, not by "Received". 15 blueprints vanished when the pattern was anchored on
# that quote. So nothing is anchored on it, every line form is matched, and receipts
# are deduplicated by the notification id, which is unique within a session. The
# name runs to the closing `: " [id]` and may itself contain quotes
# (Atzkav "Mirage" Sniper Rifle) — a [^"]* match truncates those to the bare model
# and silently merges every skin of a gun into one entry.
BLUEPRINT_RE = re.compile(r'Received Blueprint: (.*?): " \[(\d+)\]')


def _bp_clean(name):
    """The display name as the HUD shows it: markup tags and the [BP] badge removed,
    the occasional non-breaking space normalised (five of them in one player's logs)."""
    name = re.sub(r"</?EM\d>", "", name).replace("\xa0", " ")
    name = re.sub(r"\s*\[BP\]", "", name)
    return re.sub(r"\s+", " ", name).strip()
DEAD_ZONE_RE = re.compile(r"ejected from zone '([A-Za-z][A-Za-z0-9_]+?)_\d{6,}'")
COLLISION_RE = "FatalCollision"
# --- LEGACY combat (old format, only in pre-Nov-2025 / patch 4.1–4.3 logs) ---
ACTOR_KILL_RE = re.compile(
    r"<Actor Death> CActor::Kill: '([^']+)' \[\d+\] in zone '([^']*)' "
    r"killed by '([^']+)' \[\d+\] using '([^']*)' \[Class [^\]]*\] "
    r"with damage type '([^']*)'")
VDESTROY_RE = re.compile(
    r"<Vehicle Destruction> CVehicle::OnAdvanceDestroyLevel: Vehicle '([^']+)' \[\d+\] "
    r"in zone '([^']*)'.*?driven by '([^']*)' \[\d+\] "
    r"advanced from destroy level (\d+) to (\d+) caused by '([^']*)'")
GAMERULES_RE = re.compile(r'gamerules="([^"]+)"')
# shop purchases: item buys carry a price + item class + quantity; commodity buys
# carry a price only (the good is a resourceGUID we don't name).  client_price is
# the TOTAL for the transaction (it scales with quantity), so it sums as spend.
ITEMBUY_RE = re.compile(r"client_price\[([\d.]+)\].*?itemName\[([^\]]+)\] quantity\[(\d+)\]")
COMMBUY_RE = re.compile(r"\bprice\[([\d.]+)\]")

# --- machine profile & stability (all from the log's own startup header) -------
# Every build since 4.1 writes the same rig banner, so these work across the whole
# archive. NOTE: "D3D Adapter: Description" looks like the obvious GPU source but is
# absent from most 4.1/4.2/4.7 logs — the "Logging video adapters:" block is the one
# present everywhere, and it also says which adapter actually renders. We read that.
HOSTCPU_RE = re.compile(r"Host CPU:\s*(.+?)\s*$")
CORES_RE = re.compile(r"Logical CPU Count:\s*(\d+)")
RAM_RE = re.compile(r"(\d+)MB physical memory installed,\s*(\d+)MB available")
ADAPTER_RE = re.compile(r"^\s*-\s+(.+?)\s+\(vendor = 0x([0-9a-fA-F]+)")
VRAM_RE = re.compile(r"Dedicated video memory:\s*(\d+)\s*MB")
SUITABLE_RE = re.compile(r"Suitable rendering device:\s*(yes|no)", re.I)
DRIVER_RE = re.compile(r"Driver version \(UMD\):\s*([\d.]+)")
# Sessions that ran on Vulkan have no "D3D Adapter:" block at all — they report the
# driver through VulkanMetric instead. Reading both is what makes the driver timeline
# continuous across SC's DX11 → Vulkan transition, and tells us which renderer ran.
# One authoritative line naming the adapter actually chosen. Deliberately NOT the
# "VulkanMetric - Driver" lines: those repeat per enumerated device, so the last one
# read is the integrated GPU's driver (2.0.353), not the discrete card's (596.36.0.0).
VKCHOSEN_RE = re.compile(
    r"Chosen Vulkan GPU Device \((.+?)\) Driver Version \(([\d.]+)\)(?: Vulkan API \(([\d.]+)\))?")
DISPLAY_RE = re.compile(r"Current display mode is (\d+)x(\d+)")
OSVER_RE = re.compile(r"^Windows .*?\(build ([\d.]+)\)", re.M)
DLSS_RE = re.compile(r"GPU: DLSS Support\s*=\s*(.+?)\s*$")
# SC benchmarks the CPU and GPU on EVERY launch — a real per-session time series.
PERFIDX_RE = re.compile(r"Performance Index:\s*([\d.]+)\s*\(CPU\),\s*([\d.]+)\s*\(GPU\)")
# How the session ended. Seen in practice: 30016 (player quit / closed window) and
# 30024 (back-end services unresponsive). A log with no such line at all ended
# abnormally — crash, kill, or power loss.
QUIT_RE = re.compile(r"CSystem::Quit invoked with - cause=(\d+), reason=([^,]+)")

# --- crash reports -----------------------------------------------------------
# Star Citizen writes its own post-mortem into the log. This marker is EXCLUSIVE to
# crashes (0 hits across 200 clean logs), so it is positive evidence of a CTD rather
# than the inference-from-a-missing-quit-line we relied on before.
CRASH_MARK = "public crash handler taking over"
CRASH_EXC_RE = re.compile(
    r"Exception (\w+)\((0x[0-9A-Fa-f]+)\)(?:\s+addr=\S+)?(?:\s+digest=([0-9a-f]+))?")
CRASH_FLAG_RE = re.compile(
    r"Is (fatal error|GPU crash|Timeout|out of system memory|out of video memory): (Yes|No)")
CRASH_GPUMSG_RE = re.compile(r"GPU crash message: ([^\[\n]+)")
CRASH_PMEM_RE = re.compile(
    r"Process Memory Status: (\d+)MB working set size, (\d+)MB commit size \((\d+)MB peak\)")
CRASH_SMEM_RE = re.compile(r"System Memory Status: (\d+)MB total physical, (\d+)MB free physical")

# --- frame-time / FPS --------------------------------------------------------
# Written on level unload AND on normal shutdown (present in 165/200 clean logs), so
# this is per-session performance data, not a crash-only artefact. The bucket columns
# are a frame-time histogram; the last one ("slower") is frames over 50 ms — hitches.
PROF_FPS_RE = re.compile(
    r"CPU \((MainThread|WholeFrame)\)\s*:\s*([\d.]+) FPS \(([\d.]+) ms\)\|([\d.%|\s]+)")
PROF_ENT_RE = re.compile(r"Entities\s+: Min: (\d+) Max: (\d+) Avg: (\d+)")
PROF_FRAMES_RE = re.compile(r"Frames\s+: (\d+)")
PROF_WSET_RE = re.compile(r"Memory \(WorkingSet\)\s*: Min: ([\d.]+) MB Max ([\d.]+)")

# --- network / session events ------------------------------------------------
DISCO_RE = re.compile(r'cause=(\d+) reason="([^"]*)"')
#  ---- dropped to the main menu -------------------------------------------------
#  Being kicked back to the front end mid-session is, in the log, indistinguishable
#  from quitting: BOTH are `cause=30016 reason="Remote Disconnect - Player requested
#  disconnect"`, and both carry isRemote=1 because the server closes the channel
#  either way. The game says the player asked to leave even when they did not.
#
#  Three signals together separate them, checked against 1,288 logs:
#    1. gamerules="SC_Default" — the persistent universe, not the front end or Arena
#       Commander (`Nub destroyed` on SC_Frontend fires constantly and means nothing).
#    2. no client-side quit request in the 20 s before it. When the player leaves on
#       purpose the client logs its own intent first (RequestQuitLobby / DisconnectCmd
#       / CSystem::Quit); when the server drops them, nothing precedes it.
#    3. the client goes back to the front end within 15 s, and then keeps logging for
#       another 2 minutes — i.e. the player rejoined and carried on, which is what
#       makes it a drop rather than the end of the session.
#  240 of the events that pass (1) and (3) are excluded by (2) as deliberate exits.
#
#  The 2026 builds (11010425 onward) broke signal (2): the client now writes
#  RequestQuitLobby ~7 s AFTER the disconnect, on entering the front end, for drops and
#  deliberate exits alike. All 274 qualifying events in the 2026 archive have no intent
#  before and a lobby quit after, and a session where the player confirmed a deliberate
#  "exit to menu" from a server queue looked identical to a drop. No network-fault line
#  precedes real drops reliably either (14%). So from 2026 the figure is honestly
#  "returned to the main menu mid-session", drops and exits together, and the UI says
#  so. The intent test stays: it still holds for 2025-era logs.
BOOT_INTENT = ("RequestQuitLobby", "DisconnectCmd", "CSystem::Quit")
BOOT_FRONTEND = "[CSessionManager::RequestFrontEnd]"
BOOT_DISCO_RE = re.compile(
    r'cause=30016 reason="Remote Disconnect - Player requested disconnect"'
    r'[^\n]*?gamerules="SC_Default"')
BOOT_INTENT_WINDOW = 20      # seconds a quit request stays "recent"
BOOT_FRONTEND_WINDOW = 15    # seconds allowed between the drop and the menu load
# (a return to the menu counts only if a PU server join follows it — see PU_JOIN; the old
#  "log kept growing for 120 s" test also passed a slow log-off)
#: codes that fire during EVERY normal session — 30010 alone appears ~5× per log.
#: Alerting on these would bury a real fault in noise, so they are never raised.
BENIGN_NET_CODES = {"30010", "30016", "30028"}
#: the ones worth interrupting someone for. The community calls these "30k errors";
#: there is no literal "30k" in the log — it is shorthand for the 30000-series codes.
NET_CODE_MEANING = {
    "30024": "Back-end services unresponsive",
    "30000": "Connection timed out",
    "30013": "Server raised a signal",
    "30015": "Signed in from somewhere else",
    "41045": "Timed out waiting for game rules",
    "41058": "Player query failed",
    "41070": "Timed out waiting for your character",
    "64004": "Server could not resolve your location",
    "64008": "Server could not resolve your location",
    "64010": "Location lookup timed out",
    "67001": "Server database unreachable",
    "70003": "Authentication timed out",
    "70006": "Client integrity violation",
}
#: which of the above are CIG's problem rather than the player's, so the alert can
#: say so outright instead of leaving someone to suspect their own connection
NET_SERVER_SIDE = {"30024", "30000", "30013", "41045", "41058", "41070",
                   "64004", "64008", "64010", "67001"}
#: plain-English translation + whether the player can act on it
CRASH_KIND = {
    "STATUS_CRYENGINE_GPU_CRASH":  ("Graphics driver crash", True,
        "The GPU stopped responding. Star Citizen's own advice is to update or disable "
        "overlay/recording software (Discord, OBS, RTSS, GeForce Experience)."),
    "STATUS_CRYENGINE_OUT_OF_SYSMEM": ("Ran out of system memory", True,
        "The game exhausted available RAM. Closing background applications, or more RAM, "
        "is the only real fix."),
    "STATUS_CRYENGINE_WATCH_DOG":  ("Hang / watchdog timeout", True,
        "A frame took so long the game gave up on itself — often heavy streaming from a "
        "slow drive, or a stalled server."),
    "EXCEPTION_ACCESS_VIOLATION":  ("Game bug", False,
        "The client read memory it shouldn't have. This is a fault in Star Citizen — "
        "nothing on your machine caused it and no setting prevents it."),
    "EXCEPTION_BREAKPOINT":        ("Game assertion failed", False,
        "The client tripped one of its own internal checks. A build problem, not yours."),
    "STATUS_CRYENGINE_FATAL_ERROR": ("Fatal engine error", False,
        "The engine shut itself down deliberately after an unrecoverable error."),
}


def parse_crash_report(text):
    """Pull Star Citizen's post-mortem out of a log tail. Returns None if absent."""
    if CRASH_MARK not in text:
        return None
    out = {}
    m = CRASH_EXC_RE.search(text)
    if m:
        out["exception"], out["code"] = m.group(1), m.group(2)
        if m.group(3):
            out["digest"] = m.group(3)
    for k, v in CRASH_FLAG_RE.findall(text):
        if v == "Yes":
            out[{"fatal error": "fatal", "GPU crash": "gpu", "Timeout": "timeout",
                 "out of system memory": "oom", "out of video memory": "oovram"}[k]] = True
    m = CRASH_GPUMSG_RE.search(text)
    if m:
        out["gpu_msg"] = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    m = CRASH_PMEM_RE.search(text)
    if m:
        out["ws_mb"], out["peak_commit_mb"] = int(m.group(1)), int(m.group(3))
    m = CRASH_SMEM_RE.search(text)
    if m:
        out["sys_mb"], out["free_mb"] = int(m.group(1)), int(m.group(2))
    return out or None


def _mk_combat():
    """Empty per-venue combat tally (used for both PU and Arena Commander)."""
    return {"lc": Counter(), "wpn_ship": Counter(), "wpn_fps": Counter(),
            "ram": Counter(), "killed": Counter(), "killedby": Counter()}
SHOPNAME_RE = re.compile(r"shopName\[([^\]]+)\]")
_NPC_MARK = ("pu_pilots", "pu_human", "pu_populace", "pu_ai", "npc", "aimodule",
             "kopion", "quasigrazer", "marok", "vanduul", "ninetails", "criminal",
             "hostile", "enemy", "creature", "populationcontrol", "_ai_", "gunner",
             "outlaw", "pirate", "frigate", "turret", "guard", "soldier",
             # environment / props / mission entities (not real players)
             "hazard", "toggleable", "effect", "physical", "shipjacker", "_hub",
             "hub_", "module", "destructible", "prop_", "dummy", "target_",
             "sentry", "drone", "panel", "door", "elevator", "kiosk", "terminal",
             "container", "debris", "wreck", "station", "outpost", "bunker",
             "defense", "defence", "spawn", "mission", "interact", "trolley",
             "_medium_", "_light_", "_heavy_", "physicsgrid", "wildlife", "animal")
_NPCID_RE = re.compile(r"_\d{6,}$")


def _pretty_wpn(cls):
    """Clean a legacy ship-weapon class id -> readable name."""
    c = re.sub(r"_S\d+.*$", "", cls or "")        # drop size + spawn id
    c = re.sub(r"_\d{4,}$", "", c)
    return c.replace("_", " ").strip() or (cls or "")


def _merge_named(counter, namer):
    """Collapse a class->count Counter to displayname->count (drops None names)."""
    out = Counter()
    for cls, c in counter.items():
        nm = namer(cls)
        if nm:
            out[nm] += c
    return out


# ship-weapon manufacturer codes -> full names (for the fallback formatter)
_SHIPWPN_MAN = {
    "KLWE": "Klaus & Werner", "BEHR": "Behring", "MXOX": "MaxOx",
    "HRST": "Hurston Dynamics", "AMRS": "Amon & Reese", "APAR": "Apocalypse Arms",
    "GATS": "Gallenson Tactical", "KBAR": "Kroneg", "RSI": "RSI", "AEGS": "Aegis",
    "VNCL": "Vanduul", "ESPR": "Esperia", "BANU": "Banu", "GRIN": "Greycat",
}
# component words that mean a prefix match landed on a ship part, not a weapon
_NONWPN_WORDS = ("seat", "turret", "cockpit", "chair", "bed", "door", "hatch",
                 "panel", "console", "screen", "ladder", "elevator", "thruster",
                 "cooler", "shield generator", "radar", "quantum drive", "powerplant")
# words that confirm a class really is a weapon (so a ship-code prefix is fine)
_WPN_WORDS = ("cannon", "repeater", "laser", "gun", "rifle", "pistol", "smg", "lmg",
              "shotgun", "sniper", "gatling", "massdriver", "driver", "beam",
              "neutron", "distortion", "scattergun", "missile", "torpedo", "rack",
              "mrck", "melee", "launcher", "ballistic", "energy")


def _shipwpn_pretty(cls):
    """Readable fallback for an unresolved ship weapon: expand manufacturer code
    and split CamelCase.  KLWE_LaserRepeater_S1_ATLS -> Klaus & Werner Laser Repeater."""
    c = re.sub(r"_S\d+.*$", "", cls or "", flags=re.I)   # drop size + trailing
    c = re.sub(r"_\d{3,}.*$", "", c)
    parts = [p for p in c.split("_") if p]
    if not parts:
        return (cls or "").replace("_", " ")
    man = _SHIPWPN_MAN.get(parts[0].upper(), "")
    rest = " ".join(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", p) for p in parts[1:])
    return (man + " " + rest).strip() or c.replace("_", " ")


def ram_ship(weapon):
    """If a kill's 'weapon' is actually a ship (a ram/collision kill), return the
    ship's display name; otherwise None."""
    if not weapon:
        return None
    low = weapon.lower()
    if any(w in low for w in _WPN_WORDS):
        return None
    if SHIPS.ok:
        rec = SHIPS.match(weapon)
        if rec:
            return rec["name"]
    return None


#: Handles seen while scanning. The legacy kill lines sometimes name the PLAYER in the
#: weapon field (a self-inflicted death), which would otherwise show up as a weapon
#: called after you. This used to be a hardcoded "archelium" — correct on exactly one
#: machine, and silently wrong for every other player.
_KNOWN_HANDLES = set()


def legacy_wpn_name(cls):
    """Resolve a legacy kill weapon to a clean name, or None to drop it
    (self/environment, or a ship used as a ramming 'weapon')."""
    if not cls:
        return None
    low = cls.lower().strip()
    if low in ("unknown", "none", "") or low in _KNOWN_HANDLES:
        return None
    # a bare ship name (no weapon word) = a collision/ram kill, not a weapon
    if SHIPS.ok and not any(w in low for w in _WPN_WORDS):
        if SHIPS.match(cls):
            return None
    if sc_names.ITEMS:
        c = cls.lower()
        # candidate keys: full (FPS "_01" models), ship weapon keeping "_sN" size
        # but dropping the trailing spawn id, and a big-id strip.
        cands = [c,
                 re.sub(r"(_s\d+)_\d+.*$", r"\1", c),     # KLWE_LaserRepeater_S4_5286 -> ..._s4
                 re.sub(r"_\d{4,}.*$", "", c)]
        for k in cands:
            hit = sc_names.ITEMS.idx.get(k)
            if hit:
                return sc_names._skin_strip(hit)
        r = sc_names.ITEMS.resolve(cands[1])              # prefix (skins) fallback
        if r and not any(w in r.lower() for w in _NONWPN_WORDS):
            return r
    return _shipwpn_pretty(cls)


# CONFIRMED real in-game store brands (the shop code reliably names the brand).
# NB: do NOT guess brands from ambiguous internal codes — e.g. SCShop_lt_a_casaba_*
# is a generic weapons/gear kiosk, NOT the Casaba Outlet clothing chain, so "casaba"
# is intentionally absent. Unbranded shops are labelled by what you buy there instead.
_SHOP_BRANDS = (
    ("centermass", "Centermass"), ("omegapro", "Omega Pro"), ("omega_pro", "Omega Pro"),
    ("platinumbay", "Platinum Bay"), ("cubbyblast", "Cubby Blast"),
    ("dumper", "Dumper's Depot"), ("newdeal", "New Deal"), ("astroarmada", "Astro Armada"),
    ("tammany", "Tammany & Sons"), ("keltco", "Kel-To"), ("kelto", "Kel-To"),
    ("garrity", "Garrity Defense"), ("cousincrow", "Cousin Crow's"),
    ("conscientious", "Conscientious Objects"), ("skutters", "Skutters"), ("shubin", "Shubin"),
)
# shop TYPE from a shopName keyword (used when there's no confirmed brand)
_SHOP_TYPES = (
    ("weaponsmith", "Weapons"), ("weaponsarmor", "Weapons & Armor"), ("shipweapons", "Ship weapons"),
    ("fpsitems", "Weapons"), ("weapon", "Weapons"), ("armorstall", "Armor"), ("armor", "Armor"),
    ("pharmacy", "Pharmacy"), ("hospital", "Pharmacy"), ("clinic", "Pharmacy"),
    ("stall_med", "Medical"), ("medical", "Medical"), ("blackmarket", "Black market"),
    ("cargo", "Cargo office"), ("refinery", "Refinery"), ("mining", "Mining"),
    ("hotdog", "Food & drink"), ("foodstall", "Food & drink"), ("food", "Food & drink"), ("bar", "Bar"),
    ("gadget", "Gadgets"), ("clothing", "Clothing"), ("admin", "Admin office"),
    ("trdpst", "Trading post"), ("tradepost", "Trading post"),
)
# location tokens to append when present
_SHOP_LOC = {
    "newbabbage": "New Babbage", "newbab": "New Babbage", "lorville": "Lorville",
    "area18": "Area18", "orison": "Orison", "grimhex": "GrimHex", "pyro": "Pyro",
    "nyx": "Nyx", "levski": "Levski", "reststop": "Rest Stop", "truckstop": "Truck Stop",
}
# item class -> shopping category (to label unbranded shops by what's bought there)
_ITEM_CAT = (
    ("fuelpod", "Fuel"), ("magazine", "Weapons"), ("_mag", "Weapons"), ("_ammo", "Weapons"),
    ("rifle", "Weapons"), ("pistol", "Weapons"), ("shotgun", "Weapons"), ("sniper", "Weapons"),
    ("_smg", "Weapons"), ("smg_", "Weapons"), ("_lmg", "Weapons"), ("lmg_", "Weapons"),
    ("gren", "Weapons"), ("launcher", "Weapons"), ("knife", "Weapons"),
    ("optics", "Weapon attachments"), ("scope", "Weapon attachments"), ("suppressor", "Weapon attachments"),
    ("cannon", "Ship weapons"), ("repeater", "Ship weapons"), ("scattergun", "Ship weapons"),
    ("helmet", "Armor & clothing"), ("armor", "Armor & clothing"), ("undersuit", "Armor & clothing"),
    ("_legs", "Armor & clothing"), ("_arms", "Armor & clothing"), ("_core", "Armor & clothing"),
    ("_torso", "Armor & clothing"), ("backpack", "Armor & clothing"), ("clothing", "Armor & clothing"),
    ("medical", "Medical"), ("healing", "Medical"), ("medpen", "Medical"), ("hemozal", "Medical"),
    ("canister", "Medical"), ("paramed", "Medical"),
    ("food", "Food & drink"), ("drink", "Food & drink"), ("bottle", "Food & drink"), ("ration", "Food & drink"),
    ("multitool", "Tools"), ("tractor", "Tools"), ("mining", "Tools"), ("salvage", "Tools"),
    ("scanner", "Tools"), ("gadget", "Tools"), ("hackingchip", "Tools"),
    ("box", "Cargo"), ("container", "Cargo"), ("carryable", "Cargo"),
    ("shield", "Ship components"), ("cooler", "Ship components"), ("quantum", "Ship components"),
    ("powerplant", "Ship components"), ("thruster", "Ship components"),
    ("missile", "Ship ordnance"), ("torpedo", "Ship ordnance"),
)


def _item_category(name):
    n = (name or "").lower()
    for k, v in _ITEM_CAT:
        if k in n:
            return v
    return "Other"


def _purchase_category(cls):
    """Consolidated 'what did you buy' bucket for a purchased item class.
    FPS vs ship weapons is decided by weapon words + size tokens (S0–S12)."""
    n = (cls or "").lower()
    if "fuelpod" in n:
        return "Fuel"
    # FPS weapons, attachments & ammo — checked before ship-size so an FPS optic
    # tagged _s1 isn't mistaken for a ship weapon
    if any(w in n for w in ("rifle", "pistol", "shotgun", "sniper", "_smg", "smg_", "_lmg",
            "lmg_", "gren", "knife", "melee", "rocket", "launcher", "_mag", "magazine", "_ammo",
            "optics", "scope", "suppressor", "barrel", "_grip", "_stock", "holo", "reflex")):
        return "FPS weapons & armor"
    if any(w in n for w in ("helmet", "armor", "undersuit", "_legs", "_arms", "_core",
            "_torso", "backpack", "clothing", "boots", "shoes", "_hat", "jacket", "gloves",
            "flightsuit", "mask", "pants", "shirt", "vest", "coat", "dress", "skirt", "_top_")):
        return "FPS weapons & armor"
    # ship weapons & components — size-tagged (S0–S12) or ship-part / ship-gun words
    if re.search(r"_s\d{1,2}(?=_|$)", n) or any(w in n for w in ("cannon", "repeater",
            "scattergun", "gatling", "massdriver", "laserrepeater", "ballistic_gun", "neutron",
            "distortion", "_qd_", "shield", "cooler", "powerplant", "thruster", "quantumdrive",
            "missile", "misl_", "torpedo", "bomb_", "emp_", "turret")):
        return "Ship weapons & components"
    if n.startswith("crlf_") or any(w in n for w in ("medical", "healing", "medpen", "medgun",
            "hemozal", "canister", "paramed", "oxypen", "oxygen", "radiation", "revival",
            "adren", "detox", "antirad", "cure", "_vial")):
        return "Medical"
    if any(w in n for w in ("food", "drink", "bottle", "ration", "bowl", "berries", "pizza",
            "coffee", "juice", "snack")):
        return "Food & drink"
    if any(w in n for w in ("multitool", "tractor", "mining", "salvage", "scanner", "binocular",
            "hackingchip", "beacon", "flashlight", "cutter", "tool")):
        return "Tools & gadgets"
    if any(w in n for w in ("playerdeco", "trophy", "_deco_", "furniture", "couch", "armchair",
            "beanbag", "_table_")):
        return "Decor & furniture"
    if any(w in n for w in ("box", "container", "carryable", "_scu", "pallet", "cargo")):
        return "Cargo"
    if SHIPS.ok and SHIPS.match(cls):
        return "Ships"
    return "Other"


# --- blueprints ------------------------------------------------------------------
# There is no ownership list anywhere in the client log — the crafting library is
# fetched from CIG's servers over gRPC and never written down — so what CSR can show
# is every blueprint the game ANNOUNCED while a log existed on this PC. Names are the
# HUD's display strings, matched back to item ids through the wiki item index; about
# nine in ten resolve, and the id then decides the category (an id is immune to a
# weapon maker called "…Arms"). The rest are judged from the name.
_BP_COMP_A = re.compile(r'^(MIL|IND|CIV|STL|CMP)-(\d)([A-D])\s+"(.+)"\s*$', re.I)   # MIL-2A "JS-400"
_BP_COMP_B = re.compile(r'^(Mil|Ind|Civ|Stl|Cmp)/(\d)/([A-D])\s+(.+?)\s*$', re.I)    # Mil/2/A FR-76
_BP_CLASS = {"mil": "Military", "ind": "Industrial", "civ": "Civilian",
             "stl": "Stealth", "cmp": "Competition"}
_BP_COMP_KIND = (("cool_", "Cooler"), ("shld_", "Shield generator"), ("powr_", "Power plant"),
                 ("radr_", "Radar"), ("qdrv_", "Quantum drive"), ("fuel_", "Fuel nozzle"),
                 ("qtank", "Quantum fuel tank"), ("fueltank", "Fuel tank"))
_BP_SHIPGUN_KIND = (("laserrepeater", "Laser repeater"), ("lasercannon", "Laser cannon"),
                    ("ballisticcannon", "Ballistic cannon"), ("ballisticgatling", "Ballistic gatling"),
                    ("neutronrepeater", "Neutron repeater"), ("neutroncannon", "Neutron cannon"),
                    ("tachyoncannon", "Tachyon cannon"), ("massdriver", "Mass driver"),
                    ("tractorbeam", "Tractor beam"), ("scattergun", "Scattergun"), ("distortion", "Distortion"),
                    ("repeater", "Repeater"), ("cannon", "Cannon"), ("gatling", "Gatling"))
_BP_SLOT = (("helmet", "Helmet"), ("backpack", "Backpack"), ("_core", "Core"),
            ("_torso", "Core"), ("_arms", "Arms"), ("_legs", "Legs"), ("jacket", "Jacket"))
_BP_GUN_WORDS = ("pistol", "rifle", "smg", "lmg", "hmg", "sniper", "shotgun", "crossbow", "launcher", "railgun")
_BP_NAME_WPN = re.compile(r"\b(pistol|rifle|smg|lmg|hmg|shotgun|crossbow|sniper|railgun|launcher)\b", re.I)
_BP_NAME_ARM = re.compile(r"\b(helmet|core|arms|legs|backpack|armor|armour)\b", re.I)
_BP_NAME_SUIT = re.compile(r"\b(flight suit|flight helmet|racing helmet|racing flight suit|undersuit)\b", re.I)
_BP_NAME_MAG = re.compile(r"\b(magazine|battery)\b", re.I)
_BP_NAME_MINE = re.compile(r"\b(mining laser|scraper|salvage)\b", re.I)
_bp_rev = None
_bp_cache = {}


def _bp_norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip().lower()


def _bp_reverse_index():
    """display name -> item classes (shortest first = the base item before its skins),
    built once from the item index. 12k entries; a dict flip, not a search."""
    global _bp_rev
    if _bp_rev is None:
        rev = defaultdict(list)
        for cls, nm in (ITEMS.idx or {}).items():
            if isinstance(nm, str) and nm:
                rev[_bp_norm(nm)].append(cls)
        for v in rev.values():
            v.sort(key=len)
        _bp_rev = rev
    return _bp_rev


_BP_SLOT_WORDS = ("helmet", "backpack", "core", "torso", "arms", "legs")


def _bp_slot_of(text):
    """The armour slot word a name or class id carries, or ''."""
    t = (text or "").lower()
    return next((w for w in _BP_SLOT_WORDS if w in t), "")


def _bp_resolve(name):
    """Item class for a blueprint's display name, or None.

    The crafting catalogue is consulted first — its names are the game's own and its
    classes are the blueprint outputs themselves. The wiki item index comes second, and
    with a guard: it carries a few mislabelled skins (qrt_specialist_heavy_ARMS_01_01_13
    is filed as "Antium HELMET Jet"), so a class whose slot word contradicts the name's
    is refused rather than trusted. Tries the name as written, then without a magazine's
    "(30 cap)" suffix, then a language-pack component's bare model name."""
    by_cls, by_name = _bp_catalog_index()
    rev = _bp_reverse_index()
    cands = [name, re.sub(r"\s*\(\d+\s*cap\)\s*$", "", name, flags=re.I)]
    for rx in (_BP_COMP_B, _BP_COMP_A):
        m = rx.match(name)
        if m:
            cands.append(m.group(4))
    want = _bp_slot_of(name)
    for c in cands:
        hit = by_name.get(_bp_norm(c))
        if hit and len(hit) == 1:
            return hit[0]["c"]
    for c in cands:
        hit = rev.get(_bp_norm(c))
        if hit:
            ok = [k for k in hit if not want or not _bp_slot_of(k) or _bp_slot_of(k) == want]
            if ok:
                return ok[0]
    return None


def _bp_classify(cls, name):
    """(category, kind) for a blueprint. The item class is the authoritative signal —
    immune to renaming and to a weapon maker called '…Arms' — and the name is only
    consulted when there is no class or the class has no tell. Used for owned rows and
    for the whole catalogue, so both sides of the Owned / Missing toggle agree."""
    cls = (cls or "").lower()
    cat, kind = "Other", ""
    comp = _BP_COMP_A.match(name) or _BP_COMP_B.match(name)
    if comp or any(k in cls for k, _ in _BP_COMP_KIND):
        cat = "Ship components"
        typ = next((v for k, v in _BP_COMP_KIND if k in cls), "")
        sz = re.search(r"_s(\d{1,2})(?=_|$)", cls)
        bits = [typ] + (["S" + str(int(sz.group(1)))] if sz else [])
        if comp:   # a language pack put class / grade in the name — worth keeping
            bits.append(f"{_BP_CLASS.get(comp.group(1).lower(), comp.group(1))} grade {comp.group(3).upper()}")
        kind = " · ".join(b for b in bits if b)
    elif cls:
        if "_mag" in cls or "magazine" in cls or "battery" in cls:
            cat = "Magazines & batteries"
        elif ("mining" in cls and "laser" in cls) or cls.startswith("salvage_") or "mining_pod" in cls or "mining_modules" in cls:
            cat = "Mining & salvage"
            kind = ("Mining laser" if "laser" in cls else "Ore pod" if "pod" in cls
                    else "Mining module" if "modules" in cls else "Salvage module")
        elif "flightsuit" in cls or "undersuit" in cls or "_suit_" in cls:
            cat = "Flight suits"
            kind = next((v for k, v in _BP_SLOT if k in cls), "Suit" if "helmet" not in cls else "")
        elif any(k in cls for k, _ in _BP_SLOT) or "_armor" in cls or "armor_" in cls:
            cat = "Armor"
            slot = next((v for k, v in _BP_SLOT if k in cls), "")
            weight = next((w.title() for w in ("light", "medium", "heavy") if "_" + w + "_" in cls), "")
            kind = " · ".join(x for x in (weight, slot) if x)
        elif re.search(r"_s\d{1,2}(?=_|$)", cls) or any(k in cls for k, _ in _BP_SHIPGUN_KIND):
            cat = "Ship weapons"
            kind = next((v for k, v in _BP_SHIPGUN_KIND if k in cls), "")
            sz = re.search(r"_s(\d{1,2})(?=_|$)", cls)
            if sz:
                kind = (kind + " · " if kind else "") + "S" + str(int(sz.group(1)))
        elif any(g in cls for g in _BP_GUN_WORDS):
            cat = "FPS weapons"
            gtype, ammo = sc_names.gun_class_labels(cls, name)
            kind = " ".join(x for x in (ammo, gtype) if x)
        elif any(w in cls for w in ("optics", "scope", "suppressor", "barrel", "_grip", "_stock", "reflex", "holo", "underbarrel")):
            cat = "Weapon attachments"
        elif any(w in cls for w in ("medical", "medpen", "medgun", "hemozal", "paramed", "oxypen")):
            cat = "Medical"
        elif any(w in cls for w in ("food", "drink", "bottle", "ration", "snack")):
            cat = "Food & drink"
        elif any(w in cls for w in ("shirt", "pants", "jacket", "boots", "shoes", "gloves", "_hat", "hat_", "coat", "dress", "skirt", "vest", "hood", "beanie", "scarf", "glasses", "mask")):
            cat = "Clothing"
        elif any(w in cls for w in ("multitool", "tractor", "scanner", "cutter", "gadget", "beacon", "flashlight")):
            cat = "Tools & gadgets"
        elif cls.startswith("carryable") or "_deco" in cls or "flair" in cls or "plushie" in cls or "poster" in cls:
            cat = "Flair & decor"
    if cat == "Other":                       # no class, or a class with no tell — judge the name
        if _BP_NAME_MAG.search(name):
            cat = "Magazines & batteries"
        elif _BP_NAME_MINE.search(name):
            cat = "Mining & salvage"
        elif _BP_NAME_SUIT.search(name):
            cat = "Flight suits"
        elif _BP_NAME_WPN.search(name):
            cat = "FPS weapons"
        elif _BP_NAME_ARM.search(name):
            cat = "Armor"
    if not kind:                             # nothing to read a kind from — take it off the name
        mk = re.search(r"\b(Battery|Magazine|Backpack|Helmet|Core|Arms|Legs|Flight Suit|Undersuit|Jacket)\b", name, re.I)
        if mk:
            kind = mk.group(1).title()
    return cat, kind


_bp_cat_idx = None


def _bp_catalog_index():
    """(by class, by normalised name) over the crafting catalogue, built once."""
    global _bp_cat_idx
    if _bp_cat_idx is None:
        by_cls, by_name = {}, defaultdict(list)
        for r in (BPS.rows if BPS.ok else []):
            by_cls[r["c"]] = r
            by_name[_bp_norm(r["n"])].append(r)
        _bp_cat_idx = (by_cls, by_name)
    return _bp_cat_idx


def _bp_tokens(s):
    return set(re.findall(r"[a-z0-9]+", _bp_norm(s)))


def _bp_match(name, cls):
    """The catalogue entry an owned blueprint corresponds to, or None.

    Class first — exact and immune to renaming. Then the name as the catalogue spells
    it (the item index sometimes hands back a sibling variant's class, e.g. helmet_03
    for a blueprint whose output is helmet_01). Then the one catalogue entry whose name
    contains every word of the logged one, which is how a language pack's 'BlackFire
    Racing Helmet' finds 'Neutrino Racing Helmet BlackFire'. A name that fits several
    entries is left unmatched rather than guessed."""
    by_cls, by_name = _bp_catalog_index()
    if not by_cls:
        return None
    if cls and cls.lower() in by_cls:
        rec = by_cls[cls.lower()]
        # a class came from the item index, which is occasionally mislabelled — only
        # believe it if the catalogue's name and the logged name have a word in common
        if _bp_tokens(name) & _bp_tokens(rec["n"]):
            return rec
    for c in (name, re.sub(r"\s*\(\d+\s*cap\)\s*$", "", name, flags=re.I)):
        hit = by_name.get(_bp_norm(c))
        if hit and len(hit) == 1:
            return hit[0]
    t = _bp_tokens(name)
    if len(t) >= 2:
        cands = [r for r in BPS.rows if t <= _bp_tokens(r["n"])]
        if len(cands) == 1:
            return cands[0]
    return None


def blueprint_info(name):
    """(display name, category, kind, catalogue key, class, alias) for a blueprint as the
    HUD announced it. The display name is the catalogue's — the game's own English,
    unaffected by whatever language pack the player runs — falling back to the item
    index, then to the logged text. `alias` is the logged text when it differs, so a
    search for what the player actually saw still finds the row. Cached per name:
    finalize() runs once per patch view and the same names come round every time."""
    if name in _bp_cache:
        return _bp_cache[name]
    cls = (_bp_resolve(name) or "").lower()
    rec = _bp_match(name, cls)
    if rec:
        cls = rec["c"]
    display = (rec["n"] if rec else None) or ((ITEMS.idx or {}).get(cls) if cls else None) or name
    display = display.replace("\xa0", " ").strip()
    cat, kind = _bp_classify(cls, name)
    alias = name if _bp_norm(name) != _bp_norm(display) else ""
    out = (display, cat, kind, (rec or {}).get("k") or "", cls, alias)
    _bp_cache[name] = out
    return out


# ship-weapon makers, for a family that has no name of its own (Behring's M3A..M8A
# share nothing but "Cannon"); unknown codes fall back to the code itself
_BP_SHIP_MAN = {"amrs": "Amon & Reese", "apar": "Apocalypse Arms", "asad": "ASAD", "banu": "Banu",
                "behr": "Behring", "espr": "Esperia", "gats": "Gallenson Tactical", "grin": "Greycat",
                "hrst": "Hurston Dynamics", "jokr": "Joker Engineering", "kbar": "Knightbridge Arms",
                "klwe": "Klaus & Werner", "krig": "Kruger Intergalactic", "kron": "Kroneg",
                "mxox": "MaxOx"}
_BP_ARM_SLOTS = ("helmet", "core", "arms", "legs", "backpack")
_BP_SETSLOT_RE = re.compile(r"\b(Racing Flight Suit|Racing Helmet|Flight Helmet|Flight Suit|Undersuit|Helmet|Core|Arms|Legs|Backpack|Armor|Suit)\b", re.I)
_BP_FAM_DROP = re.compile(r"^(?:[IVX]+|\d+|Mark|Mk\.?|\(S\d+\)|S\d+)$", re.I)
_BP_FAM_GENERIC = frozenset(("cannon", "repeater", "gatling", "scattergun", "tractor", "beam", "mass",
                             "driver", "ballistic", "laser", "series", "distortion", "neutron", "tachyon"))


def _bp_set_name(name):
    return re.sub(r"\s{2,}", " ", _BP_SETSLOT_RE.sub("", name)).strip()


def _bp_sets(rows):
    """Armour sets, read off the class ids: `cds_legacy_armor_heavy_arms_01_01_17` is
    slot *arms* of set `cds_legacy_armor_heavy_01_01_17` — the pieces of one set share
    the variant number, so grouping on it holds even where the names don't (the ADP
    set's helmet is the "Balor HCH"). Flight suits pair helmet and suit by name instead;
    their variant numbers don't line up. Returns [name, category, weight, pieces] with
    pieces [[slot index, key, piece name]]; single-piece groups are not sets and are
    left out."""
    groups = {}
    for key, name, cls, cat, kind, _d, _m in rows:
        low = (cls or "").lower()
        if cat == "Armor":
            toks = low.split("_")
            slot = next((s for s in _BP_ARM_SLOTS if s in toks), None)
            if not slot:
                continue
            g = groups.setdefault(("Armor", "_".join(t for t in toks if t != slot)), [])
            g.append((_BP_ARM_SLOTS.index(slot), key, name, kind))
        elif cat == "Flight suits":
            si = 0 if ("helmet" in low or "helmet" in name.lower()) else 1
            groups.setdefault(("Flight suits", _bp_set_name(name).lower()), []).append((si, key, name, kind))
    out = []
    for (cat, _base), pcs in groups.items():
        if len(pcs) < 2:
            continue
        pcs.sort()
        # what the non-helmet pieces call themselves, slot word dropped; helmets don't vote
        names = [_bp_set_name(n) for si, _k, n, _ in pcs if si != 0] or [_bp_set_name(pcs[0][2])]
        cnt = Counter(names)
        name = max(cnt, key=lambda x: (cnt[x], len(x)))
        weight = next((k.split(" \u00b7 ")[0] for _s, _k, _n, k in pcs
                       if k.split(" \u00b7 ")[0] in ("Light", "Medium", "Heavy")), "")
        out.append([name, cat, weight, [[si, k, n] for si, k, n, _ in pcs]])
    out.sort(key=lambda s: (s[1], s[0].lower()))
    return out


def _bp_fam_name(names, maker, kind):
    """A name for a size family: the words every member shares once the size tells
    (III, -1, Mark 2, (S1), HV-S1) are dropped. If nothing distinctive survives, the
    maker fronts it: M3A..M8A become "Behring Cannon"."""
    if len(names) == 1:
        return names[0]
    per = []
    for n in names:
        toks = []
        for t in n.split():
            t = re.sub(r"-(?:\d+|S\d+|[IVX]+)$", "", t)
            t = re.sub(r"^\d+-", "", t)
            if t and not _BP_FAM_DROP.match(t) and t.lower() != "series":
                toks.append(t)
        per.append(toks)
    common = [t for t in per[0] if all(t in p for p in per[1:])]
    own = [t for t in common if t.strip('"\u201c\u201d').lower() not in _BP_FAM_GENERIC]
    # a bare model code ("FL", "GT", "NN") isn't a name either — front it with the maker
    if not own or max(len(t) for t in own) < 4:
        common = [maker] + (common or [kind or "weapon"])
    return " ".join(common)


def _bp_families(rows):
    """Ship weapons grouped by class with the size token removed: `klwe_laserrepeater_s1`
    .. `_s6` is one family, the Hazard-Zone `_s1_mr01` variants another. Returns
    [name, kind, sizes] with sizes [[size, key, item name]]; one-size families included
    (the page counts them separately)."""
    fam = {}
    for key, name, cls, cat, kind, _d, _m in rows:
        if cat != "Ship weapons":
            continue
        m = re.search(r"_s(\d{1,2})(?=_|$)", cls or "")
        if not m:
            continue
        base = cls[:m.start()] + cls[m.end():]
        fam.setdefault(base, []).append((int(m.group(1)), key, name, kind))
    out = []
    for base, members in fam.items():
        members.sort()
        code = base.split("_")[0]
        maker = _BP_SHIP_MAN.get(code, code.upper())
        typ = (members[0][3] or "").split(" \u00b7 ")[0]
        out.append([_bp_fam_name([n for _s, _k, n, _ in members], maker, typ), typ,
                    [[s, k, n] for s, k, n, _ in members]])
    out.sort(key=lambda f: f[0].lower())
    return out


def _bp_catalog_payload():
    """The whole crafting catalogue for the page, classified the same way owned rows
    are: [key, name, class, category, kind, default?, unlocking missions] — plus the
    armour sets and ship-weapon size families the collection board is drawn from."""
    if not BPS.ok:
        return None
    rows = []
    for r in BPS.rows:
        cat, kind = _bp_classify(r["c"], r["n"])
        rows.append([r["k"], r["n"], r["c"], cat, kind, 1 if r["d"] else 0, r["m"]])
    return {"version": BPS.version, "n": len(rows), "rows": rows,
            "sets": _bp_sets(rows), "fams": _bp_families(rows)}


def _pretty_shop(name, cats=None):
    low = (name or "").lower()
    brand = next((v for k, v in _SHOP_BRANDS if k in low), None)
    loc = next((v for k, v in _SHOP_LOC.items() if k in low), None)
    if brand:
        return f"{brand} · {loc}" if loc and loc not in brand else brand
    # no confirmed brand: shop TYPE from the name, else from what was bought there
    typ = next((v for k, v in _SHOP_TYPES if k in low), None)
    if not typ and cats:
        typ = cats.most_common(1)[0][0]
    if typ:
        return f"{typ} · {loc}" if loc and loc not in typ else typ
    n = re.sub(r"^sc?shop_", "", low)
    n = re.sub(r"[-_](small|base|a|b|c|\d+)$", "", n)
    n = re.sub(r"[-_](small|base|\d+)", " ", n)
    return " ".join(w.capitalize() for w in re.split(r"[_\s]+", n) if w).strip() or (name or "")


# generic ship-part words the item index returns when a ship class prefix-matches
# a sub-component (aegs_redeemer -> ..._bed -> "Bed"); reject these so a ship
# purchase never shows up as a piece of its own furniture.
_PART_WORDS = frozenset((
    "bed", "seat", "access", "station", "console", "manned turret", "turret",
    "mining arm", "ladder", "door", "hatch", "screen", "panel", "chair", "couch",
    "elevator", "lift", "storage", "cargo grid", "component", "interior"))


def purchase_display(cls):
    """Resolve a purchased item's class id to a readable name.

    Items (weapons, armour, consumables, mags, containers) come from the SC-Wiki
    item index; ships bought for aUEC (yes, ships are shop-buyable) come from the
    RSI matrix.  Ships are tried BEFORE the item index's fuzzy prefix fallback,
    which otherwise latches a ship class onto one of its sub-parts."""
    if not cls:
        return cls
    low = cls.lower()
    # 1. exact / lightly-cleaned item-index keys (precise, skin-stripped)
    for k in (low,
              re.sub(r"(_\d{2})(_\d{2})+$", r"\1", low),   # drop skin variant tail
              sc_names.strip_variant(low),
              re.sub(r"(_\d{2,})+$", "", low)):
        hit = sc_names.ITEMS.idx.get(k) if sc_names.ITEMS else None
        if hit:
            return sc_names._skin_strip(hit)
    # 2. ships (buyable for aUEC) — before the noisy prefix fallback
    if SHIPS.ok:
        rec = SHIPS.match(cls)
        if rec:
            return rec["name"]
    # 3. item-index prefix fallback, but never a bare ship-part word
    if sc_names.ITEMS:
        r = sc_names.ITEMS.resolve(low)
        if r and r.strip().lower() not in _PART_WORDS:
            return r
    # 4. prettify (drop trailing id/size segments)
    pretty = re.sub(r"\s+", " ", re.sub(r"(_\d{2,})+$", "", cls).replace("_", " ")).strip()
    return pretty.title() or cls


_SHIP_JUNK = ("default", "dummy", "placeholder", "template", "spawndummy",
              "test_", "_test", "prototype_",
              # ship sub-parts / modules that emit control tokens but aren't
              # flyable ships (e.g. DRAK_Command_Module), and NPC/AI variants
              "_module", "_ai_", "_npc")


def is_ship_junk(cls):
    """Placeholder / streaming / sub-part / NPC entities that show up on
    control-token lines but aren't real player-flyable ships (e.g. 'Default',
    'DRAK_Command_Module', a Vanduul '..._AI_VAN')."""
    c = (cls or "").lower()
    return any(j in c for j in _SHIP_JUNK)


def is_env(name):
    return (name or "").strip().lower() in ("", "unknown", "n/a")


def is_npc(name):
    """Distinguish game entities/NPCs from real player handles. Real RSI handles
    are simple tokens (at most one underscore); entities use multi-underscore
    descriptive names, type words, or numeric id segments."""
    n = (name or "").lower()
    if any(m in n for m in _NPC_MARK):
        return True
    if _NPCID_RE.search(name or ""):          # long spawn id suffix
        return True
    if (name or "").count("_") >= 2:          # e.g. Shipjacker_HUB_Medium_01_001
        return True
    if re.search(r"_\d{2,}$", name or ""):    # trailing numeric id segment
        return True
    return False


# ship vs FPS combat, decided by damage type first, then weapon
_FPS_DMG = ("bullet", "takedown", "melee", "bleedout", "knife")
_SHIP_DMG = ("vehicledestruction", "electricarc", "collision", "crash")
_FPS_WPN = ("rifle", "pistol", "smg", "lmg", "shotgun", "sniper", "knife",
            "melee", "gren", "grenade")
_SHIP_WPN = ("cannon", "repeater", "massdriver", "laser", "gatling", "distortion",
             "scattergun", "neutron", "ballistic_gun", "_s1", "_s2", "_s3", "_s4",
             "_s5", "_s6", "_s7", "_s8", "_s9", "_s10")


def combat_kind(weapon, dtype):
    """Return 'ship', 'fps', or None (environmental/self) for a legacy kill.
    The weapon identifies the tool most reliably; damage type is the fallback
    (e.g. an energy rifle deals 'ElectricArc' but is still an FPS weapon)."""
    w = (weapon or "").lower()
    if any(k in w for k in _FPS_WPN):
        return "fps"
    if any(k in w for k in _SHIP_WPN):
        return "ship"
    d = (dtype or "").lower()
    if d in _FPS_DMG:
        return "fps"
    if d in _SHIP_DMG:
        return "ship"
    return None
# Systems are read ONLY from location strings (OOC_/jumppoint_ zone names), never
# from arbitrary text — otherwise player names like "odinvinci"/"Devon_Nyx" leak in.
# Star Citizen currently has three: Stanton, Pyro, Nyx.
STARSYS = ("stanton", "pyro", "nyx")

# Classify held items. Guns vs tools/utility vs noise we drop entirely.
def _ts_prefix(line):
    # line like: <2026-06-29T15:07:59.037Z> ...
    if line[:1] == "<":
        return line[1:20]        # '2026-06-29T15:07:59'
    return None


def parse_dt(prefix):
    try:
        return datetime.strptime(prefix, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None


_MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
#: "Game Build(12248363) 19 Jul 26 (22 04 12).log" — the rotated name, in LOCAL time
LOGNAME_DT_RE = re.compile(
    r"(\d{1,2}) ([A-Za-z]{3}) (\d{2}) \((\d{2}) (\d{2}) (\d{2})\)\.log$", re.I)


def _utc_offset_secs(path, first_dt, mtime):
    """Seconds to add to a log's UTC timestamps to get the player's wall clock.

    Star Citizen stamps every line in UTC but names its rotated logs in local time,
    so the pair gives the offset that applied on the machine that WROTE the log —
    which is the right answer for a folder imported from a friend in another zone.
    The live `Game.log` carries no date in its name, so it falls back to this
    machine's own offset at the time the file was last written (DST included)."""
    m = LOGNAME_DT_RE.search(os.path.basename(path))
    if m and first_dt:
        try:
            local = datetime(2000 + int(m.group(3)), _MONTHS[m.group(2).lower()],
                             int(m.group(1)), int(m.group(4)), int(m.group(5)),
                             int(m.group(6)))
        except (ValueError, KeyError):
            local = None
        if local:
            # every real timezone is a whole number of quarter-hours from UTC, so
            # snapping there absorbs the second or two between the game creating the
            # file and writing its first line
            q = round((local - first_dt).total_seconds() / 900.0) * 900
            if abs(q) <= 14 * 3600:
                return q
    if mtime:
        return round((datetime.fromtimestamp(mtime)
                      - datetime.fromtimestamp(mtime, timezone.utc).replace(tzinfo=None)
                      ).total_seconds())
    return 0


def detect_patch(fh):
    """(patch, build) for a session.

    `patch` is what a player calls the version. The build's own branch tag wins when
    it carries one (a 1.0-branded client still running the 4.8 branch is a 4.8
    session); a named feature branch becomes its own patch; FileVersion is the last
    resort. `build` is the client's full version string — the only version a
    Tech-Preview feature branch has, so it's kept to show alongside its name."""
    fh.seek(0)
    fver = branch = build = None
    for i, line in enumerate(fh):
        if build is None:
            mb2 = FULLVER_RE.search(line)
            if mb2:
                build = mb2.group(1)
        m = ENV_RE.search(line)
        if m:
            return f"{m.group(1)}.{m.group(2)}", build
        m2 = BRANCH_RE.search(line)
        if m2:
            return f"{int(m2.group(1))}.{int(m2.group(2))}", build
        if branch is None and "Branch:" in line:
            mb = NAMED_BRANCH_RE.search(line.rstrip())
            if mb:
                branch = mb.group(1).lower()
        if fver is None:
            m3 = FILEVER_RE.search(line)
            if m3:
                fver = f"{int(m3.group(1))}.{int(m3.group(2))}"
        if i > 120:
            break
    return (branch or fver), build


def _lz_visits(stamps):
    """Distinct landing-zone visits from armistice-zone entries. The notification fires
    on every re-entry — walking in and out of a hangar gave twelve in one stay — so
    entries closer together than LZ_VISIT_GAP are one visit."""
    n, last = 0, None
    for t in sorted(stamps):
        if last is None or (t - last).total_seconds() > LZ_VISIT_GAP:
            n += 1
        last = t
    return n


def scan_log(path):
    """Extract one session's stats from a single Game.log file."""
    try:
        _st = os.stat(path)
        _mtime, _size = int(_st.st_mtime), _st.st_size
    except OSError:
        _mtime, _size = 0, 0
    patch = None
    first = last = None
    ships = set()
    ship_events = Counter()            # ship class -> control-token count (≈ flights)
    missions = {}                      # id -> completion type
    weapons = Counter()                # weapon class -> times DRAWN into hand
    reloads = Counter()                # weapon class -> reloads (AmmoRepool to 4.9; magazine port from 4.10)
    mag_sec = None                     # second currently being counted for attachment bursts
    mag_att_n = 0                      # AttachmentReceived lines in that second
    mag_pending = []                   # weapon classes whose magazine attached in that second
    mag_method = False                 # True once the patch is known to be 4.10+
    carried = Counter()                # weapon class -> times CARRIED (stowed/holstered)
    loot_boxes = Counter()             # container size -> crates looted (unique instances)
    loot_box_ids = set()               # container instance ids (dedup)
    loot_gear_ids = set()              # external wearable-gear inventories accessed (corpse loot)
    own_ids = set()                    # entity ids the player attached to self (owned gear)
    transfers = 0                      # Type[Move] inventory transfers
    contracts = Counter()
    death_ships = Counter()
    systems = set()
    shops = Counter()
    combat = {"pu": _mk_combat(), "ac": _mk_combat()}   # split by game mode
    venue = "pu"                       # current mode: 'pu' | 'ac' (from gamerules)
    deaths = 0
    collisions = 0
    qt = 0                             # quantum jumps completed (drive arrived)
    qt_targets = 0                     # quantum targets selected (intentions)
    hangars = 0                        # 'Hangar Request Completed' notifications
    juris = Counter()                  # jurisdiction -> times entered (HUD notification)
    lz_entries = []                    # armistice-zone entry stamps -> visits after dedupe
    contracts_acc = Counter()          # contract display name -> accepted
    contracts_done = Counter()         # contract display name -> completed
    contracts_failed = Counter()
    hud_n = 0                          # HUD notifications seen at all (era detection)
    pu_joins = []                      # times the client finished joining a PU server
    fleet_max = 0
    purchases = 0
    spend = 0.0                        # aUEC across item + commodity buys
    commodity_spend = 0.0
    commodity_buys = 0
    item_spend = Counter()             # item class -> aUEC
    item_qty = Counter()               # item class -> units
    shop_cat = defaultdict(Counter)    # shopName -> category -> count (what you buy where)
    buy_cat = Counter()                # purchase category -> count (what you buy)
    claims = 0
    blueprints = Counter()             # blueprint name -> times the HUD announced it (4.7+)
    bp_seen = set()                    # (notification id, name) already counted this session
    systems_incomplete = True
    handle = None
    handle_fb = None                   # fallback handle (reduced-logging builds)
    saw_login = False                  # did the normal Handle[…login] line appear?
    channel = None
    nlines = 0
    # --- machine profile / stability (startup header + exit line) ---
    rig = {}
    perf = None                        # [cpu_index, gpu_index] from the launch benchmark
    exit_cause = exit_reason = None
    _adp = None                        # adapter being described across the next few lines
    _adps = []                         # every adapter the client enumerated
    _in_hdr = True                     # header parsing stops once the benchmark is read
    saw_crash = False                  # SC's own crash handler ran in this session
    crash_at = None                    # UTC stamp of the last line before it did
    boots = []                         # dropped back to the main menu, mid-session
    boot_pending = None                # a drop awaiting its front-end confirmation
    boot_intent = None                 # last time the CLIENT asked to leave
    prof = {}                          # last Level Profile Statistics block (FPS etc.)
    net_events = Counter()             # non-benign disconnect codes seen
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            patch, build = detect_patch(fh)
            # 4.10 dropped the AmmoRepool inventory request that reloads were read from.
            # From there a reload is the magazine landing on the weapon's magazine port —
            # counted only when it arrives ALONE, because zone changes re-attach every
            # worn item in a burst of 20-50 lines in the same second, magazines included.
            # Checked against a session with six known magazines fired: six, exactly.
            mag_method = _ver_tuple(patch) >= (4, 10)
            fh.seek(0)
            for line in fh:
                nlines += 1
                p = _ts_prefix(line)
                if p:
                    if first is None:
                        first = p
                    last = p
                # --- startup rig banner: cheap, and switched off once we're past it ---
                if _in_hdr:
                    if "Host CPU:" in line:
                        m = HOSTCPU_RE.search(line)
                        if m:
                            rig["cpu"] = m.group(1)
                    elif "Logical CPU Count:" in line:
                        m = CORES_RE.search(line)
                        if m:
                            rig["cores"] = int(m.group(1))
                    elif "physical memory installed" in line:
                        m = RAM_RE.search(line)
                        if m:
                            rig["ram_mb"] = int(m.group(1))
                            rig["ram_free_mb"] = int(m.group(2))
                    elif "(vendor = 0x" in line:
                        m = ADAPTER_RE.search(line.split("> ", 1)[-1])
                        _adp = {"gpu": m.group(1)} if m else None
                    elif _adp is not None and "Dedicated video memory:" in line:
                        m = VRAM_RE.search(line)
                        if m:
                            _adp["vram_mb"] = int(m.group(1))
                    elif _adp is not None and "Suitable rendering device:" in line:
                        m = SUITABLE_RE.search(line)
                        # the adapter that actually renders is the one worth reporting
                        if m and m.group(1).lower() == "yes" and "gpu" not in rig:
                            rig.update(_adp)
                        # every adapter is remembered too, so we can tell later whether
                        # the game picked the best one (laptops routinely render on the
                        # integrated chip while a discrete card sits idle)
                        _adps.append(_adp)
                        _adp = None
                    elif "Driver version (UMD)" in line:
                        m = DRIVER_RE.search(line)
                        if m:
                            rig["driver"] = m.group(1)
                            rig.setdefault("api", "DirectX 11")
                    elif "Chosen Vulkan GPU Device" in line:
                        m = VKCHOSEN_RE.search(line)
                        if m:
                            rig["api"] = "Vulkan"    # overrides any D3D default
                            rig["driver"] = m.group(2)
                            if m.group(3):
                                rig["vk_api"] = m.group(3)
                    elif "Loaded Vulkan DLL" in line:
                        rig["api"] = "Vulkan"
                    elif "Current display mode is" in line:
                        m = DISPLAY_RE.search(line)
                        if m:
                            rig["res"] = f"{m.group(1)}x{m.group(2)}"
                    elif "64 bit (build" in line:
                        m = OSVER_RE.search(line.split("> ", 1)[-1])
                        if m:
                            rig["os_build"] = m.group(1)
                    elif "DLSS Support" in line:
                        m = DLSS_RE.search(line)
                        if m:
                            rig["dlss"] = m.group(1)
                    elif "Performance Index:" in line:
                        m = PERFIDX_RE.search(line)
                        if m:
                            perf = [float(m.group(1)), float(m.group(2))]
                    elif nlines > 900:
                        # Fixed line bound rather than stopping at the benchmark: builds
                        # order the banner differently, and driver/DLSS sit AFTER the
                        # Performance Index in the 2026 builds. The whole banner lands
                        # inside the first ~150 lines, so 900 is slack, not cost.
                        _in_hdr = False
                # how the session ended (near EOF, so checked on every line)
                if exit_cause is None and "CSystem::Quit" in line:
                    m = QUIT_RE.search(line)
                    if m:
                        exit_cause = m.group(1)
                        exit_reason = m.group(2).strip()
                elif not saw_crash and CRASH_MARK in line:
                    saw_crash = True
                    # the handler's own banner carries no timestamp, so the newest
                    # stamped line before it is the moment the game went down
                    crash_at = last
                # performance: keep the LAST profile block (one is written per level)
                if "CPU (" in line and " FPS (" in line:
                    m = PROF_FPS_RE.search(line)
                    if m:
                        # buckets are percentages of frames under 16/20/25/…/50 ms,
                        # with the final column being everything slower — the hitches
                        buckets = [b.strip().rstrip("%") for b in m.group(4).split("|")]
                        buckets = [float(b) for b in buckets if b]
                        prof[m.group(1)] = [float(m.group(2)), float(m.group(3)), buckets]
                elif "Entities  " in line and "Min:" in line:
                    m = PROF_ENT_RE.search(line)
                    if m:
                        prof["ent"] = [int(m.group(1)), int(m.group(2)), int(m.group(3))]
                elif "Memory (WorkingSet)" in line:
                    m = PROF_WSET_RE.search(line)
                    if m:
                        prof["ws"] = [float(m.group(1)), float(m.group(2))]
                elif "Frames  " in line:
                    m = PROF_FRAMES_RE.search(line)
                    if m:
                        prof["frames"] = int(m.group(1))
                # network faults worth surfacing — benign per-session chatter excluded
                elif "cause=" in line and "reason=" in line:
                    m = DISCO_RE.search(line)
                    if m and m.group(1) not in BENIGN_NET_CODES:
                        net_events[m.group(1)] += 1
                    if BOOT_DISCO_RE.search(line):
                        _bt = parse_dt(p) if p else None
                        if _bt and not (boot_intent and
                                        0 <= (_bt - boot_intent).total_seconds()
                                        <= BOOT_INTENT_WINDOW):
                            boot_pending = _bt
                elif boot_pending is not None and BOOT_FRONTEND in line:
                    _ft = parse_dt(p) if p else None
                    if _ft and 0 <= (_ft - boot_pending).total_seconds() <= BOOT_FRONTEND_WINDOW:
                        boots.append(boot_pending)
                    boot_pending = None
                if "Quit" in line or "Disconnect" in line:
                    if any(k in line for k in BOOT_INTENT):
                        boot_intent = parse_dt(p) if p else boot_intent
                if all(k in line for k in PU_JOIN):
                    _pj = parse_dt(p) if p else None
                    if _pj:
                        pu_joins.append(_pj)
                # cheap substring gates before any regex
                if channel is None and "Bin64" in line:      # the Executable: line
                    m = EXE_CHANNEL_RE.search(line)
                    if m:
                        channel = _channel_label(m.group(1))
                if "Handle[" in line and "login" in line.lower():
                    saw_login = True                       # normal login-line logging present
                    if handle is None:
                        m = LOGIN_RE.search(line)
                        if m:
                            handle = m.group(1)
                elif handle is None and handle_fb is None and "owned by '" in line and "LocalPlayer" in line:
                    m = LOCAL_HANDLE_RE.search(line)
                    if m:
                        handle_fb = m.group(1)
                if 'gamerules="' in line:
                    gm = GAMERULES_RE.search(line)
                    if gm:
                        g = gm.group(1)
                        if g[:3] == "EA_":          # Arena Commander modes
                            venue = "ac"
                        elif g == "SC_Default":      # persistent universe
                            venue = "pu"
                        # SC_Frontend (menu) leaves the venue unchanged
                if "control token for" in line:
                    m = SHIP_RE.search(line)
                    if m:
                        ships.add(m.group(1))
                        ship_events[m.group(1)] += 1   # each pilot-seat entry ≈ a flight
                elif "<EndMission" in line:
                    m = MISSION_RE.search(line)
                    if m:
                        missions[m.group(1)] = m.group(2)
                elif "Received Blueprint:" in line:
                    # one receipt is 2-4 lines (queue, continuation, lifecycle) sharing an id
                    m = BLUEPRINT_RE.search(line)
                    if m and (m.group(2), m.group(1)) not in bp_seen:
                        bp_seen.add((m.group(2), m.group(1)))
                        blueprints[_bp_clean(m.group(1))] += 1
                elif DEAD_RE in line:
                    deaths += 1
                    m = DEAD_ZONE_RE.search(line)
                    if m:
                        death_ships[m.group(1)] += 1
                elif COLLISION_RE in line and "PlayerPilot: 1" in line:
                    collisions += 1
                elif QT_ARRIVE in line:
                    qt += 1
                elif QT_RE in line:
                    qt_targets += 1
                elif 'Added notification "' in line:
                    nm = NOTIF_RE.search(line)
                    if nm:
                        hud_n += 1
                        txt = _notif_clean(nm.group(1))
                        if txt.startswith("Hangar Request Completed"):
                            hangars += 1
                        elif txt.startswith("Entering Armistice Zone"):
                            _lz = parse_dt(p) if p else None
                            if _lz:
                                lz_entries.append(_lz)
                        else:
                            jm = JURIS_RE.match(txt)
                            if jm:
                                juris[(jm.group(1) or jm.group(2)).strip()] += 1
                            else:
                                cm = CONTRACT_NOTIF_RE.match(txt)
                                if cm:
                                    (contracts_acc if cm.group(1) == "Accepted" else
                                     contracts_done if cm.group(1) == "Complete" else
                                     contracts_failed)[cm.group(2)] += 1
                elif "AttachmentReceived" in line:
                    if mag_method:
                        sec = line[1:20]                       # <YYYY-MM-DDTHH:MM:SS
                        if sec != mag_sec:
                            if mag_att_n <= 2:                 # a quiet second: those were real swaps
                                for wcls in mag_pending:
                                    reloads[wcls] += 1
                            mag_sec, mag_att_n, mag_pending = sec, 0, []
                        mag_att_n += 1
                        if "Port[magazine_attach]" in line and "Status[persistent]" in line:
                            mm = ATTACH_RE.search(line)
                            if mm and mm.group(1).strip().endswith("_mag"):
                                mag_pending.append(mm.group(1).strip()[:-4])
                    mo = ATTACH_ID_RE.search(line)
                    if mo:
                        own_ids.add(mo.group(1))       # this entity is gear the player owns
                    if ("weapon_attach_hand" in line or "wep_stocked" in line
                            or "wep_sidearm" in line):
                        m = ATTACH_RE.search(line)
                        if m:
                            cls = m.group(1).strip()
                            if "weapon_attach_hand" in line:
                                weapons[cls] += 1      # drawn into hand
                            else:
                                carried[cls] += 1      # stowed on back / holstered = carried
                # One inventory request spans several log lines, and the wording changed
                # between eras: 4.1 writes "Request[N] Type[X] Player[…]", later builds
                # write "Queued Request[N] Type[X] for 'handle'". The <InventoryManagement‐
                # Request> tag carrying a Source[itemclass] appears exactly ONCE per request
                # in both, so it is the format-agnostic way to count each action a single time.
                # 4.10 renamed the tag: <InventoryManagementRequest> became
                # <Inventory Mgmt Request Queued>, with the "Queued Request[N] Type[X] …
                # Source[class]" text unchanged. Both are accepted; the 4.10 sessions read
                # so far show no AmmoRepool requests at all, so reloads may simply no
                # longer be logged — if they are, this still counts them.
                elif ("<InventoryManagementRequest>" in line or "<Inventory Mgmt Request Queued>" in line) and "AmmoRepool" in line:
                    m = AMMO_RE.search(line)
                    if m:
                        # magazine class -> weapon class (drop the trailing _mag)
                        reloads[re.sub(r"_mag$", "", m.group(1))] += 1
                elif ("<InventoryManagementRequest>" in line or "<Inventory Mgmt Request Queued>" in line) and "Type[Move]" in line:
                    if MOVE_RE.search(line):
                        transfers += 1                 # item moved between inventories
                elif "Requesting access token" in line and "on Inventory[" in line:
                    m = LOOT_RE.search(line)
                    if m:
                        cls, eid = m.group(1), m.group(2)
                        if cls.startswith("Lootable_Container"):
                            if eid not in loot_box_ids:
                                loot_box_ids.add(eid)
                                low = cls.lower()
                                size = ("Large" if "large" in low else "Medium" if "medium" in low
                                        else "Small" if "small" in low else "Other")
                                loot_boxes[size] += 1
                        elif any(w in cls.lower() for w in _GEAR_WORDS):
                            loot_gear_ids.add(eid)     # corpse-loot candidate; filtered vs own_ids
                elif "contract [" in line:
                    m = CONTRACT_RE.search(line)
                    if m:
                        contracts[m.group(1)] += 1
                elif "entitlements out of" in line:
                    m = FLEET_RE.search(line)
                    if m:
                        fleet_max = max(fleet_max, int(m.group(1)))
                elif "BuyRequest" in line and "Sending" in line:
                    # covers old SendStandardItemBuyRequest (4.1-4.7) and new
                    # SendShopBuyRequest / SendCommodityBuyRequest (4.8+)
                    purchases += 1
                    sm = SHOPNAME_RE.search(line)
                    shop = sm.group(1) if sm else None
                    if shop:
                        shops[shop] += 1
                    if "Commodity" in line:          # cargo/trade good (resourceGUID)
                        cm = COMMBUY_RE.search(line)
                        if cm:
                            amt = float(cm.group(1))
                            spend += amt
                            commodity_spend += amt
                            commodity_buys += 1
                            buy_cat["Cargo"] += 1          # commodities are cargo/trade goods
                            if shop:
                                shop_cat[shop]["Commodities"] += 1
                    else:                            # named item / ship purchase
                        im = ITEMBUY_RE.search(line)
                        if im:
                            price = float(im.group(1))
                            iname = im.group(2).strip()
                            spend += price
                            item_spend[iname] += price
                            item_qty[iname] += int(im.group(3))
                            buy_cat[_purchase_category(iname)] += 1
                            if shop:
                                shop_cat[shop][_item_category(iname)] += 1
                elif "New Insurance Claim Request" in line:
                    claims += 1     # old insurance system only (pre-4.8)
                elif "CActor::Kill" in line:
                    m = ACTOR_KILL_RE.search(line)
                    if m and handle:
                        victim, _z, killer, weapon, dtype = m.groups()
                        vic_me, kil_me = victim == handle, killer == handle
                        kind = combat_kind(weapon, dtype)      # 'ship' | 'fps' | None
                        C = combat[venue]
                        if vic_me and (kil_me or dtype.lower() in ("suicide", "selfdestruct")):
                            C["lc"]["suicide"] += 1
                        elif vic_me:
                            pvp = not (is_npc(killer) or is_env(killer))
                            if pvp:
                                C["killedby"][killer] += 1
                            if kind:
                                C["lc"]["%s_death_%s" % (kind, "pvp" if pvp else "pve")] += 1
                        elif kil_me:
                            pvp = not (is_npc(victim) or is_env(victim))
                            if pvp:
                                C["killed"][victim] += 1
                            ramship = ram_ship(weapon)     # ship used to ram them
                            if ramship:
                                C["lc"]["ram_kill_%s" % ("pvp" if pvp else "pve")] += 1
                                C["ram"][ramship] += 1
                            good_wpn = weapon and weapon.lower() not in ("unknown", "", handle.lower())
                            if kind:
                                C["lc"]["%s_kill_%s" % (kind, "pvp" if pvp else "pve")] += 1
                                if good_wpn and not ramship:
                                    (C["wpn_ship"] if kind == "ship" else C["wpn_fps"])[weapon] += 1
                elif "OnAdvanceDestroyLevel" in line:
                    m = VDESTROY_RE.search(line)
                    if m and handle:
                        _veh, _z, driver, _frm, to, causer = m.groups()
                        if int(to) >= 2:
                            if causer == handle and driver != handle:
                                combat[venue]["lc"]["veh_kills"] += 1
                            if driver == handle:
                                combat[venue]["lc"]["veh_losses"] += 1
                # systems: only from unambiguous world-location strings
                if ("OOC_" in line or "jumppoint_" in line) and systems_incomplete:
                    ll = line.lower()
                    for sysname in STARSYS:
                        if "_" + sysname in ll or sysname + "_" in ll:
                            systems.add(sysname.title())
                    systems_incomplete = len(systems) < 3
    except Exception as e:
        print(f"  ! error {os.path.basename(path)}: {e}", file=sys.stderr)

    if handle is None:                 # no normal login line — use the fallback
        handle = handle_fb

    # Did the game render on the best card it could see? Storing just the verdict
    # (rather than the whole adapter list, on every session, forever) keeps the
    # archive small. A card with MORE dedicated memory than the one actually chosen
    # is the reliable tell for the integrated-GPU and software-fallback cases alike.
    if _adps and rig.get("gpu"):
        best = max(_adps, key=lambda a: a.get("vram_mb") or 0)
        if (best.get("gpu") != rig.get("gpu")
                and (best.get("vram_mb") or 0) > (rig.get("vram_mb") or 0)):
            rig["gpu_better"] = best.get("gpu")
            rig["gpu_better_vram"] = best.get("vram_mb")

    dt0, dt1 = parse_dt(first), parse_dt(last)
    # Every line the game logs is stamped in UTC, but a player thinks in wall clock —
    # "it crashed at 10pm", not "at 15:16Z". Shift once, here, so every date, hour and
    # crash time downstream is already in the clock the session was actually played on.
    tz_off = _utc_offset_secs(path, dt0, _mtime)
    if tz_off:
        if dt0:
            dt0 += timedelta(seconds=tz_off)
        if dt1:
            dt1 += timedelta(seconds=tz_off)
    # A drop counts only if play carried on afterwards. One that lands within two
    # minutes of the last line is the player quitting, which looks identical.
    # A return to the menu counts only if the player then joined a server again. The old
    # test — "the log kept growing for two minutes" — also passed a slow log-off that sat
    # in the front end for 2½ minutes before quitting.
    boots = [b for b in boots if any(j > b for j in pu_joins)]
    boots = [(b + timedelta(seconds=tz_off)).isoformat(timespec="seconds") for b in boots]

    dur = 0.0
    if dt0 and dt1 and dt1 > dt0:
        dur = (dt1 - dt0).total_seconds()
        if dur > 12 * 3600:            # guard against a log left open for days
            dur = 0.0

    # "limited data" = a real-length session that produced NO gameplay signal AND
    # is missing the normal Handle[…login] line — the signature of a game build
    # that logged far less than usual (e.g. some 4.10 PTU builds). Requiring the
    # missing login line is what separates this from an ordinary quiet/AFK session
    # on a normal build (those still log the login). Playtime counts; the rest is
    # simply absent from the log.
    # corpse loot = external wearable-gear inventories accessed that the player never
    # equipped (own worn gear also emits access-token lines, so we subtract it out).
    corpse_loots = len(loot_gear_ids - own_ids)

    gameplay = bool(ships or weapons or missions or contracts
                    or deaths or purchases or qt or fleet_max or loot_boxes or transfers)
    limited = (dur >= 900) and not saw_login and not gameplay

    # How the session ended. 30016 is the player leaving (console quit or closing the
    # window); 30024 is CIG's back end going unresponsive. No exit line at all means
    # the process died without shutting down — a crash, a kill, or lost power.
    # Exception: the LIVE "Game.log" is the file the game is writing right now, so a
    # missing exit line there just means "still running". Calling that a crash would
    # invent one every single scan. A genuinely crashed session whose log is still
    # named Game.log is therefore counted as 'open' too — undercounting crashes is the
    # safe direction, since the alternative is accusing the game of a crash it may not
    # have had.
    # SC's own crash handler is positive proof, so it outranks every inference below.
    crash = None
    if saw_crash:
        try:
            with open(path, "rb") as fh:
                sz = fh.seek(0, 2)
                fh.seek(max(0, sz - 40000))
                crash = parse_crash_report(fh.read().decode("utf-8", "replace"))
        except OSError:
            crash = None
        crash = crash or {"exception": "UNKNOWN"}
        _cat = parse_dt(crash_at)
        if _cat:
            crash["at"] = (_cat + timedelta(seconds=tz_off)).isoformat(timespec="seconds")
        outcome = "crash"
    elif exit_cause == "30016":
        outcome = "clean"
    elif exit_cause == "30024":
        outcome = "backend"
    elif exit_cause:
        outcome = "other"
    elif os.path.basename(path).lower() == "game.log":
        outcome = "open"
    else:
        # died without writing a report — a kill, a power cut, or the crash handler
        # itself went down. Kept apart from confirmed crashes rather than merged.
        outcome = "died"

    if mag_method and mag_att_n <= 2:          # the last second of the log
        for wcls in mag_pending:
            reloads[wcls] += 1
    return {
        "patch": patch, "build": build, "start": dt0, "dur": dur,
        "ships": ships, "ship_events": ship_events, "missions": missions, "weapons": weapons,
        "reloads": reloads, "carried": carried,
        "loot_boxes": loot_boxes, "transfers": transfers, "corpse_loots": corpse_loots,
        "deaths": deaths, "collisions": collisions, "death_ships": death_ships,
        "qt": qt, "qt_targets": qt_targets, "contracts": contracts, "systems": systems,
        "hangars": hangars, "juris": dict(juris), "hud_n": hud_n,
        "lz_visits": _lz_visits(lz_entries),
        "contracts_acc": dict(contracts_acc), "contracts_done": dict(contracts_done),
        "contracts_failed": dict(contracts_failed),
        "fleet_max": fleet_max, "purchases": purchases, "claims": claims,
        "blueprints": dict(blueprints),
        "shops": shops, "combat": combat,
        "spend": spend, "commodity_spend": commodity_spend,
        "commodity_buys": commodity_buys,
        "item_spend": item_spend, "item_qty": item_qty,
        "shop_cat": shop_cat, "buy_cat": buy_cat,
        "handle": handle,
        # machine profile + launch benchmark + how the session ended
        "rig": rig or None,
        "perf": perf,
        "outcome": outcome,
        "exit_reason": exit_reason,
        "crash": crash,                # SC's own post-mortem, when it ran
        "prof": prof or None,          # FPS / frame-time histogram for the session
        "net_events": dict(net_events),
        "boots": boots,                # times the server dropped you to the main menu
        "limited": limited,            # session ran on a reduced-logging build
        # the channel this session ACTUALLY ran on (from its Executable: path),
        # so attribution survives logs being copied between channel folders.
        "channel": channel,
        # session fingerprint for cross-folder dedup: two byte-identical copies of
        # the same session (e.g. a LIVE log copied into the PTU folder) share the
        # same start/end timestamp and line count, so they collapse to one.
        "fp": (first, last, nlines),
        # provenance, so a quick refresh can skip files that haven't changed since
        # they were parsed (see _file_key / scan_channels).
        "_src": os.path.basename(path),
        "_mt": _mtime, "_sz": _size, "_pv": PARSER_VERSION,
    }


# --------------------------------------------------------------------------- #
#  Aggregation
# --------------------------------------------------------------------------- #

def blank_patch():
    return {
        "sessions": 0, "seconds": 0.0, "longest": 0.0,
        "days": set(), "months": defaultdict(float),
        "by_hour": [0] * 24, "by_dow": [0] * 7,
        "weekhour": [[0] * 24 for _ in range(7)],
        "durations": [],
        "ships": Counter(), "ship_set": set(), "ship_first": {}, "ship_last": {},
        "ship_events": Counter(),
        "missions": {}, "weapons": Counter(),
        "reloads": Counter(), "carried": Counter(),
        "loot_boxes": Counter(), "transfers": 0, "corpse_loots": 0,
        "contracts": Counter(), "systems": Counter(),
        "death_ships": Counter(),
        "deaths": 0, "collisions": 0, "qt": 0, "qt_targets": 0, "fleet_max": 0,
        "hangars": 0, "lz_visits": 0, "hud_n": 0,
        "juris": Counter(), "juris_sessions": Counter(),
        "contracts_acc": Counter(), "contracts_done": Counter(), "contracts_failed": Counter(),
        "purchases": 0, "claims": 0, "shops": Counter(),
        # blueprints (4.7+): receipts per name, first/last date, sessions seen in, per month
        "bp_recv": Counter(), "bp_first": {}, "bp_last": {}, "bp_sessions": Counter(),
        "bp_months": Counter(),
        "spend": 0.0, "commodity_spend": 0.0, "commodity_buys": 0,
        "item_spend": Counter(), "item_qty": Counter(),
        "shop_cat": defaultdict(Counter), "buy_cat": Counter(),
        "combat": {"pu": _mk_combat(), "ac": _mk_combat()},
        "first": None, "last": None, "build": None, "build_at": None,
        "limited_sessions": 0, "limited_seconds": 0.0,
        # machine profile / stability
        "rig_events": [],              # (date, rig) per session, for the spec timeline
        "perf": [],                    # (date, cpu_index, gpu_index) launch benchmarks
        "outcomes": Counter(),         # clean | backend | crash | died | other | open
        "crashes": [],                 # (date, crash dict) — SC's own post-mortems
        "digests": Counter(),          # crash signature -> times hit
        "fps": [],                     # (date, avg fps, avg ms, hitch %) per session
        "net_events": Counter(),       # non-benign disconnect codes
        "boots": [],                   # local-time stamps of drops to the main menu
        "boot_sessions": 0,            # sessions that suffered at least one
    }


def fold(agg, s):
    if s.get("handle"):
        _KNOWN_HANDLES.add(s["handle"].lower().strip())
    agg["sessions"] += 1
    agg["seconds"] += s["dur"]
    agg["longest"] = max(agg["longest"], s["dur"])
    if s.get("limited"):
        agg["limited_sessions"] += 1
        agg["limited_seconds"] += s["dur"]
    agg["deaths"] += s["deaths"]
    agg["collisions"] += s["collisions"]
    agg["qt"] += s["qt"]
    agg["qt_targets"] += s.get("qt_targets", 0)
    agg["hangars"] += s.get("hangars", 0)
    agg["lz_visits"] += s.get("lz_visits", 0)
    agg["hud_n"] += s.get("hud_n", 0)
    _j = s.get("juris") or {}
    agg["juris"].update(_j)
    for k in _j:
        agg["juris_sessions"][k] += 1
    agg["contracts_acc"].update(s.get("contracts_acc") or {})
    agg["contracts_done"].update(s.get("contracts_done") or {})
    agg["contracts_failed"].update(s.get("contracts_failed") or {})
    agg["fleet_max"] = max(agg["fleet_max"], s["fleet_max"])
    agg["purchases"] += s["purchases"]
    agg["claims"] += s["claims"]
    agg["shops"].update(s["shops"])
    agg["spend"] += s["spend"]
    agg["commodity_spend"] += s["commodity_spend"]
    agg["commodity_buys"] += s["commodity_buys"]
    agg["item_spend"].update(s["item_spend"])
    agg["item_qty"].update(s["item_qty"])
    for shop, cats in s["shop_cat"].items():
        agg["shop_cat"][shop].update(cats)
    agg["buy_cat"].update(s["buy_cat"])
    for v in ("pu", "ac"):
        for key, ctr in s["combat"][v].items():
            agg["combat"][v][key].update(ctr)
    if s["dur"] > 0:
        agg["durations"].append(round(s["dur"] / 60))
    for ship in s["ships"]:
        agg["ships"][ship] += 1
        agg["ship_set"].add(ship)
    agg["ship_events"].update(s.get("ship_events", {}))   # control-token count (≈ flights)
    for mid, ctype in s["missions"].items():
        agg["missions"][mid] = ctype          # last completion type wins
    # The build shown for a scope is the one from its most RECENT session. It used to be
    # the numerically largest version string, which broke in 4.10 when CIG rebranded the
    # client's FileVersion from 4.9.188.x to 1.0.191.x — a numeric compare would have
    # pinned "4.9.188" as the latest build for as long as the archive existed.
    b = s.get("build")
    if b:
        st = s.get("start")
        if not agg.get("build") or (st and (agg.get("build_at") is None or st > agg["build_at"])):
            agg["build"] = b
            agg["build_at"] = st or agg.get("build_at")
    agg["weapons"].update(s["weapons"])
    agg["reloads"].update(s.get("reloads", {}))
    agg["carried"].update(s.get("carried", {}))
    agg["loot_boxes"].update(s.get("loot_boxes", {}))
    agg["transfers"] += s.get("transfers", 0)
    agg["corpse_loots"] += s.get("corpse_loots", 0)
    agg["contracts"].update(s["contracts"])
    agg["death_ships"].update(s["death_ships"])
    agg["outcomes"][s.get("outcome") or "unknown"] += 1
    _d = s["start"].date().isoformat() if s.get("start") else None
    if s.get("rig"):
        agg["rig_events"].append((_d, s["rig"]))
    if s.get("perf"):
        agg["perf"].append((_d, s["perf"][0], s["perf"][1]))
    if s.get("crash"):
        agg["crashes"].append((_d, s["crash"]))
        if s["crash"].get("digest"):
            agg["digests"][s["crash"]["digest"]] += 1
    _p = s.get("prof") or {}
    _wf = _p.get("WholeFrame") or _p.get("MainThread")
    if _wf and _wf[0]:
        # the final bucket is frames slower than 50 ms — the ones you actually feel
        hitch = _wf[2][-1] if len(_wf) > 2 and _wf[2] else 0
        agg["fps"].append((_d, _wf[0], _wf[1], hitch))
    _bp = s.get("blueprints") or {}
    if _bp:
        agg["bp_recv"].update(_bp)
        for nm in _bp:
            agg["bp_sessions"][nm] += 1
        if _d:
            for nm in _bp:
                if nm not in agg["bp_first"] or _d < agg["bp_first"][nm]:
                    agg["bp_first"][nm] = _d
                if nm not in agg["bp_last"] or _d > agg["bp_last"][nm]:
                    agg["bp_last"][nm] = _d
            agg["bp_months"][_d[:7]] += sum(_bp.values())
    agg["net_events"].update(s.get("net_events") or {})
    _bt = s.get("boots") or []
    if _bt:
        agg["boots"].extend(_bt)
        agg["boot_sessions"] += 1
    for sys_ in s["systems"]:
        agg["systems"][sys_] += 1
    dt = s["start"]
    if dt:
        d = dt.date().isoformat()
        agg["days"].add(d)
        agg["months"][dt.strftime("%Y-%m")] += s["dur"]
        agg["by_hour"][dt.hour] += 1
        agg["by_dow"][dt.weekday()] += 1
        agg["weekhour"][dt.weekday()][dt.hour] += 1
        for ship in s["ships"]:
            if ship not in agg["ship_first"] or d < agg["ship_first"][ship]:
                agg["ship_first"][ship] = d
            if ship not in agg["ship_last"] or d > agg["ship_last"][ship]:
                agg["ship_last"][ship] = d
        if agg["first"] is None or dt < agg["first"]:
            agg["first"] = dt
        if agg["last"] is None or dt > agg["last"]:
            agg["last"] = dt


#: fields whose change means the MACHINE changed. Deliberately excludes driver and
#: renderer: those are per-session software settings — a player who alternates between
#: the DX11 and Vulkan builds would otherwise generate a "hardware change" every launch.
_RIG_SIG = ("cpu", "gpu", "vram_mb", "ram_mb", "res", "os_build")
#: human labels for those fields, used by the change timeline
_RIG_LABEL = {"cpu": "Processor", "gpu": "Graphics card", "vram_mb": "Video memory",
              "ram_mb": "System memory", "res": "Resolution", "os_build": "Windows build"}


def _rig_summary(events):
    """(latest rig, hardware timeline, renderer split, driver history).

    The timeline names only the fields that actually changed between consecutive
    configurations, so a RAM upgrade reads as a RAM upgrade rather than as a whole
    new machine. Driver versions are tracked per renderer because DirectX and Vulkan
    report entirely different numbering for the same physical driver (32.00.15.9649
    vs 596.36.0.0) — comparing them across APIs would invent upgrades."""
    ev = sorted((e for e in events if e[0]), key=lambda e: e[0])
    if not ev:
        return (events[-1][1] if events else None), [], {}, []
    # Memory sizes are quantised: the driver reports the same 16 GB card as 16047 MB
    # one month and 16045 the next, which is not an upgrade. 256 MB granularity keeps
    # real changes (a new card, another DIMM) while dropping that jitter.
    def sig(r):
        out = []
        for k in _RIG_SIG:
            v = r.get(k)
            out.append(v // 256 if k in ("vram_mb", "ram_mb") and isinstance(v, int) else v)
        return tuple(out)
    changes, prev, seen = [], None, set()
    renderer = Counter()
    drv = {}                              # (api, driver) -> [first, last, sessions]
    for date, r in ev:
        s = sig(r)
        if prev is None:
            prev = s
        elif s != prev:
            diff = [_RIG_LABEL[k] for i, k in enumerate(_RIG_SIG)
                    if s[i] != prev[i] and s[i] is not None and prev[i] is not None]
            # One row per day per kind of change: toggling resolution three times in
            # an evening is one "you changed resolution", not three upgrades.
            if diff and (date, tuple(diff)) not in seen:
                seen.add((date, tuple(diff)))
                changes.append([date, diff,
                                {k: r.get(k) for k in _RIG_SIG if r.get(k) is not None}])
            prev = s
        if r.get("api"):
            renderer[r["api"]] += 1
        if r.get("driver"):
            k = (r.get("api") or "?", r["driver"])
            if k in drv:
                drv[k][1] = date
                drv[k][2] += 1
            else:
                drv[k] = [date, date, 1]
    drivers = sorted(([a, d] + v for (a, d), v in drv.items()), key=lambda x: x[2])
    return ev[-1][1], changes[-12:], dict(renderer), drivers[-14:]


def _perf_summary(perf):
    """Mean/min/max of the launch benchmark. The per-session number is noisy —
    the spread is returned alongside the mean so the UI can show it, rather than
    letting one unlucky launch read as a verdict on the machine."""
    if not perf:
        return None
    cpu = [p[1] for p in perf]
    gpu = [p[2] for p in perf]
    mean = lambda xs: round(sum(xs) / len(xs), 1)
    return {"n": len(perf),
            "cpu": mean(cpu), "cpu_min": round(min(cpu), 1), "cpu_max": round(max(cpu), 1),
            "gpu": mean(gpu), "gpu_min": round(min(gpu), 1), "gpu_max": round(max(gpu), 1)}


def finalize(agg):
    miss = Counter(agg["missions"].values())
    complete = miss.get("Complete", 0)
    fail = miss.get("Fail", 0)
    abandon = miss.get("Abandon", 0)
    total_m = complete + fail + abandon

    # ---- ships: resolve to real names via RSI matrix, merge variants ----
    ship_named = Counter()
    ship_size, ship_role = Counter(), Counter()
    for cls, c in agg["ships"].items():
        if is_ship_junk(cls):
            continue
        name, man, rec = SHIPS.display(cls)
        ship_named[name] += c
        if rec:
            if rec.get("size"):
                ship_size[rec["size"].strip().title()] += c
            if rec.get("focus"):
                ship_role[rec["focus"].strip()] += c
    # first/last date each ship (by name) was flown in this scope
    ship_first_named, ship_last_named = {}, {}
    for cls, d in agg["ship_first"].items():
        if is_ship_junk(cls):
            continue
        nm = SHIPS.display(cls)[0]
        if nm not in ship_first_named or d < ship_first_named[nm]:
            ship_first_named[nm] = d
    for cls, d in agg["ship_last"].items():
        if is_ship_junk(cls):
            continue
        nm = SHIPS.display(cls)[0]
        if nm not in ship_last_named or d > ship_last_named[nm]:
            ship_last_named[nm] = d
    # take-offs (control-token count) per ship name — times you took the pilot seat
    ship_takeoffs = Counter()
    for cls, c in agg["ship_events"].items():
        if is_ship_junk(cls):
            continue
        ship_takeoffs[SHIPS.display(cls)[0]] += c
    # most-flown ships: [name, sessions, first_date, last_date, takeoffs]
    top_ships = [[n, c, ship_first_named.get(n), ship_last_named.get(n), ship_takeoffs.get(n, 0)]
                 for n, c in ship_named.most_common(12)]

    # ---- weapons: resolve names, drop noise, merge variants, split gun/tool ----
    gun_named, tool_named = Counter(), Counter()
    gun_meta = {}
    for cls, c in agg["weapons"].items():
        if sc_names.is_noise(cls):
            continue
        name, kind = sc_names.weapon_display(cls)
        if kind is None:          # armour / attachment / cosmetic / melee — not rankable
            continue
        if kind == "tool":
            tool_named[name] += c
        else:
            gun_named[name] += c
            if name not in gun_meta:
                tok = (cls or "").split("_")[0].lower()
                man = tok.upper() if tok in sc_names.WEAPON_MAN else ""
                gun_meta[name] = list(sc_names.gun_class_labels(cls, name)) + [man]
    # reloads (from AmmoRepool) & carry (stowed/holstered) resolved to gun names,
    # reusing the same display resolver so they line up with the drawn-weapon names.
    def _resolve_guns(src):
        out = Counter()
        for cls, c in src.items():
            if sc_names.is_noise(cls):
                continue
            name, kind = sc_names.weapon_display(cls)
            if kind == "gun":
                out[name] += c
        return out
    gun_reloads = _resolve_guns(agg["reloads"])
    gun_carried = _resolve_guns(agg["carried"])
    guns = gun_named.most_common(12)
    tools = tool_named.most_common(8)
    top_w = [[n, c, False] for n, c in guns] + [[n, c, True] for n, c in tools]

    # ---- mission types (from contract-name keywords) ----
    mtypes = Counter()
    for root, c in agg["contracts"].items():
        mtypes[mission_type(root)] += c
    # ---- death ships resolved to names ----
    dship = Counter()
    for cls, c in agg["death_ships"].items():
        dship[SHIPS.display(cls)[0]] += c
    # ---- session-length distribution buckets (minutes) ----
    buckets = [("<15m", 0, 15), ("15-30m", 15, 30), ("30-60m", 30, 60),
               ("1-2h", 60, 120), ("2-4h", 120, 240), ("4h+", 240, 99999)]
    dist = [0] * len(buckets)
    for d in agg["durations"]:
        for i, (_, lo, hi) in enumerate(buckets):
            if lo <= d < hi:
                dist[i] += 1
                break
    deaths = agg["deaths"]
    coll = agg["collisions"]

    # ---- combat (pre-2026 only), split by venue (PU vs Arena Commander) ----
    def _venue_combat(C):
        lc = C["lc"]

        def _mode(kind):
            kp, kv = lc[kind + "_kill_pvp"], lc[kind + "_kill_pve"]
            dp, dv = lc[kind + "_death_pvp"], lc[kind + "_death_pve"]
            kills, deaths = kp + kv, dp + dv
            return {
                "kill_pvp": kp, "kill_pve": kv, "kills": kills,
                "death_pvp": dp, "death_pve": dv, "deaths": deaths,
                "kd": round(kills / deaths, 2) if deaths else (float(kills) if kills else 0),
                "pvp_kd": round(kp / dp, 2) if dp else float(kp or 0),
            }
        return {
            "has_data": sum(lc.values()) > 0,
            "ship": _mode("ship"), "fps": _mode("fps"),
            "suicide": lc["suicide"], "veh_kills": lc["veh_kills"],
            "ram_kills": lc["ram_kill_pvp"] + lc["ram_kill_pve"],
            "ram_kill_pvp": lc["ram_kill_pvp"],
            "top_ram_ships": C["ram"].most_common(8),
            "top_weapons_ship": _merge_named(C["wpn_ship"], legacy_wpn_name).most_common(10),
            "top_weapons_fps": _merge_named(C["wpn_fps"], legacy_wpn_name).most_common(10),
            "top_killed": C["killed"].most_common(10),
            "top_killedby": C["killedby"].most_common(10),
        }

    legacy = {
        "pu": _venue_combat(agg["combat"]["pu"]),
        "ac": _venue_combat(agg["combat"]["ac"]),
    }
    shops_named = Counter()
    for s, c in agg["shops"].items():
        shops_named[_pretty_shop(s, agg["shop_cat"].get(s))] += c
    # ---- purchased items: resolve class -> name, keep spend + qty ----
    item_spend_named = Counter()
    item_qty_named = Counter()
    for cls, amt in agg["item_spend"].items():
        nm = purchase_display(cls)
        item_spend_named[nm] += amt
        item_qty_named[nm] += agg["item_qty"][cls]
    # ---- blueprints (4.7+): one row per distinct name, newest first ----
    # Two logged spellings of one blueprint (a language pack changing format between
    # versions) merge into one row once they resolve to the same catalogue entry.
    bp_merged = {}
    for nm, n in agg["bp_recv"].items():
        display, cat, kind, key, cls, alias = blueprint_info(nm)
        k = key or display.lower()
        row = bp_merged.get(k)
        first = agg["bp_first"].get(nm)
        if row is None:
            bp_merged[k] = [display, cat, kind, first, n, agg["bp_sessions"].get(nm, 0), key, alias]
        else:
            row[3] = min(x for x in (row[3], first) if x) if (row[3] or first) else None
            row[4] += n
            row[5] += agg["bp_sessions"].get(nm, 0)
            if alias and alias not in row[7]:
                row[7] = (row[7] + " / " + alias) if row[7] else alias
    bp_rows = list(bp_merged.values())
    bp_rows.sort(key=lambda r: (r[3] or "", r[0].lower()), reverse=True)
    bp_cats = Counter(r[1] for r in bp_rows).most_common()
    bp_firsts = [r[3] for r in bp_rows if r[3]]
    top_items = [[n, round(item_spend_named[n]), item_qty_named[n]]
                 for n, _ in item_spend_named.most_common(12)]
    # purchase categories: keep the top 8 named buckets, roll the rest into "Other"
    _bc = agg["buy_cat"].most_common()
    _bc_top = [[n, c] for n, c in _bc if n != "Other"][:8]
    _bc_keep = {n for n, _ in _bc_top}
    _bc_other = sum(c for n, c in _bc if n not in _bc_keep)
    buy_cats = _bc_top + ([["Other", _bc_other]] if _bc_other else [])
    # ---- machine profile & stability ----
    rig_now, rig_changes, renderer, drivers = _rig_summary(agg["rig_events"])
    oc = agg["outcomes"]
    # Only sessions we actually know the ending of are rated.
    #  'open'    = the log the game is still writing; it has no ending yet.
    #  'unknown' = archived by a CSR older than this parser AND its source log is
    #              gone from disk, so it can never be re-read. Counting either as
    #              good or bad would quietly bias the stability figure, so both stay
    #              out of the denominator and are reported separately instead.
    rated = (oc.get("clean", 0) + oc.get("backend", 0) + oc.get("crash", 0)
             + oc.get("died", 0) + oc.get("other", 0))
    rough = oc.get("crash", 0) + oc.get("backend", 0) + oc.get("died", 0)
    # ---- crashes ----
    crashes = sorted((c for c in agg["crashes"] if c[0]), key=lambda c: c[0])
    last_crash = None
    if crashes:
        d, c = crashes[-1]
        last_crash = dict(c)
        last_crash["date"] = d
        dg = c.get("digest")
        last_crash["seen"] = agg["digests"].get(dg, 1) if dg else 1
    causes = Counter(c.get("exception") or "UNKNOWN" for _, c in agg["crashes"])
    # Every crash, newest first, for the paged history. Only the fields the list shows
    # — the full post-mortem would multiply the page size for data nothing reads.
    crash_list = [{k: v for k, v in
                   {"at": c.get("at"), "date": d,
                    "exception": c.get("exception") or "UNKNOWN",
                    "digest": c.get("digest"),
                    "ws_mb": c.get("ws_mb"), "free_mb": c.get("free_mb"),
                    "gpu_msg": c.get("gpu_msg")}.items() if v is not None}
                  for d, c in reversed(crashes)]
    fpsrows = [f for f in agg["fps"] if f[0]]
    fps_sum = None
    if fpsrows:
        vals = [f[1] for f in fpsrows]
        ms = [f[2] for f in fpsrows]
        hh = [f[3] for f in fpsrows]
        srt = sorted(vals)
        fps_sum = {"n": len(vals),
                   "avg": round(sum(vals) / len(vals), 1),
                   "median": round(srt[len(srt) // 2], 1),
                   "min": round(min(vals), 1), "max": round(max(vals), 1),
                   "ms": round(sum(ms) / len(ms), 1),
                   "hitch": round(sum(hh) / len(hh), 2)}
    # ---- dropped to the main menu ----
    # Rate per 10 hours played, not a raw count: a heavy month would otherwise always
    # look worse than a light one. Months are the player's own, from the local stamps.
    boots = sorted(agg["boots"])
    boot_sum = None
    if boots:
        by_month = Counter(b[:7] for b in boots)
        hrs_month = {k: v / 3600.0 for k, v in agg["months"].items()}
        # months with too little play are dropped: one drop in 40 minutes reads as a
        # catastrophic rate and says nothing. Quiet months stay in at 0, on purpose —
        # that is how you see when this started.
        trend = [[k, round(by_month.get(k, 0) / h * 10, 2), by_month.get(k, 0), round(h, 1)]
                 for k, h in sorted(hrs_month.items()) if h >= 5]
        hours = agg["seconds"] / 3600.0
        boot_sum = {
            "n": len(boots),
            "sessions": agg["boot_sessions"],
            "session_pct": round(agg["boot_sessions"] / agg["sessions"] * 100, 1)
            if agg["sessions"] else 0,
            "per10h": round(len(boots) / hours * 10, 2) if hours >= 1 else None,
            "last": boots[-1],
            "recent": boots[-8:][::-1],
            "trend": trend[-14:],
        }
    return {
        "boots": boot_sum,
        "gun_meta": gun_meta,
        "legacy": legacy,
        "shops": shops_named.most_common(10),
        "buy_cats": buy_cats,
        "sessions": agg["sessions"],
        "hours": round(agg["seconds"] / 3600, 1),
        "limited_sessions": agg["limited_sessions"],
        "limited_hours": round(agg["limited_seconds"] / 3600, 1),
        "avg_min": round(agg["seconds"] / agg["sessions"] / 60) if agg["sessions"] else 0,
        "longest_h": round(agg["longest"] / 3600, 1),
        "active_days": len(agg["days"]),
        "streak": longest_streak(agg["days"]),
        "deaths": deaths,
        "death_causes": {"collision": coll, "other": max(0, deaths - coll)},
        "death_ships": dship.most_common(8),
        "qt": agg["qt"],
        # Star Citizen only began writing a player-attributable quantum-jump event
        # ("Player Selected Quantum Target - Local") in 4.4. Older builds log a
        # <Create Travel Envelope> error instead, but that fires for EVERY ship in the
        # area — including other players' — so it can't be counted as yours. A scope
        # with no jumps at all therefore means "not recorded", shown as N/A not 0.
        "qt_known": agg["qt"] > 0,
        "qt_targets": agg["qt_targets"],
        # ---- HUD-notification metrics (logged from 4.5) ----
        "hud_known": agg["hud_n"] > 0,
        "hangars": agg["hangars"],
        "lz_visits": agg["lz_visits"],
        "juris": [[k, agg["juris_sessions"][k], agg["juris"][k]]
                  for k, _ in agg["juris_sessions"].most_common()],
        "contracts_named": [[n, c, agg["contracts_done"].get(n, 0), agg["contracts_failed"].get(n, 0)]
                            for n, c in agg["contracts_acc"].most_common(14)],
        "contracts_named_total": sum(agg["contracts_acc"].values()),
        "fleet_max": agg["fleet_max"],
        "purchases": agg["purchases"],
        "spend": round(agg["spend"]),
        "commodity_spend": round(agg["commodity_spend"]),
        "commodity_buys": agg["commodity_buys"],
        "item_spend": round(agg["spend"] - agg["commodity_spend"]),
        "top_items": top_items,
        "claims": agg["claims"],
        "blueprints": bp_rows,
        "bp_unique": len(bp_rows),
        "bp_receipts": sum(agg["bp_recv"].values()),
        "bp_repeats": sum(1 for r in bp_rows if r[4] > 1),
        "bp_cats": bp_cats,
        "bp_months": sorted(agg["bp_months"].items()),
        "bp_since": min(bp_firsts) if bp_firsts else None,
        "ships_unique": len(agg["ship_set"]),
        "ships_top": top_ships,
        "ships_flown_total": sum(agg["ships"].values()),
        "takeoffs_total": sum(agg["ship_events"].values()),
        "ship_size": ship_size.most_common(),
        "ship_role": ship_role.most_common(8),
        "weapons_top": top_w,
        "guns_distinct": len(gun_named),
        "tools_distinct": len(tool_named),
        "gun_reloads": dict(gun_reloads),
        "gun_carried": dict(gun_carried),
        "reloads_total": sum(gun_reloads.values()),
        "carried_total": sum(gun_carried.values()),
        "drawn_total": sum(gun_named.values()),
        "loot_boxes": dict(agg["loot_boxes"]),
        "containers_looted": sum(agg["loot_boxes"].values()),
        "transfers": agg["transfers"],
        "corpse_loots": agg["corpse_loots"],
        "missions": {"complete": complete, "fail": fail, "abandon": abandon,
                     "total": total_m,
                     "rate": round(100 * complete / total_m) if total_m else 0},
        "mission_types": mtypes.most_common(10),
        "systems": agg["systems"].most_common(10),
        "months": dict(agg["months"]),
        "by_hour": agg["by_hour"],
        "by_dow": agg["by_dow"],
        "weekhour": agg["weekhour"],
        "session_dist": [[buckets[i][0], dist[i]] for i in range(len(buckets))],
        "first": agg["first"].date().isoformat() if agg["first"] else None,
        "last": agg["last"].date().isoformat() if agg["last"] else None,
        "build": agg.get("build"),
        # ---- System & Stability ----
        "rig": rig_now,
        "rig_changes": rig_changes,
        "renderer": renderer,
        "drivers": drivers,
        "perf_index": _perf_summary(agg["perf"]),
        "outcomes": {"clean": oc.get("clean", 0), "backend": oc.get("backend", 0),
                     "crash": oc.get("crash", 0), "died": oc.get("died", 0),
                     "other": oc.get("other", 0),
                     "open": oc.get("open", 0), "unknown": oc.get("unknown", 0)},
        "last_crash": last_crash,
        "crash_causes": causes.most_common(),
        "crash_list": crash_list,
        "crash_repeats": [[d, n] for d, n in agg["digests"].most_common(5) if n > 1],
        "fps": fps_sum,
        "net_events": dict(agg["net_events"]),
        "outcomes_rated": rated,
        "rough_pct": round(100 * rough / rated, 1) if rated else 0,
    }


# mission-type keyword map (matched against contract-name roots)
_MTYPE_RULES = (
    ("killship", "Combat"), ("eliminate", "Combat"), ("bounty", "Bounty"),
    ("combat", "Combat"), ("gauntlet", "Combat"), ("defen", "Combat"),
    ("enforcement", "Bounty"), ("mercenary", "Combat"),
    ("shipmining", "Mining"), ("fpsmine", "Mining"), ("mining", "Mining"),
    ("salvage", "Salvage"), ("delivery", "Delivery"), ("cargo", "Hauling"),
    ("haul", "Hauling"), ("transport", "Delivery"), ("courier", "Delivery"),
    ("investigat", "Investigation"), ("discovery", "Exploration"),
    ("scan", "Exploration"), ("rescue", "Medical / Rescue"), ("medical", "Medical / Rescue"),
    ("repair", "Repair"), ("refuel", "Refuel"), ("pilotschool", "Training"),
    ("tutorial", "Training"), ("intro", "Training"), ("racing", "Racing"),
    ("shubin", "Mining"), ("reclaimer", "Salvage"), ("fps", "FPS / Ground"),
)


def mission_type(root):
    r = root.lower()
    for key, label in _MTYPE_RULES:
        if key in r:
            return label
    return "Other"


def longest_streak(days):
    if not days:
        return 0
    ds = sorted(datetime.strptime(d, "%Y-%m-%d").date() for d in days)
    best = run = 1
    for i in range(1, len(ds)):
        if (ds[i] - ds[i - 1]).days == 1:
            run += 1
            best = max(best, run)
        elif ds[i] != ds[i - 1]:
            run = 1
    return best


UNKNOWN_PATCH = "unknown"


def _ver_tuple(v):
    """'1.0.172.14271' -> (1,0,172,14271) so builds compare numerically."""
    out = []
    for part in str(v or "").split("."):
        out.append(int(part) if part.isdigit() else 0)
    return tuple(out)


def patch_key(k):
    """Numbered patches first, in version order; named feature branches after them
    (alphabetical); the unlabelled catch-all last."""
    try:
        a, b = k.split(".")[:2]
        return (0, int(a), int(b), "")
    except (ValueError, AttributeError):
        return (2, 0, 0, "") if k == UNKNOWN_PATCH else (1, 0, 0, str(k))


def build_account(sessions, handle, fresh_profile=False):
    """Aggregate one account's sessions into {career, patches, showcase, profile...}."""
    per_patch = defaultdict(blank_patch)
    career = blank_patch()
    for s in sessions:
        fold(per_patch[s["patch"] or UNKNOWN_PATCH], s)
        fold(career, s)
    # Keep the catch-all bucket: a session whose build carries no version anywhere still
    # counts toward the career, so dropping it here would make the career exceed the sum
    # of its patches with no visible explanation.
    patches = {k: finalize(v) for k, v in per_patch.items()}
    # Order by WHEN YOU PLAYED each build, not by version string. Numbered patches
    # release in order so the two agree there, but a Tech-Preview feature branch has
    # no version to sort by — and sorting those after the numbers put 4.10 (last week)
    # before previews from February. Version order is only the tie-breaker.
    order = sorted(patches.keys(),
                   key=lambda k: (patches[k].get("first") or "9999", patch_key(k)))
    career_f = finalize(career)
    prof, orgs = fetch_profile_cached(handle, force=fresh_profile)
    months = sorted({m for k in patches for m in patches[k]["months"]})
    return {
        "career": career_f, "patches": patches, "order": order, "months": months,
        "patch_playtime": [[k, patches[k]["hours"]] for k in order],
        "showcase": build_showcase(career_f, patches, order),
        "profile": prof,
        "orgs": orgs,
    }


def build_channel(sessions, fresh_profile=False):
    """Group one channel's already-scanned sessions by account → dataset."""
    by_acct = defaultdict(list)
    for s in sessions:
        by_acct[s["handle"]].append(s)
    accounts = sorted([h for h in by_acct if h], key=lambda h: -len(by_acct[h]))
    primary = accounts[0] if accounts else "Citizen"
    if accounts and None in by_acct:
        # menu-only sessions (no login) fold into the primary account
        by_acct[primary].extend(by_acct.pop(None))
        accounts.sort(key=lambda h: -len(by_acct[h]))
    elif None in by_acct:
        # never logged in on this channel — keep the sessions under "Citizen"
        by_acct["Citizen"] = by_acct.pop(None)
        accounts, primary = ["Citizen"], "Citizen"

    csub("citizens on record: " + ", ".join(f"{h} ({len(by_acct[h])})" for h in accounts))
    acct = {h: build_account(by_acct[h], h, fresh_profile) for h in accounts}
    return {
        "primary": primary,
        "accounts": [{"handle": h, "sessions": len(by_acct[h])} for h in accounts],
        "acct": acct,
        "log_count": len(sessions),
    }


def _file_key(name, mtime, size):
    """Identity of a log FILE as parsed. Rotated backups never change once written,
    so an identical (name, mtime, size) means re-reading it would produce exactly the
    same session — that's what makes a quick refresh safe."""
    return (name, int(mtime or 0), int(size or 0))


def _reuse_map(sessions):
    """Archived sessions keyed by source file, for quick-refresh reuse. Only sessions
    parsed by the CURRENT parser qualify; older ones are re-read so parser fixes land."""
    out = {}
    for s in sessions:
        if s.get("_pv") == PARSER_VERSION and s.get("_src"):
            out[_file_key(s["_src"], s.get("_mt"), s.get("_sz"))] = s
    return out


def scan_channels(channels, reuse=None):
    """Scan the log files, dedup by fingerprint, and stamp each session with its
    FINAL channel (content `Executable:` path, folder as fallback, HOTFIX folded
    into LIVE). Returns (sessions, dropped).

    `reuse` (quick refresh) maps an unchanged log file to its already-parsed session,
    so only new or modified logs — including the live Game.log, which grows every
    session — are actually read."""
    reuse = reuse or {}
    seen = set()
    reused = 0
    out = []
    total_files = sum(len(f) + (1 if l else 0) for _, f, l in channels)
    dropped = 0
    i = 0
    cstep("decrypting & parsing flight telemetry")
    for folder_label, files, live in channels:
        # current graphics settings for this channel (local installs only)
        cdir = _channel_dir(files, live)
        lbl = CHANNEL_ALIAS.get(folder_label, folder_label)
        s_ = read_settings(cdir)
        if s_:
            _SETTINGS_CACHE[lbl] = s_
        if cdir:
            _CHANNEL_DIRS[lbl] = cdir
            rp = settings_restore_points(cdir)
            if rp:
                s_ = s_ or {}
                # dated restore points, so the UI can offer "undo the last change"
                # separately from "put everything back the way it was"
                for k, v in rp.items():
                    s_["_csr_" + k] = v["when"]
                _SETTINGS_CACHE[lbl] = s_
        for path in list(files) + ([live] if live else []):
            i += 1
            s = None
            if reuse:
                try:
                    st = os.stat(path)
                    s = reuse.get(_file_key(os.path.basename(path),
                                            int(st.st_mtime), st.st_size))
                except OSError:
                    s = None
                if s is not None:
                    reused += 1
            if s is None:
                s = scan_log(path)
            fp = s.get("fp")
            if fp in seen:                 # duplicate within this scan (folder copies)
                dropped += 1
                cprogress(i, total_files)
                continue
            seen.add(fp)
            chan = s.get("channel") or folder_label   # content wins; folder is the fallback
            s["channel"] = CHANNEL_ALIAS.get(chan, chan)   # store the FINAL channel
            out.append(s)
            cprogress(i, total_files)
            if i % 15 == 0:            # feed the browser terminal's bar (parse = 8..85%)
                set_progress(0.08 + 0.77 * i / total_files, "parsing flight telemetry", i, total_files)
    cprogress(total_files, total_files)    # ensure the bar closes at 100%
    set_progress(0.85, "parsing flight telemetry", total_files, total_files)
    if reused:
        csub(f"quick refresh: reused {reused:,} unchanged log(s), "
             f"read {total_files - reused:,}")
    return out, dropped


def build_from_sessions(sessions, fresh_profile=False):
    """Bucket already-scanned sessions by their stored channel → per-channel
    datasets. Sessions come from the archive (accumulated) + this run's scan."""
    buckets = defaultdict(list)
    for s in sessions:
        buckets[s.get("channel") or "LIVE"].append(s)
    if not buckets:
        return None
    pr = {label: idx for idx, (_, label) in enumerate(SC_CHANNELS)}
    order = sorted(buckets, key=lambda c: (pr.get(c, 99), c))   # LIVE first
    ch, total = {}, 0
    for c in order:
        n = len(buckets[c])
        cstep(f"{_paint(c, _WHT)} channel — {n:,} flight records on file")
        ch[c] = build_channel(buckets[c], fresh_profile)
        ch[c]["settings"] = _SETTINGS_CACHE.get(c)
        total += n
    return {
        "channels": order,
        "default_channel": order[0],
        "ch": ch,
        "generated": datetime.now().strftime("%d-%b-%Y %H:%M"),
        "log_count": total,
        "bp_catalog": _bp_catalog_payload(),
    }


# --------------------------------------------------------------------------- #
#  Persistent session archive  —  your career survives log deletion, and merges
#  across PCs. Every parsed session is kept in app_dir/csr_archive.json, deduped
#  by fingerprint; the dashboard is built from the archive ∪ the current scan.
# --------------------------------------------------------------------------- #

ARCHIVE_VERSION = 1


def archive_path():
    return os.path.join(app_dir(), "csr_archive.json")


def _json_default(o):
    # Counter/defaultdict are dict subclasses (JSON-native); only these two aren't
    if isinstance(o, set):
        return sorted(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not JSON-serializable: {type(o)}")


def _fp_key(fp):
    return tuple(fp) if isinstance(fp, list) else fp


def _deser_session(d):
    """Rehydrate a stored session: start → datetime, fp → tuple. Everything else
    (Counters/sets stored as dict/list) is consumed fine by fold() as-is."""
    d = dict(d)
    st = d.get("start")
    try:
        d["start"] = datetime.fromisoformat(st) if isinstance(st, str) else st
    except Exception:
        d["start"] = None
    d["fp"] = _fp_key(d.get("fp"))
    # Parser 9 moved session times from the log's UTC stamps to the player's wall
    # clock. Sessions written by an older parser whose log has since been deleted can
    # never be re-read, so they are shifted here instead — otherwise the same chart
    # would mix two clocks 7-ish hours apart. `_tzf` makes it happen exactly once.
    if d.get("_pv", 0) < 9 and not d.get("_tzf") and d.get("start") and d.get("_mt"):
        try:
            mt = d["_mt"]
            off = round((datetime.fromtimestamp(mt)
                         - datetime.fromtimestamp(mt, timezone.utc).replace(tzinfo=None)
                         ).total_seconds())
            d["start"] += timedelta(seconds=off)
        except Exception:
            pass
        d["_tzf"] = 1
    return d


def load_archive():
    try:
        raw = json.load(open(archive_path(), encoding="utf-8"))
        return [_deser_session(s) for s in raw.get("sessions", [])]
    except Exception:
        return []


def save_archive(sessions):
    try:
        payload = {"v": ARCHIVE_VERSION, "app": "CSR", "version": CSR_VERSION,
                   "saved": datetime.now().isoformat(), "sessions": sessions}
        json.dump(payload, open(archive_path(), "w", encoding="utf-8"),
                  default=_json_default)
        return True
    except Exception as e:
        cerr(f"could not save archive: {e}")
        return False


def _nlines(s):
    fp = s.get("fp") or (None, None, 0)
    return (fp[2] if isinstance(fp, (list, tuple)) and len(fp) > 2 else 0) or 0


def _session_id(s):
    """Stable identity for a play session. A session's start timestamp + account
    + channel is unique (you can't start two sessions in the same second on the
    same channel). This lets a still-growing live Game.log — whose byte-count
    fingerprint changes every scan — collapse to ONE session instead of piling up
    partial copies in the archive. Falls back to the raw fingerprint when there's
    no timestamp (empty/menu-only logs)."""
    fp = s.get("fp") or (None, None, None)
    first = fp[0] if isinstance(fp, (list, tuple)) else None
    if first:
        return ("s", s.get("channel"), s.get("handle"), first)
    return ("fp", _fp_key(s.get("fp")))


def merge_sessions(*groups):
    """Union sessions across groups, deduped by session identity. When the same
    session appears more than once (a folder copy, or the live log re-scanned as
    it grew), the most complete version — largest line count — wins."""
    best, order = {}, []
    for g in groups:
        for s in g:
            k = _session_id(s)
            if k not in best:
                best[k] = s
                order.append(k)
            elif _nlines(s) > _nlines(best[k]):
                best[k] = s
    return [best[k] for k in order]


def import_archive_file(path_or_sessions):
    """Merge another archive (a path/JSON string of the export, or a session
    list) into the local archive. Returns (added, total)."""
    incoming = path_or_sessions
    if isinstance(path_or_sessions, str):
        try:
            raw = json.loads(path_or_sessions) if path_or_sessions.lstrip()[:1] in "{[" \
                else json.load(open(path_or_sessions, encoding="utf-8"))
            incoming = [_deser_session(s) for s in raw.get("sessions", [])]
        except Exception as e:
            cerr(f"import failed: {e}")
            return 0, len(load_archive())
    existing = load_archive()
    before = len(existing)
    merged = merge_sessions(existing, incoming)
    save_archive(merged)
    return len(merged) - before, len(merged)


def import_log_folder(path):
    """Merge a folder of raw Game.log files copied from another PC.

    The natural way to move a career between machines is to copy the log folder,
    not to install CSR on the old PC just to press Export — and a log folder runs
    to hundreds of MB, far too much to push through a browser file upload. So the
    folder is picked natively and read here, straight off disk.

    Accepts a StarCitizen folder, a single channel folder, or any folder with
    logbackups underneath. Channel attribution still comes from each log's own
    `Executable:` path, so copied logs land in the right career regardless of the
    folder they were dropped in. Returns (added, total, scanned)."""
    # strict: never fall back to this PC's own install if the pick was wrong.
    # Try the normal channel layout first (it labels folders nicely), then accept ANY
    # folder containing .log files at any depth — copied folders rarely keep the
    # original structure, and each log names its own channel anyway.
    channels = resolve_channels(path, strict=True)
    if not channels:
        set_progress(0.03, "locating log files")
        logs = find_logs_recursive(path)
        if not logs:
            raise ValueError("no .log files found in that folder (or anything under it)")
        channels = [("LIVE", logs, None)]      # label is a placeholder; see scan_channels
    scanned, _dropped = scan_channels(channels)
    if not scanned:
        raise ValueError("found .log files, but none were readable Star Citizen sessions")
    set_progress(0.88, "merging into archive", len(scanned), len(scanned))
    existing = load_archive()
    before = len(existing)
    # existing first: a session already on file keeps its version unless the
    # incoming copy is more complete (merge_sessions prefers the longer log)
    merged = merge_sessions(existing, scanned)
    save_archive(merged)
    return len(merged) - before, len(merged), len(scanned)


# --------------------------------------------------------------------------- #
#  Online data — everything CSR fetches from somewhere other than your logs
# --------------------------------------------------------------------------- #
# Ship & weapon art, manufacturer logos, your RSI dossier, org list and org logos.
# All of it is essentially STATIC: artwork never changes, an enlist date never
# changes, org membership shifts maybe once a year. So the policy is "fetch once,
# keep it" rather than a short poll — CSR used to re-scrape the profile on every
# single rebuild (~35 requests) which is what made a refresh take ~27 seconds.
#
#   * missing        -> fetched on demand
#   * present        -> reused, no request at all
#   * very old       -> refreshed on its own after PROFILE_TTL_D days
#   * "Update now"   -> user forces a re-fetch of everything (joined an org, new
#                       ships released, or a lookup failed while offline)
#
# The manual button matters more than the timer: it is the honest answer to "my
# org changed", and it means the automatic interval can stay long and cheap.
PROFILE_TTL_D = 30
ONLINE_META = "sc_online.json"


def _online_meta_path():
    return os.path.join(app_dir(), ONLINE_META)


def online_meta():
    try:
        return json.load(open(_online_meta_path(), encoding="utf-8"))
    except Exception:
        return {}


def stamp_online(key):
    m = online_meta()
    m[key] = datetime.now().isoformat()
    try:
        json.dump(m, open(_online_meta_path(), "w", encoding="utf-8"))
    except OSError:
        pass


def refresh_online_data():
    """Force a re-fetch of everything CSR pulls from the network.

    Clears the profile/org cache and drops art entries that previously FAILED (a
    cached null means "tried once, got nothing" — usually the source was down, and
    without this they would never be retried). Successfully-fetched art is kept:
    it never changes, and re-downloading megabytes of it would be pointless."""
    global _PROFILE_CACHE
    _PROFILE_CACHE = {}
    try:
        os.remove(_profile_cache_path())
    except OSError:
        pass
    dropped = 0
    for cache in (_SHIP_IMG, _GUN_IMG, _MAN_IMG):
        for k in [k for k, v in cache.items() if not v]:
            del cache[k]
            dropped += 1
    save_img_cache()
    stamp_online("forced")
    return dropped


def _profile_cache_path():
    return os.path.join(app_dir(), "sc_profile.json")


def load_profile_cache():
    try:
        return json.load(open(_profile_cache_path(), encoding="utf-8"))
    except Exception:
        return {}


def save_profile_cache(cache):
    try:
        json.dump(cache, open(_profile_cache_path(), "w", encoding="utf-8"))
    except OSError:
        pass


_PROFILE_CACHE = None


def fetch_profile_cached(handle, force=False):
    """(profile, orgs) for a handle, re-scraped at most once a day.

    A network failure falls back to whatever is cached regardless of age — a
    dropped connection should never blank out your dossier."""
    global _PROFILE_CACHE
    if _PROFILE_CACHE is None:
        _PROFILE_CACHE = load_profile_cache()
    key = (handle or "").lower()
    ent = _PROFILE_CACHE.get(key)
    if ent and not force:
        try:
            age = (datetime.now() - datetime.fromisoformat(ent["at"])).total_seconds()
            if age < PROFILE_TTL_D * 86400:
                return ent.get("profile"), ent.get("orgs")
        except Exception:
            pass
    try:
        prof = sc_names.fetch_profile(handle)
        orgs = sc_names.fetch_orgs(handle)
    except Exception:
        if ent:                       # offline: stale data beats no data
            return ent.get("profile"), ent.get("orgs")
        return None, []
    _PROFILE_CACHE[key] = {"at": datetime.now().isoformat(), "profile": prof, "orgs": orgs}
    save_profile_cache(_PROFILE_CACHE)
    stamp_online("profile")
    return prof, orgs


# image caches so each asset is only downloaded once across all patch views.
# Persisted to disk (app_dir/sc_images.json) so Refresh reuses already-fetched
# art instead of re-downloading ~5 MB every run. Keyed by ship/gun display name;
# a cached null means "tried, no image" (won't retry — delete the file to retry).
_SHIP_IMG, _GUN_IMG, _MAN_IMG = {}, {}, {}


def _img_cache_path():
    return os.path.join(app_dir(), "sc_images.json")


def load_img_cache():
    try:
        d = json.load(open(_img_cache_path(), encoding="utf-8"))
        _SHIP_IMG.update(d.get("ship", {}))
        _GUN_IMG.update(d.get("gun", {}))
        _MAN_IMG.update(d.get("man", {}))
        csub(f"art cache mounted — {len(_SHIP_IMG)} hulls · {len(_GUN_IMG)} arms · {len(_MAN_IMG)} makers")
    except Exception:
        pass


def save_img_cache():
    try:
        json.dump({"ship": _SHIP_IMG, "gun": _GUN_IMG, "man": _MAN_IMG},
                  open(_img_cache_path(), "w", encoding="utf-8"))
    except Exception as e:
        cerr(f"could not save image cache: {e}")


# Some makers' logos live under a wiki page that isn't their common name.
_LOGO_NAME_OVERRIDE = {}
# Pin a maker to a SPECIFIC wiki logo file when its default is broken or a wordmark.
_LOGO_FILE_OVERRIDE = {
    "Volt": "Verified Offworld Laser Technologies svg.svg",   # default JP0704 svg renders black
    "Anvil Aerospace": "AnvilAerospace.svg",                  # emblem, not the wordmark lockup
}


def _man_logo(name):
    """Manufacturer logo (data-URI) from the SC wiki, cached. A cached null means
    'tried, none' so we don't re-hit the network."""
    if not name:
        return None
    if name not in _MAN_IMG:
        if name in _LOGO_FILE_OVERRIDE:
            url = sc_names.wiki_file_logo(_LOGO_FILE_OVERRIDE[name])
        else:
            url = sc_names.wiki_manufacturer_logo(_LOGO_NAME_OVERRIDE.get(name, name))
        _MAN_IMG[name] = sc_names.fetch_data_uri(url, max_bytes=200_000)
    return _MAN_IMG[name]


def _ship_img(rec):
    if not rec:
        return None
    key = rec.get("name")
    if key not in _SHIP_IMG:
        # prefer the large store render (crisp on hi-dpi cards), fall back to small
        _SHIP_IMG[key] = (sc_names.fetch_data_uri(rec.get("img_large"))
                          or sc_names.fetch_data_uri(rec.get("img_small")))
    return _SHIP_IMG[key]


def _gun_img(name):
    if name in _GUN_IMG:
        return _GUN_IMG[name]
    titles = [name]
    short = re.sub(r"\s+(Rifle|SMG|LMG|Pistol|Shotgun|Sniper( Rifle)?|"
                   r"Assault Rifle|Energy Assault Rifle|Laser Sniper Rifle)$", "", name).strip()
    if short and short != name:
        titles.append(short)
    img = None
    for t in titles:
        img = sc_names.fetch_data_uri(sc_names.wiki_weapon_image(t))
        if img:
            break
    _GUN_IMG[name] = img
    return img


def _ship_entry(name, count, first=None, last=None, takeoffs=0):
    rec = SHIPS.record_by_name(name)
    man = rec["man"] if rec else ""
    return {"name": name, "man": man,
            "code": (rec.get("code") if rec else "") or "",
            "size": (rec.get("size") if rec else "") or "",
            "role": (rec.get("focus") or rec.get("type") if rec else "") or "",
            "url": (rec.get("url") if rec else "") or "",
            "count": count, "first": first, "last": last, "takeoffs": takeoffs,
            "logo": _man_logo(man), "img": _ship_img(rec)}


def _gun_entry(name, count, meta, reloads=0, carried=0, reloads_known=True):
    gtype, ammo, man = ((meta.get(name) or ["", "", ""]) + ["", "", ""])[:3]
    full = sc_names.WEAPON_MAN.get((man or "").lower(), "")   # BEHR -> Behring
    return {"name": name, "count": count, "type": gtype, "ammo": ammo, "code": man,
            "man": full, "logo": _man_logo(full), "img": _gun_img(name),
            "reloads": reloads, "carried": carried,
            # False when the whole scope logged no reloads at all (a build that never
            # wrote ammo-repool events) — the card then shows N/A instead of a bare 0,
            # so "never reloaded" is never confused with "not recorded".
            "reloads_known": reloads_known}


def _view_showcase(v):
    ships = [_ship_entry(n, c, (t[0] if t else None), (t[1] if len(t) > 1 else None),
                         (t[2] if len(t) > 2 else 0))
             for n, c, *t in v["ships_top"][:3]]
    meta = v.get("gun_meta", {})
    rl, ca = v.get("gun_reloads", {}), v.get("gun_carried", {})
    known = bool(v.get("reloads_total"))
    guns = [_gun_entry(n, c, meta, rl.get(n, 0), ca.get(n, 0), known)
            for n, c, tool in v["weapons_top"] if not tool][:3]
    return {"ships": ships, "guns": guns}


def build_showcase(career, patches, order):
    """Top-3 ships + guns with artwork & classification, per patch AND career."""
    cstep("retrieving hull & armament imagery from the fleet registry")
    out = {"all": _view_showcase(career)}
    for k in order:
        out[k] = _view_showcase(patches[k])
    return out


# --------------------------------------------------------------------------- #
#  HTML rendering
# --------------------------------------------------------------------------- #

def render_html(data):
    payload = json.dumps(data, separators=(",", ":"))
    fonts = sc_names.fetch_google_fonts_embed(cache_file("sc_fonts_embed.css"))
    return (HTML_TEMPLATE
            .replace("/*__FONTS__*/", fonts)
            .replace("<!--__TERMINAL__-->", TERMINAL_HTML)
            .replace("__CSR_VERSION__", CSR_VERSION)
            .replace("__CSR_CONTACT__", CSR_CONTACT)
            .replace("/*__DATA__*/", payload))


# --------------------------------------------------------------------------- #
#  Animated "flight-recorder" loading terminal — a full-screen SC-MFD overlay
#  shown in the BROWSER while a scan runs (onboarding + Refresh + Import).
#  Injected into both the dashboard and the onboarding page via <!--__TERMINAL__-->.
#  Exposes window.CSRTerm.start() / .finish(cb).
# --------------------------------------------------------------------------- #
TERMINAL_HTML = r"""
<style>
/* ===== CSR loading terminal — dense SC-MFD HUD (cyan/amber) ===== */
#csrTerm{position:fixed;inset:0;z-index:9000;display:none;align-items:center;justify-content:center;
  padding:16px;background:radial-gradient(130% 120% at 50% -10%,#0a1119,#02040a 72%);
  font-family:'Share Tech Mono',ui-monospace,'Cascadia Mono',Consolas,monospace}
#csrTerm.on{display:flex;animation:ctIn .35s ease}
@keyframes ctIn{from{opacity:0}to{opacity:1}}
#csrTerm *{box-sizing:border-box}
#csrTerm .ct-mfd{position:relative;width:min(1120px,97vw);max-height:95vh;border:1px solid rgba(91,209,230,.32);
  border-radius:14px;overflow:hidden;color:#a9bccd;background:linear-gradient(180deg,#070d15,#04070c);
  box-shadow:0 26px 90px rgba(0,0,0,.75),inset 0 0 100px rgba(91,209,230,.04)}
#csrTerm .ct-mfd::before{content:"";position:absolute;inset:0;pointer-events:none;opacity:.4;
  background-image:linear-gradient(rgba(91,209,230,.12) 1px,transparent 1px),linear-gradient(90deg,rgba(91,209,230,.12) 1px,transparent 1px);
  background-size:32px 32px;mask:radial-gradient(130% 100% at 50% 20%,#000 45%,transparent 82%)}
#csrTerm .ct-mfd::after{content:"";position:absolute;inset:0;pointer-events:none;z-index:8;
  background:repeating-linear-gradient(180deg,rgba(0,0,0,0) 0 2px,rgba(0,0,0,.13) 2px 3px);animation:ctFlick 4.5s infinite steps(60)}
@keyframes ctFlick{0%,97%{opacity:.5}98%{opacity:.24}100%{opacity:.5}}
#csrTerm .ct-sweep{position:absolute;left:0;right:0;height:130px;z-index:2;pointer-events:none;
  background:linear-gradient(180deg,transparent,rgba(91,209,230,.08),transparent);animation:ctSweep 6s linear infinite}
@keyframes ctSweep{0%{transform:translateY(-150px)}100%{transform:translateY(820px)}}
#csrTerm .ct-tick{position:absolute;width:14px;height:14px;border-color:#5bd1e6;z-index:6;opacity:.85}
#csrTerm .ct-tl{top:8px;left:8px;border-left:2px solid;border-top:2px solid}
#csrTerm .ct-tr{top:8px;right:8px;border-right:2px solid;border-top:2px solid}
#csrTerm .ct-bl{bottom:8px;left:8px;border-left:2px solid;border-bottom:2px solid}
#csrTerm .ct-br{bottom:8px;right:8px;border-right:2px solid;border-bottom:2px solid}
#csrTerm .ct-head{position:relative;z-index:5;display:flex;align-items:center;gap:13px;padding:12px 18px 10px;border-bottom:1px solid rgba(91,209,230,.18)}
#csrTerm .ct-chev{width:26px;height:31px;flex:none;filter:drop-shadow(0 0 8px rgba(91,209,230,.55))}
#csrTerm .ct-title b{color:#eaf2f8;font-size:14.5px;letter-spacing:.16em;font-family:'Chakra Petch',system-ui,sans-serif;font-weight:700}
#csrTerm .ct-title small{display:block;color:#5bd1e6;font-size:9px;letter-spacing:.3em;margin-top:2px}
#csrTerm .ct-hd-r{margin-left:auto;text-align:right;color:#6c8093;font-size:9.5px;letter-spacing:.16em;line-height:1.55}
#csrTerm .ct-hd-r b{color:#f0b43c}#csrTerm .ct-hd-r .cx{color:#5bd1e6}
/* Escape hatch — hidden during a healthy scan, revealed when the run FAULTs so a
   dead/closed CSR app can never leave the overlay covering the dashboard. */
#csrTerm .ct-exit{display:none;margin-left:14px;font-family:inherit;font-size:10px;
  letter-spacing:.18em;color:#eb6e6e;background:rgba(235,110,110,.08);cursor:pointer;
  border:1px solid rgba(235,110,110,.45);border-radius:7px;padding:7px 12px;transition:.13s}
#csrTerm .ct-exit:hover{background:rgba(235,110,110,.18);color:#ffd9d9}
#csrTerm.failed .ct-exit{display:block;animation:ctIn .3s ease}
#csrTerm.failed .ct-mfd{border-color:rgba(235,110,110,.4)}
#csrTerm .ct-main{position:relative;z-index:5;display:grid;grid-template-columns:1fr 258px}
/* The feed lives in its own bordered sub-window (StarLogs-style) so the CSR header,
   telemetry and progress stay put while only the log text moves inside the frame. */
#csrTerm .ct-console{padding:13px 14px;border-right:1px solid rgba(91,209,230,.14);height:262px}
/* No overflow clip here: the title and footer deliberately straddle the border, and
   clipping the pane sliced both of them in half. The inner scroller does the clipping
   instead — it needs min-height:0, or a flex child refuses to shrink below its content
   and the feed spills out of the frame. */
#csrTerm .ct-pane{position:relative;height:100%;border:1px solid rgba(91,209,230,.30);
  border-radius:8px;background:rgba(2,6,11,.55);padding:13px 14px 12px;
  display:flex;flex-direction:column;justify-content:flex-end}
/* title + footer sit ON the border, like a labelled frame */
#csrTerm .ct-pane-t,#csrTerm .ct-pane-f{position:absolute;background:#060c14;
  padding:1px 8px;line-height:1.25;font-size:9px;letter-spacing:.22em;
  text-transform:uppercase;white-space:nowrap;z-index:2}
#csrTerm .ct-pane-t{top:-7px;left:13px;color:#5bd1e6}
#csrTerm .ct-pane-t b{color:#eaf2f8;font-weight:600}
#csrTerm .ct-pane-f{bottom:-7px;right:13px;color:#6c8093;letter-spacing:.16em}
#csrTerm .ct-pane-f b{color:#8ba0b2;font-weight:600;font-variant-numeric:tabular-nums}
#csrTerm .ct-scroll{flex:1 1 auto;min-height:0;overflow:hidden;
  display:flex;flex-direction:column;justify-content:flex-end}
#csrTerm #ct-feed{font-size:11px;line-height:1.5;color:#8ba0b2}
#csrTerm #ct-feed .ct-ln{opacity:0;animation:ctLn .1s forwards;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@keyframes ctLn{from{opacity:0;transform:translateY(2px)}to{opacity:1;transform:none}}
#csrTerm #ct-feed .ct-hl{color:#eaf2f8;font-size:11.5px}
#csrTerm .cx{color:#5bd1e6}#csrTerm .cd{color:#5a6e81}#csrTerm .ca{color:#f0b43c}#csrTerm .cw{color:#dfe9f2}#csrTerm .cg{color:#78de96}#csrTerm .cr{color:#eb6e6e}
#csrTerm .ct-side{padding:11px 13px;display:flex;flex-direction:column;gap:11px}
#csrTerm .ct-sh{font-size:8px;letter-spacing:.22em;color:#5a6e81;margin-bottom:6px}
#csrTerm .ct-sh b{color:#5bd1e6}
#csrTerm .ct-chart{display:flex;align-items:flex-end;gap:3px;height:46px}
#csrTerm .ct-chart i{flex:1;background:linear-gradient(180deg,#5bd1e6,rgba(91,209,230,.2));border-radius:1px;transition:height .22s ease;min-height:2px;height:20%}
#csrTerm .ct-stats{display:grid;grid-template-columns:1fr 1fr;gap:8px}
#csrTerm .ct-stat{border:1px solid rgba(91,209,230,.16);border-radius:7px;padding:6px 9px;background:rgba(91,209,230,.03)}
#csrTerm .ct-stat .k{font-size:7.5px;letter-spacing:.14em;color:#5a6e81}
#csrTerm .ct-stat .v{font-size:15px;color:#eaf2f8;font-variant-numeric:tabular-nums;margin-top:2px}
#csrTerm .ct-stat .v small{font-size:9px;color:#6c8093}
#csrTerm .ct-wave{height:30px;display:flex;align-items:center;gap:2px}
#csrTerm .ct-wave i{width:2px;background:#f0b43c;opacity:.85;border-radius:1px;transition:height .18s}
#csrTerm .ct-tasks{position:relative;z-index:5;display:grid;grid-template-columns:repeat(3,1fr);gap:9px 16px;padding:12px 16px;border-top:1px solid rgba(91,209,230,.16)}
#csrTerm .ct-task .tl{display:flex;justify-content:space-between;align-items:baseline;font-size:8.5px;letter-spacing:.14em;color:#6c8093;margin-bottom:4px}
#csrTerm .ct-task .tl b{color:#8ba0b2;transition:color .2s}
#csrTerm .ct-task.act .tl b,#csrTerm .ct-task.done .tl b{color:#eaf2f8}
#csrTerm .ct-task .tl .tp{color:#5a6e81}
#csrTerm .ct-task.act .tl .tp{color:#f0b43c}#csrTerm .ct-task.done .tl .tp{color:#78de96}
#csrTerm .ct-trail{height:7px;border:1px solid rgba(91,209,230,.22);border-radius:2px;background:rgba(0,0,0,.4);overflow:hidden}
#csrTerm .ct-tfill{height:100%;width:0;background:linear-gradient(90deg,rgba(91,209,230,.4),#5bd1e6);transition:width .3s;box-shadow:0 0 7px rgba(91,209,230,.35)}
#csrTerm .ct-task.act .ct-tfill{background:linear-gradient(90deg,rgba(240,180,60,.45),#f0b43c);box-shadow:0 0 7px rgba(240,180,60,.4)}
#csrTerm .ct-task.done .ct-tfill{background:linear-gradient(90deg,rgba(120,222,150,.45),#78de96);box-shadow:0 0 7px rgba(120,222,150,.4)}
#csrTerm .ct-master{position:relative;z-index:5;display:flex;align-items:center;gap:13px;padding:10px 16px 4px}
#csrTerm .ct-mlabel{font-size:9px;letter-spacing:.2em;color:#6c8093;min-width:58px}
#csrTerm .ct-mrail{flex:1;height:16px;border:1px solid rgba(91,209,230,.3);border-radius:3px;background:rgba(0,0,0,.45);position:relative;overflow:hidden}
#csrTerm #ct-mfill{position:absolute;inset:0 100% 0 0;background:linear-gradient(90deg,rgba(91,209,230,.55),#5bd1e6);box-shadow:0 0 16px rgba(91,209,230,.6);transition:right .3s ease}
#csrTerm #ct-mfill::after{content:"";position:absolute;inset:0;background:repeating-linear-gradient(90deg,transparent 0 11px,rgba(0,0,0,.28) 11px 13px)}
#csrTerm #ct-mpct{color:#f0b43c;font-size:15px;min-width:48px;text-align:right;font-variant-numeric:tabular-nums}
#csrTerm .ct-metrics{position:relative;z-index:5;display:flex;border-top:1px solid rgba(91,209,230,.16);background:rgba(3,6,11,.5)}
#csrTerm .ct-mc{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px;padding:9px 14px;border-right:1px solid rgba(91,209,230,.09)}
#csrTerm .ct-mc:last-child{border-right:0}
#csrTerm .ct-mc .k{font-size:8px;letter-spacing:.16em;color:#5a6e81}
#csrTerm .ct-mc .v{font-size:12.5px;color:#dfe9f2;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#csrTerm .ct-mc .v.cx{color:#5bd1e6}#csrTerm .ct-mc .v.ca{color:#f0b43c}
@media(max-width:760px){#csrTerm .ct-main{grid-template-columns:1fr}#csrTerm .ct-side{display:none}#csrTerm .ct-tasks{grid-template-columns:repeat(2,1fr)}#csrTerm .ct-metrics{flex-wrap:wrap}}
</style>
<div id="csrTerm"><div class="ct-mfd">
  <span class="ct-tick ct-tl"></span><span class="ct-tick ct-tr"></span><span class="ct-tick ct-bl"></span><span class="ct-tick ct-br"></span>
  <div class="ct-sweep"></div>
  <div class="ct-head">
    <svg class="ct-chev" viewBox="0 0 90 110"><polygon points="45,4 86,58 68,58 45,30 22,58 4,58" fill="#5bd1e6"/>
      <polygon points="45,44 70,78 52,78 45,68 38,78 20,78" fill="#e7edf2" opacity=".85"/>
      <line x1="45" y1="90" x2="45" y2="104" stroke="#5bd1e6" stroke-width="4" stroke-linecap="round"/></svg>
    <div class="ct-title"><b>CITIZEN&nbsp;SERVICE&nbsp;RECORD</b><small>FLIGHT-RECORDER TERMINAL</small></div>
    <div class="ct-hd-r">NODE&nbsp;<b>CSR-01</b>&nbsp;&middot;&nbsp;STANTON<br><span class="cx" id="ct-clock">--:--:--</span>&nbsp;&middot;&nbsp;v__CSR_VERSION__</div>
    <button class="ct-exit" id="ct-exit" type="button" title="Return to your dashboard (ESC)">&#10006;&nbsp;CLOSE</button>
  </div>
  <div class="ct-main">
    <div class="ct-console">
      <div class="ct-pane">
        <span class="ct-pane-t">&#9670;&nbsp; Telemetry Feed &#183; <b>Live</b></span>
        <div class="ct-scroll"><div id="ct-feed"></div></div>
        <span class="ct-pane-f" id="ct-feedcount">standing by</span>
      </div>
    </div>
    <div class="ct-side">
      <div><div class="ct-sh"><b>&#9670;</b> SIGNAL FLUX</div><div class="ct-chart" id="ct-chart"></div></div>
      <div><div class="ct-sh"><b>&#9670;</b> DATA STREAM</div><div class="ct-wave" id="ct-wave"></div></div>
      <div class="ct-stats">
        <div class="ct-stat"><div class="k">FILES</div><div class="v" id="ct-s-files">0<small id="ct-s-total"></small></div></div>
        <div class="ct-stat"><div class="k">RECORDS</div><div class="v" id="ct-s-sess">0</div></div>
        <div class="ct-stat"><div class="k">RATE&nbsp;/s</div><div class="v" id="ct-s-rate">0</div></div>
        <div class="ct-stat"><div class="k">ELAPSED</div><div class="v" id="ct-s-time">0.0s</div></div>
      </div>
    </div>
  </div>
  <div class="ct-tasks" id="ct-tasks"></div>
  <div class="ct-master">
    <div class="ct-mlabel">MASTER</div><div class="ct-mrail"><div id="ct-mfill"></div></div><div id="ct-mpct">0%</div>
  </div>
  <div class="ct-metrics">
    <div class="ct-mc"><div class="k">PHASE</div><div class="v cx" id="ct-m-phase">STANDBY</div></div>
    <div class="ct-mc"><div class="k">CHANNELS</div><div class="v" id="ct-m-chan">LIVE&nbsp;&middot;&nbsp;PTU&nbsp;&middot;&nbsp;TP</div></div>
    <div class="ct-mc"><div class="k">INTEGRITY</div><div class="v ca" id="ct-m-int">NOMINAL</div></div>
    <div class="ct-mc"><div class="k">LINK</div><div class="v cx">127.0.0.1&nbsp;&middot;&nbsp;SECURE</div></div>
  </div>
</div></div>
<script>
window.CSRTerm=(function(){
  var F,mfill,mpct,chart,wave,tasks={},clock,done=false,cb=null,boot=false;
  var prog=0,target=6,raf=0,pollT=0,streamT=0,fxT=0,clockT=0,t0=0,lastDone=0,lastMs=0,lastPhase='';
  var TASKS=[['uplink','UPLINK',0,.05],['registry','REGISTRY',.02,.08],['decrypt','DECRYPT',.05,.10],
             ['parse','PARSE',.10,.85],['compile','COMPILE',.85,.92],['render','RENDER',.92,1]];
  var PH={'linking to fleet registry':['LINKING',1],'parsing flight telemetry':['PARSING',3],
    'locating log files':['SCANNING',1],'merging into archive':['MERGING',3],
    'compiling service record':['COMPILING',4],'embedding hull &amp; armament imagery':['RENDERING',4],
    'embedding hull & armament imagery':['RENDERING',4],'complete':['COMPLETE',5]};
  function $(id){return document.getElementById(id);}
  function clamp(x){return x<0?0:x>1?1:x;}
  function rnd(n){return Math.floor(Math.random()*n);}
  function hex(n){var s='';for(var i=0;i<n;i++)s+='0123456789ABCDEF'[rnd(16)];return s;}
  function build(){
    F=$('ct-feed');mfill=$('ct-mfill');mpct=$('ct-mpct');chart=$('ct-chart');wave=$('ct-wave');clock=$('ct-clock');
    var box=$('ct-tasks');box.innerHTML='';tasks={};
    TASKS.forEach(function(t){var d=document.createElement('div');d.className='ct-task';
      d.innerHTML='<div class="tl"><b>'+t[1]+'</b><span class="tp">0%</span></div><div class="ct-trail"><div class="ct-tfill"></div></div>';
      box.appendChild(d);tasks[t[0]]={el:d,fill:d.querySelector('.ct-tfill'),pct:d.querySelector('.tp')};});
    if(!chart.children.length){for(var i=0;i<11;i++)chart.appendChild(document.createElement('i'));}
    if(!wave.children.length){for(var j=0;j<28;j++){var w=document.createElement('i');w.style.height='6px';wave.appendChild(w);}}
  }
  var HULL=['Drake Cutlass','Anvil Carrack','RSI Polaris','MISC Freelancer','Crusader Intrepid','Aegis Gladius',
    'Origin 400i','Argo MOLE','Banu Merchantman','RSI Zeus','Aegis Reclaimer','Drake Corsair','Anvil Carrack',
    'RSI Constellation','Origin 890 Jump','Drake Cutlass Black','MISC Prospector','Aegis Retaliator','Crusader A2 Hercules',
    'RSI Perseus','Drake Vulture','Origin 300i','Anvil C8X Pisces','Aegis Sabre','Consolidated Nomad'];
  var SYS=['Stanton','Pyro','Nyx','Terra','Magnus','Odin','Castra','Sol','Ellis','Kellog'];
  var LOC=['Port Olisar','Everus Harbor','Lorville','Area18','New Babbage','GrimHEX','Orison','Seraphim Station',
    'microTech','Baijini Point','Klescher Rehabilitation','Ruin Station','Checkmate','the tram platform','a freight elevator'];
  var ITEM=['railgun','warbond ship','armor set','tractor beam','medgun','multi-tool','mining laser','pledged Idris',
    'FPS backpack','entire inventory','favorite helmet','ship weapon','quantum drive','box of laglory','crate of Cruz'];
  function pick(a){return a[rnd(a.length)];}
  // Don't repeat a joke while it's still on screen or fresh in memory. Each template
  // remembers when it was last used; picking prefers anything unseen in the last 30s,
  // and if the pool is exhausted it falls back to whichever line is oldest — so the
  // spacing is always the best the pool allows rather than collapsing to random.
  var NO_REPEAT_MS=30000, lastUsed={};
  function pickFresh(pool, spare){
    var now=Date.now();
    function scan(p){
      // a line never shown yet counts as infinitely old, so it is always eligible —
      // otherwise nothing qualifies as "fresh" during the first 30 s of a scan and
      // the picker degenerates to walking the pool in order
      var fresh=[], oldest=null, at=Infinity;
      for(var i=0;i<p.length;i++){
        var t=(p[i] in lastUsed) ? lastUsed[p[i]] : -Infinity;
        if(now-t>=NO_REPEAT_MS) fresh.push(p[i]);
        if(t<at){ at=t; oldest=p[i]; }
      }
      return {fresh:fresh, oldest:oldest, at:at};
    }
    var a=scan(pool), chosen=null;
    if(a.fresh.length) chosen=a.fresh[rnd(a.fresh.length)];
    else if(spare){                       // this pool is used up — borrow from the other
      var b=scan(spare);
      if(b.fresh.length) chosen=b.fresh[rnd(b.fresh.length)];
    }
    // Nothing eligible: return null rather than repeat. The caller waits a moment and
    // asks again, which makes the 30 s rule absolute instead of best-effort — the only
    // visible effect is an occasional slightly longer gap between lines.
    if(chosen) lastUsed[chosen]=now;
    return chosen;
  }
  // proper thousands grouping — digits only, no hex letters ever
  function grp(n){return String(Math.floor(n)).replace(/\B(?=(\d{3})+(?!\d))/g,',');}
  function auec(){var mags=[999,9999,99999,999999,9999999,49999999];return grp(1+Math.floor(Math.random()*pick(mags)));}
  // The jokes exaggerate, but the NUMBERS have to stay believable or they stop being
  // funny and just read as broken. So each quantity is bounded to a plausible range:
  //   {usd}  real money — a big spender, not a fantasy: $2k–$60k, never millions
  //   {crit} hull left after hitting something at quantum speed: 1–4%
  //   {gm}   distance to the nearest station when stranded: 18–34 Gm
  function usd(){return '$'+grp(2000+rnd(58000));}
  function tok(s){return s.replace(/\{h8\}/g,function(){return hex(8);}).replace(/\{h4\}/g,function(){return hex(4);})
    .replace(/\{auec\}/g,function(){return auec();})
    .replace(/\{usd\}/g,function(){return usd();})
    .replace(/\{crit\}/g,function(){return rnd(4)+1;})
    .replace(/\{gm\}/g,function(){return rnd(17)+18;})
    .replace(/\{p3\}/g,function(){return rnd(23)+1;})
    .replace(/\{n2\}/g,function(){return rnd(99)+1;}).replace(/\{n\}/g,function(){return rnd(9999);})
    .replace(/\{sys\}/g,function(){return pick(SYS);}).replace(/\{loc\}/g,function(){return pick(LOC);})
    .replace(/\{item\}/g,function(){return pick(ITEM);})
    .replace(/\{hull\}/g,function(){return pick(HULL);});}
  var TECH=[
    '<span class="cx">OK</span> <span class="cd">0x{h8}</span> entity resolved &#183; verified across <span class="cw">{n2}</span> shards in <span class="cd">{n}ms</span>',
    '<span class="cd">indexing hull registry</span> <span class="cw">{hull}</span> &#183; loadout port_{h4} <span class="cg">valid</span> &#183; checksum <span class="cd">{h8}</span>',
    '<span class="cd">session</span> <span class="cd">{h8}</span> decoded &#183; <span class="cx">{n2}</span> events tallied &#183; fingerprint <span class="cd">{h8}&#8228;{h4}</span> <span class="cx">match</span>',
    '<span class="cd">telemetry stream</span> chunk {n} &#183; <span class="cx">decoded</span> &#183; buffer {n2}% &#183; region <span class="cw">{sys}</span> <span class="cg">nominal</span>',
    '<span class="cd">cross-referencing patch tags</span> &#183; {n2} manifests &#183; <span class="cx">{n} entries</span> reconciled &#183; clock drift <span class="cd">0.0{n2}s</span>',
    '<span class="cd">de-duplicating flight records</span> &#183; {n} scanned &#183; <span class="cg">{n2} collapsed</span> &#183; archive integrity <span class="cx">100%</span>',
    '<span class="cd">resolving weapon manifest</span> &#183; port hardpoint_{h4} &#183; <span class="cx">{n2}</span> mounts mapped &#183; <span class="cg">ok</span>',
    '<span class="cd">walking quantum log</span> &#183; {n} jumps parsed &#183; drift <span class="cd">{n2}km</span> &#183; frame <span class="cx">{h4}</span> locked',
    '<span class="cd">hashing kill ledger</span> &#183; <span class="cw">{n2}</span> engagements &#183; PvP/PvE split reconciled &#183; <span class="cg">sealed</span>',
    '<span class="cd">reconciling ledger</span> &#183; opening balance <span class="cw">{auec}</span> aUEC &#183; <span class="cx">books balanced</span>',
    '<span class="cd">rebuilding career index</span> &#183; {n2} channels &#183; LIVE / PTU / TP &#183; merge <span class="cg">clean</span>',
    '<span class="cd">verifying signatures</span> &#183; <span class="cx">{h8}</span> &#183; no tampering &#183; chain of custody <span class="cg">intact</span>',
    '<span class="cd">normalising timestamps</span> &#183; {n} records &#183; UTC offset <span class="cd">+0{n2}:00</span> &#183; <span class="cg">aligned</span>',
    '<span class="cd">rebuilding loadout index</span> &#183; {n2} ports &#183; {n} attachments &#183; orphans <span class="cg">0</span>',
    '<span class="cd">scanning container manifests</span> &#183; {n} crates &#183; seal <span class="cx">{h4}</span> &#183; <span class="cg">unbroken</span>',
    '<span class="cd">collating death records</span> &#183; {n2} incidents &#183; cause-of-loss resolved &#183; <span class="cx">filed</span>',
    '<span class="cd">indexing mission contracts</span> &#183; {n} filed &#183; {n2}% closed &#183; ledger <span class="cg">consistent</span>',
    '<span class="cd">verifying archive integrity</span> &#183; {n} blocks &#183; parity <span class="cx">{h4}</span> &#183; <span class="cg">no corruption</span>',
    '<span class="cd">mapping hardpoints</span> &#183; <span class="cw">{hull}</span> &#183; {n2} mounts &#183; size class <span class="cx">S{n2}</span> <span class="cg">ok</span>'
  ];
  var FUN=[
    '<span class="cd">spooling quantum drive for jump to</span> <span class="cw">{sys}</span> &#183; calibration {n2}% &#183; <span class="cx">aligned</span>',
    '<span class="cd">pinging</span> <span class="cw">{loc}</span> &#183; deprecated in patch 3.{p3}, rerouting to <span class="cw">{loc}</span>',
    '<span class="cd">estimating time to</span> <span class="cw">1.0 release</span> &#183; recomputing &#183; <span class="ca">Soon&#8482;</span>',
    '<span class="cd">buffing planet tech to</span> v{n2} &#183; polishing the rocks &#183; <span class="cd">please stand by (again)</span>',
    '<span class="cd">applying server meshing</span> &#183; stitching {n2} shards &#183; status <span class="ca">tentative</span> &#183; do not hold breath',
    '<span class="cd">reticulating splines &#183; herding NPCs at</span> <span class="cw">{loc}</span> &#183; {n2} wandered into the sea',
    '<span class="cd">counting your aUEC</span> &#183; balance <span class="cw">{auec}</span> &#183; still not enough for an <span class="cw">Idris</span>',
    '<span class="cd">bed-logging Citizen</span> &#183; dreams backed up &#183; <span class="cg">consciousness cached</span> to shard',
    '<span class="cd">rendering</span> <span class="cw">890 Jump</span> interior &#183; loading {n2}% &#183; frames located <span class="cx">eventually</span>',
    '<span class="cd">verifying pledge</span> &#183; melting nothing today &#183; wallet <span class="cg">safe</span> &#183; concept ship <span class="ca">on sale</span>',
    '<span class="cd">checking Squadron 42 status</span> &#183; still <span class="ca">When It&#8217;s Done&#8482;</span> &#183; roadmap to the roadmap updated',
    '<span class="cd">calculating server FPS</span> &#183; result: <span class="cw">{n2}</span> &#183; rounding down to 4 &#183; <span class="cd">good enough</span>',
    '<span class="cd">ordering ship at ASOP terminal</span> &#183; retrieval timer <span class="cw">{n2} min</span> &#183; go make a coffee',
    '<span class="cd">tuning quantum boil</span> &#183; fuel at {n2}% &#183; <span class="cd">may explode, may not, thrilling</span>',
    '<span class="cd">syncing wishlist</span> &#183; {n2} concept jpegs &#183; total <span class="cw">{usd}</span> of real money &#183; <span class="ca">please seek help</span>',
    '<span class="cd">boarding tram to</span> <span class="cw">{loc}</span> &#183; tram arriving in {n2}s &#183; tram is <span class="cd">imaginary</span>',
    '<span class="cd">plotting Daymar rally route</span> &#183; {n2} racers &#183; expected survivors <span class="cw">{crit}</span> &#183; <span class="ca">good luck</span>',
    '<span class="cd">reviewing patch notes</span> &#183; <span class="cw">{n2}</span> fixes &#183; {n2} new bugs &#183; net <span class="ca">character building</span>',
    '<span class="cd">deploying to</span> <span class="cw">Pyro</span> &#183; lawlessness {n2}% &#183; loot {n2}% &#183; regret <span class="cw">100%</span>',
    '<span class="cd">wishing you a happy</span> <span class="cw">IAE</span> &#183; warbond total <span class="cw">{usd}</span> &#183; your bank account <span class="cr">disagrees</span>',
    '<span class="cd">buffing NPC aim</span> &#183; now {n2}% &#183; they can see through walls &#183; <span class="cd">as intended</span>',
    '<span class="cd">loading character customizer</span> &#183; still no beards &#183; <span class="ca">on the roadmap since 2016</span>',
    '<span class="cd">queueing for</span> <span class="cw">{loc}</span> elevator &#183; {n2} in line &#183; estimated wait <span class="ca">a patch or two</span>',
    '<span class="cd">consulting the roadmap</span> &#183; item moved to <span class="ca">next quarter</span> &#183; again &#183; <span class="cd">shocking absolutely no one</span>',
    '<span class="cd">counting hours in the</span> <span class="cw">{hull}</span> &#183; mostly spent walking to the elevator &#183; <span class="cd">worth it</span>',
    '<span class="cd">simulating cargo physics</span> &#183; {n2} boxes &#183; {n2} achieved orbit unprompted &#183; <span class="ca">feature</span>',
    '<span class="cd">polling shard population</span> &#183; <span class="cw">{n2}</span> citizens in <span class="cw">{sys}</span> &#183; {n2}% currently stuck in a door',
    '<span class="cd">calculating distance to</span> <span class="cw">{loc}</span> &#183; {gm} Gm &#183; brew something, this will take a while',
    '<span class="cd">indexing your hangar</span> &#183; {n2} ships &#183; {n2} you forgot you owned &#183; <span class="cd">one is still in the wrong system</span>',
    '<span class="cd">restoring insurance claim</span> &#183; expedite fee <span class="cw">{auec}</span> aUEC &#183; or wait <span class="ca">{n2} minutes</span>',
    '<span class="cd">checking gravity at</span> <span class="cw">{loc}</span> &#183; {n2}% nominal &#183; do not jump, you will not come back down'
  ];
  var WARN=[
    'freight elevator unresponsive &#183; attempting manual override &#183; override also stuck',
    'unable to assign power pips &#183; opening engineering menu &#183; menu has despawned',
    'fire detected in engine bay &#183; locating extinguisher &#183; someone left it on the floor &#183; needs recharge',
    'setting jump point &#183; NAV refuses to route &#183; have you tried flying there manually?',
    'hydration critical &#183; Citizen dying of thirst &#183; dispensing <span class="cw">Cruz</span> &#183; vending machine ate your aUEC',
    'error <span class="cw">30000</span> &#183; connection to shard lost &#183; blaming the elevator',
    'ship impounded at <span class="cw">{loc}</span> &#183; reason: existing &#183; fine <span class="cw">{auec}</span> aUEC',
    'tractor beam flung cargo into orbit &#183; recovery odds {n2}% &#183; wishing it well',
    'quantum fuel at <span class="cw">{crit}%</span> &#183; nearest station <span class="cw">{gm} Gm</span> away &#183; this is fine',
    'seat ejected Citizen on spawn &#183; medical bed occupied &#183; respawning at <span class="cw">{loc}</span>',
    'cannot find workaround for bug #{n} &#183; escalating to engineering &#183; <span class="ca">need to wait until next patch</span>',
    'welp, just lost my <span class="cw">{item}</span> to a bug &#183; filing claim #{n} &#183; recovery in next patch <span class="cd">(maybe)</span>',
    'CIG has been notified &#183; issue reproduced &#183; marked <span class="ca">fixed in a future release&#8482;</span>',
    'attempting to exit ship &#183; character stuck in geometry &#183; only fix is <span class="cw">/killself</span>',
    'inventory desync detected &#183; your <span class="cw">{item}</span> is on another shard &#183; please <span class="cw">re-log</span> and pray',
    'elevator arrived without a floor &#183; Citizen introduced to the void &#183; <span class="cd">claim ship at</span> <span class="cw">{loc}</span>',
    'armistice zone disabled &#183; friendly fire enabled &#183; hangar is now <span class="cr">a warzone</span>',
    'long-term persistence wiped &#183; <span class="cw">{item}</span> gone &#183; account reset &#183; <span class="cd">it builds character</span>',
    'cargo clipped through the floor &#183; {n2} SCU lost to the planet &#183; <span class="cd">gravity remains undefeated</span>',
    'ship exploded on hangar spawn &#183; cause: <span class="cd">looked at it wrong</span> &#183; claim timer {n2} min',
    'quantum travel interrupted by <span class="cd">an asteroid that wasn&#8217;t there</span> &#183; hull integrity <span class="cw">{crit}%</span>',
    'medical gown equipped &#183; cannot remove &#183; all armor lost &#183; <span class="cr">this is now your look</span>',
    'sent to <span class="cw">Klescher</span> for a crime you didn&#8217;t commit &#183; sentence: {n2} min of mining',
    'tried to refuel &#183; refueling arm bugged &#183; now stranded <span class="cw">{gm} Gm</span> from anywhere &#183; <span class="cd">soft death imminent</span>',
    'party member fell through the map &#183; VOIP replaced with <span class="cd">radio static</span> &#183; reforming group',
    'pressed <span class="cw">Backspace</span> to respawn &#183; nothing happened &#183; pressed it {n2} more times &#183; still nothing',
    'delivery box refuses to be picked up &#183; mission failed &#183; reputation -{n2} &#183; <span class="cd">the box wins</span>',
    'server meshing replication lag &#183; you are now {n2}s in the past &#183; <span class="cd">enjoy the flashback</span>'
  ];
  function fakeLine(){
    var r=Math.random(), t;
    if(r<.15){
      t=pickFresh(WARN);
      return t ? {html:'<span class="cr">&#9888; '+tok(t)+'</span>',warn:true} : null;
    }
    // if the chosen category has nothing fresh left, borrow from the other
    t = r<.52 ? pickFresh(FUN,TECH) : pickFresh(TECH,FUN);
    return t ? {html:tok(t),warn:false} : null;
  }
  var FEED_MAX=15, feedSeen=0;
  function push(html,hl){var d=document.createElement('div');d.className='ct-ln'+(hl?' ct-hl':'');d.innerHTML=html;
    F.appendChild(d);while(F.children.length>FEED_MAX)F.removeChild(F.firstChild);
    feedSeen++;
    var fc=$('ct-feedcount');
    // Deliberately NOT "showing N/15": the pane only fits ~11 rows, so the buffer
    // count would overstate what you can actually see. Buffer depth + total logged
    // are both true regardless of how tall the frame ends up.
    if(fc) fc.innerHTML='buffer <b>'+F.children.length+'</b> &#183; <b>'+
      feedSeen+'</b> logged';}
  function streamTick(){
    if(done&&prog>=100){streamT=0;return;}
    var L=fakeLine();
    if(!L){                       // every line is still inside its no-repeat window
      streamT=setTimeout(streamTick,400);
      return;
    }
    push(L.html,!!L.warn);
    // Regular lines: 399-631ms — tuned by eye over several passes; slow enough to read
    // a full line without pausing. Warnings hold 2340-3640ms so the joke has time to land.
    var delay=L.warn?(2340+rnd(1300)):(399+rnd(233));
    streamT=setTimeout(streamTick,delay);
  }
  var TAG='<span class="cx">[CSR]</span> <span class="cx">&#9656;</span> ';
  function tick(){prog+=Math.max(.3,(target-prog)*0.06);if(prog>target)prog=target;if(prog>100)prog=100;
    mfill.style.right=(100-prog)+'%';mpct.textContent=Math.round(prog)+'%';
    TASKS.forEach(function(t){var f=clamp((prog/100-t[2])/(t[3]-t[2]));var o=tasks[t[0]];if(!o)return;
      o.fill.style.width=(f*100)+'%';o.pct.textContent=Math.round(f*100)+'%';
      o.el.className='ct-task'+(f>=1?' done':(f>0?' act':''));});
    raf=requestAnimationFrame(tick);}
  function fx(){for(var i=0;i<chart.children.length;i++)chart.children[i].style.height=(12+rnd(80)+ (i===rnd(11)?18:0))+'%';
    var ph=prog/100;for(var j=0;j<wave.children.length;j++){var amp=6+Math.round((2+rnd(22))*(0.4+ph));wave.children[j].style.height=amp+'px';}}
  function fmtClock(){var d=new Date();function p(n){return(n<10?'0':'')+n;}return p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());}
  function poll(){
    fetch('/api/status',{cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
      if(!s)return;var pr=s.progress||{},f=(pr.frac!=null?pr.frac:0);
      var t=Math.min(done?100:99,f*100);if(t>target)target=t;
      var now=Date.now();
      if(pr.total){$('ct-s-files').firstChild.nodeValue=pr.done;$('ct-s-total').textContent=' / '+pr.total;
        var dm=(now-lastMs)/1000;if(dm>0&&lastMs){var rt=Math.max(0,Math.round((pr.done-lastDone)/dm));$('ct-s-rate').textContent=rt;}
        lastDone=pr.done;lastMs=now;}
      if(s.sessions!=null)$('ct-s-sess').textContent=(s.sessions).toLocaleString();
      if(pr.phase&&pr.phase!==lastPhase){lastPhase=pr.phase;var m=PH[pr.phase];
        if(m){$('ct-m-phase').textContent=m[0];
          if(pr.phase!=='complete')push(TAG+pr.phase+(pr.total?(' <span class="cd">'+pr.done+' / '+pr.total+'</span>'):''),true);}}
      if(boot&&s.configured)finish(function(){location.reload();});
    }).catch(function(){});
  }
  function start(opts){build();if(!F)return;boot=!!(opts&&opts.boot);done=false;prog=0;target=6;cb=null;lastPhase='';
    lastDone=0;lastMs=0;t0=Date.now();F.innerHTML='';$('csrTerm').classList.add('on');
    push(TAG+'powering on flight-recorder terminal',true);
    setTimeout(function(){push(TAG+'uplink to fleet registry established',true);},420);
    setTimeout(function(){push(TAG+'ship &amp; munitions registries synced',true);},820);
    cancelAnimationFrame(raf);raf=requestAnimationFrame(tick);
    clearTimeout(streamT);streamT=0;setTimeout(streamTick,900);
    clearInterval(fxT);fx();fxT=setInterval(fx,220);
    clearInterval(clockT);clock.textContent=fmtClock();clockT=setInterval(function(){clock.textContent=fmtClock();
      var e=(Date.now()-t0)/1000;$('ct-s-time').textContent=e.toFixed(1)+'s';},1000);
    clearInterval(pollT);setTimeout(poll,260);pollT=setInterval(poll,450);}
  // The backend got ~20x faster, so a quick refresh finished in about a second and the
  // terminal flashed past showing two lines. For anything the user deliberately asked
  // for, hold it on screen long enough to actually be read; a boot pass gets out of the
  // way immediately, since nobody asked to look at it.
  var MIN_SHOW_MS=3000;
  function finish(done_cb){if(done)return;
    var waited=Date.now()-t0, hold=boot?0:Math.max(0,MIN_SHOW_MS-waited);
    if(hold){ setTimeout(function(){finish(done_cb);},hold); return; }
    cb=done_cb;done=true;target=100;clearInterval(pollT);
    $('ct-m-phase').textContent='COMPLETE';$('ct-m-int').textContent='SEALED';
    setTimeout(function(){clearTimeout(streamT);
      push('<span class="cg">[CSR] &#10004; service record compiled &#8212; sealed</span>',true);
      push('<span class="cd">      opening dashboard&#8230;</span>');
      setTimeout(function(){clearInterval(fxT);clearInterval(clockT);cancelAnimationFrame(raf);if(cb)cb();},820);},340);}
  // Tear the overlay down and hand the page back. Without this a failed scan (most
  // often: the CSR app was closed, so /api/refresh never answers) left the terminal
  // covering the dashboard with no way out.
  function close(){
    clearInterval(pollT);clearTimeout(streamT);clearInterval(fxT);clearInterval(clockT);
    cancelAnimationFrame(raf);
    var el=$('csrTerm'); if(el){el.classList.remove('on');el.classList.remove('failed');}
    done=true;
  }
  function fail(msg){done=true;clearInterval(pollT);clearTimeout(streamT);
    $('ct-m-phase').textContent='FAULT';$('ct-m-int').textContent='DEGRADED';
    push('<span class="cr">[CSR] &#10006; '+(msg||'scan failed')+'</span>',true);
    push('<span class="cd">      press ESC or use the button above to return to your dashboard</span>');
    var el=$('csrTerm'); if(el) el.classList.add('failed');   // reveals the exit button
    if(boot) { var b=$('ct-exit'); if(b) b.textContent='RELOAD'; }
  }
  // ESC always dismisses a FAILED run (never a healthy one — that would strand the scan)
  document.addEventListener('keydown',function(e){
    if(e.key==='Escape'){var el=$('csrTerm'); if(el&&el.classList.contains('failed')) exit();}
  });
  function exit(){ if(boot) location.reload(); else close(); }
  var xb=$('ct-exit'); if(xb) xb.onclick=exit;
  return {start:start,finish:finish,fail:fail,close:close};
})();
</script>
"""


def BOOT_HTML():
    """Minimal page that opens straight onto the loading terminal while the
    first scan runs in the background, then reloads into the dashboard."""
    return (BOOT_TEMPLATE
            .replace("/*__FONTS__*/", sc_names.fetch_google_fonts_embed(cache_file("sc_fonts_embed.css")))
            .replace("<!--__TERMINAL__-->", TERMINAL_HTML)
            .replace("__CSR_VERSION__", CSR_VERSION))


BOOT_TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>CSR · booting…</title>
<style>/*__FONTS__*/ html,body{margin:0;height:100%;background:#02040a}</style></head><body>
<!--__TERMINAL__-->
<script>window.addEventListener('load',function(){
  // A startup pass that reused every log finishes in well under a second, so by the
  // time this page loads the dashboard is often already built. Check first and go
  // straight there rather than flashing the terminal for half a second.
  fetch('/api/status',{cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
    if(s&&s.configured&&!s.busy){ location.reload(); return; }
    CSRTerm.start({boot:true});
  }).catch(function(){ CSRTerm.start({boot:true}); });
});</script>
</body></html>"""


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CSR · Citizen Service Record</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 90 110'><polygon points='45,4 86,58 68,58 45,30 22,58 4,58' fill='%235bd1e6'/><polygon points='45,44 70,78 52,78 45,68 38,78 20,78' fill='%23e7edf2' opacity='0.85'/><line x1='45' y1='90' x2='45' y2='104' stroke='%235bd1e6' stroke-width='3' stroke-linecap='round'/></svg>">
<style>
/*__FONTS__*/
  :root{
    --font-display:'Chakra Petch','Segoe UI',system-ui,sans-serif;
    --font-mono:'Share Tech Mono',ui-monospace,Consolas,monospace;
    --bg:#070b11; --bg2:#0b1119; --panel:#111a26; --panel2:#0d1622;
    --line:#1d2c3d; --txt:#dbe7f4; --muted:#8fa7bd; --dim:#647a90;
    --amber:#f4a92a; --cyan:#5bd1e6; --green:#4ade80; --red:#ff5468;
    --purple:#b98bff; --orange:#ff9147;
    /* structural (theme-swapped) */
    --track:#0a121c; --track-line:#16222f; --tile:#0c1622; --grid:#152234;
    --hover-line:#2b4056; --sel-chev:7089a1;
    --glow1:#14283a; --glow2:#1a1330;
    --sidebar-bg:rgba(9,13,19,.7); --topbar-bg:rgba(10,14,20,.82);
    --card-shadow:none; --heat-rgb:63,208,230; --hud-grid:rgba(91,209,230,.035);
  }
  :root[data-theme="light"]{
    --bg:#eef2f7; --bg2:#e4eaf1; --panel:#ffffff; --panel2:#f4f8fc;
    --line:#d3dde8; --txt:#17232f; --muted:#5a6b7d; --dim:#93a2b3;
    --amber:#b9770c; --cyan:#0e93a8; --green:#238a4b; --red:#d8384c;
    --purple:#6f45bd; --orange:#d95f22;
    --track:#e7edf4; --track-line:#d3dde8; --tile:#e9eff5; --grid:#dde5ee;
    --hover-line:#9db2c7; --sel-chev:5a6b7d;
    --glow1:#d6e6f6; --glow2:#e7defb;
    --sidebar-bg:rgba(255,255,255,.72); --topbar-bg:rgba(247,250,253,.86);
    --card-shadow:0 1px 2px rgba(23,35,47,.05); --heat-rgb:13,130,150; --hud-grid:rgba(14,147,168,.06);
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{font-family:var(--font-display);color:var(--txt);letter-spacing:.2px;
    background:
      radial-gradient(1000px 500px at 85% -5%, var(--glow1) 0%, rgba(0,0,0,0) 55%),
      radial-gradient(900px 500px at 0% 0%, var(--glow2) 0%, rgba(0,0,0,0) 50%),
      var(--bg);
    min-height:100vh}
  /* subtle HUD grid projected behind everything (fades toward the bottom) */
  body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
    background-image:linear-gradient(var(--hud-grid) 1px,transparent 1px),
      linear-gradient(90deg,var(--hud-grid) 1px,transparent 1px);
    background-size:48px 48px;
    -webkit-mask-image:radial-gradient(130% 100% at 50% -12%, #000 22%, transparent 72%);
    mask-image:radial-gradient(130% 100% at 50% -12%, #000 22%, transparent 72%)}
  .app{position:relative;z-index:1}
  /* faint CRT scanlines over the whole UI — flight-recorder screen feel, kept
     low enough not to hurt readability */
  .app::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:40;
    background:repeating-linear-gradient(180deg,rgba(0,0,0,0) 0 3px,var(--scan) 3px 4px)}
  :root{--scan:rgba(8,16,22,.12)}
  :root[data-theme="light"]{--scan:rgba(60,90,110,.03)}
  a{color:var(--cyan)}
  a.lk{color:inherit;text-decoration:none;border-bottom:1px dotted transparent;transition:.12s}
  a.lk:hover{color:var(--cyan);border-bottom-color:var(--cyan)}
  .show-name a.lk:hover{color:var(--cyan)}
  /* HUD telemetry readout type — Share Tech Mono */
  .kpi .l,.kpi .s,.tb-sub,.patch-meta,.bar-row .vv,.axis,.tt,.acc-lbl,
  .selwrap>span,.section-title .tag,.row-title .tag,.side-foot,.show-count,
  .show-sub,.chip-c,.brand-sub,.vpill .vc,.frow .fdate,.frow .fsub{font-family:var(--font-mono)}
  .wrap{max-width:1180px;margin:0 auto;padding:0 20px 80px}
  header{padding:30px 0 18px;display:flex;align-items:flex-end;gap:18px;flex-wrap:wrap}
  h1{font-size:26px;margin:0;letter-spacing:.5px;font-weight:800}
  h1 .d{color:var(--amber)}
  .sub{color:var(--muted);font-size:13px;margin-top:4px}
  .who{color:var(--cyan);font-weight:700}
  .spacer{flex:1}
  .gen{color:var(--dim);font-size:11px;text-align:right}

  .hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:8px 0 26px}
  .kpi{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
    border-radius:14px;padding:18px 18px 16px;position:relative;overflow:hidden;box-shadow:var(--card-shadow)}
  .kpi::after{content:"";position:absolute;left:0;top:0;height:3px;width:100%;
    background:linear-gradient(90deg,var(--amber),var(--cyan))}
  .kpi::before{content:"";position:absolute;top:9px;right:9px;width:9px;height:9px;
    border-top:1px solid var(--cyan);border-right:1px solid var(--cyan);opacity:.45}
  .kpi .v{font-size:30px;font-weight:800;line-height:1;letter-spacing:.5px}
  /* Overview snapshot: four headline tiles, then a strip of supporting numbers.
     Eight equal tiles used to wrap into a lopsided 6 + 2. */
  .snap .hero{grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
  .minis{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));gap:10px;margin:12px 0 26px}
  .mini{display:flex;align-items:center;gap:10px;padding:11px 14px;border-radius:10px;
    background:var(--panel2);border:1px solid var(--line);position:relative;overflow:hidden}
  .mini::before{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;background:var(--cyan);opacity:.55}
  .mini .mi{display:flex;align-items:center;color:var(--cyan);flex:none}
  .mini .mi .csr-ic{width:20px;height:20px;stroke-width:1.6}
  .mini .mv{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:.4px}
  .mini .ml{font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);
    margin-left:auto;text-align:right;line-height:1.25}
  .mini.na{opacity:.72}.mini.na::before{background:var(--line)}
  .mini.na .mv{color:var(--dim);font-size:16px}.mini.na .mi{color:var(--dim)}
  /* a stat this patch's game build never recorded — muted so it reads "absent", not "zero" */
  .kpi.na .v{color:var(--dim);font-size:24px}
  .kpi.na::after{background:linear-gradient(90deg,var(--line),transparent)}
  .kpi .l{font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:var(--muted);margin-top:8px}
  .kpi .s{font-size:11px;color:var(--dim);margin-top:3px}

  .section-title{font-size:13px;text-transform:uppercase;letter-spacing:1.2px;color:var(--muted);
    margin:30px 0 12px;display:flex;align-items:center;gap:10px}
  .section-title::before{content:"▲";color:var(--cyan);font-size:9px}
  .section-title .tag{color:var(--dim);font-weight:400;text-transform:none;letter-spacing:0;font-size:11px}

  .showcase{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
  @media(max-width:720px){.showcase{grid-template-columns:1fr}}
  .show-card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
    border-radius:14px;overflow:hidden;display:flex;flex-direction:column}
  .show-img{height:132px;background-size:cover;background-position:center;position:relative;
    overflow:hidden;background-color:var(--tile)}
  /* periodic cyan light sweep across the art (flight-recorder scanner) */
  .show-img::after{content:"";position:absolute;top:-25%;bottom:-25%;width:46%;left:-60%;z-index:1;
    pointer-events:none;transform:skewX(-16deg);
    background:linear-gradient(90deg,transparent,rgba(120,220,245,.16),transparent);
    animation:hudsweep 6s ease-in-out infinite}
  @keyframes hudsweep{0%{left:-60%}55%,100%{left:165%}}
  /* periodic scanner sweep over charts (donuts + bars) */
  .hud-sweep{position:relative}
  .hud-sweep::after{content:"";position:absolute;inset:0;pointer-events:none;z-index:3;border-radius:inherit;
    background:linear-gradient(100deg,transparent 42%,rgba(120,220,245,.12) 50%,transparent 58%);
    background-size:260% 100%;background-repeat:no-repeat;animation:chartsweep 6.5s ease-in-out infinite}
  @keyframes chartsweep{0%{background-position:190% 0}58%,100%{background-position:-90% 0}}
  @media(prefers-reduced-motion:reduce){.show-img::after,.hud-sweep::after{animation:none;display:none}}
  .show-img.placeholder{display:flex;align-items:center;justify-content:center;font-size:44px;
    background:radial-gradient(120px 80px at 50% 40%,var(--panel),var(--tile))}
  /* manufacturer logo badge (bottom-right of the art) — on a light "placard" so
     the many dark-coloured maker logos (Anvil, Drake, MISC, Origin, K&W…) stay
     visible on both dark and light dashboards */
  /* light placard for BOTH themes (dark maker logos need a light backing); the
     badge stays the same size but the logo fills more of it */
  .show-logo-badge{position:absolute;right:10px;bottom:10px;z-index:2;height:54px;min-width:58px;max-width:162px;
    display:flex;align-items:center;justify-content:center;padding:4px 9px;border-radius:11px;
    background:linear-gradient(180deg,#fbfcfe,#eef2f7);border:1px solid rgba(255,255,255,.55);
    box-shadow:0 4px 14px rgba(0,0,0,.45),inset 0 0 0 1px rgba(120,150,180,.12)}
  .show-logo-badge img{max-height:46px;max-width:150px;object-fit:contain;display:block}
  .show-rank{position:absolute;top:9px;left:9px;background:rgba(6,12,18,.82);color:var(--amber);
    font-weight:800;font-size:12px;padding:3px 9px;border-radius:20px;border:1px solid #2b4056}
  .show-tag{position:absolute;top:9px;right:9px;background:rgba(6,12,18,.82);color:var(--cyan);
    font-size:10px;padding:3px 8px;border-radius:20px;text-transform:uppercase;letter-spacing:.5px}
  .show-body{padding:12px 14px 14px;position:relative}
  .show-man{position:absolute;top:12px;right:12px;font-family:var(--font-mono);font-size:10px;font-weight:700;
    letter-spacing:.08em;color:var(--cyan);border:1px solid var(--line);border-radius:6px;padding:3px 7px;
    background:var(--tile);white-space:nowrap}
  /* real manufacturer logo (from the SC wiki) in the maker sub-line */
  .show-sub.logo{margin-top:5px;min-height:26px;display:flex;align-items:center}
  .show-logo{height:23px;max-width:150px;object-fit:contain;object-position:left center;display:block}
  :root[data-theme="light"] .show-logo{filter:brightness(0) saturate(0) opacity(.72)}
  .show-name{font-weight:800;font-size:16px;line-height:1.15;padding-right:46px}
  .show-sub{color:var(--muted);font-size:12px;margin-top:2px}
  .show-count{color:var(--amber);font-size:13.5px;margin-top:8px;font-weight:700}
  .show-count.g{color:var(--cyan)}
  /* flight-recorder HUD chips: take-offs + first/last flown */
  .mchips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
  .mchip{display:flex;flex-direction:column;gap:1px;padding:4px 8px 4px 9px;position:relative;
    border:1px solid var(--track-line);border-left:2px solid var(--cyan);border-radius:4px;
    background:rgba(91,209,230,.05)}
  .mchip.to{border-left-color:var(--amber);background:rgba(240,180,60,.06)}
  .mchip .mk{font-family:var(--font-mono);font-size:8.5px;letter-spacing:.12em;color:var(--muted)}
  .mchip .mv{font-family:var(--font-mono);font-size:12.5px;color:var(--txt);font-variant-numeric:tabular-nums;line-height:1.15;font-weight:600}
  .mchip.to .mv{color:var(--amber)}
  /* data the game build never recorded — muted so it reads as "absent", not "zero" */
  .mchip.na{border-left-color:var(--line);background:transparent}
  .mchip.na .mv{color:var(--dim);font-weight:500}
  .bar-row .vv .vv-u{font-family:var(--font-mono);font-size:9.5px;color:var(--dim);letter-spacing:.06em}
  .bar-row .bsub .mchips{margin-top:5px}
  /* machine spec sheet — a read-out panel, one row per component */
  .specs{display:grid;grid-template-columns:repeat(auto-fit,minmax(238px,1fr));gap:1px;
    background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden}
  .spec{display:flex;flex-direction:column;gap:3px;padding:11px 14px;background:var(--panel)}
  .spec .sk{font-family:var(--font-mono);font-size:8.5px;letter-spacing:.14em;
    text-transform:uppercase;color:var(--muted)}
  .spec .sv{font-size:13.5px;color:var(--txt);font-weight:600;line-height:1.3;word-break:break-word}
  .spec .ss{font-family:var(--font-mono);font-size:10px;color:var(--dim)}
  .spec.na .sv{color:var(--dim);font-weight:500}
  /* dated list of hardware / driver changes */
  .tline{display:flex;flex-direction:column;gap:0;margin-top:2px}
  .tev{display:flex;gap:12px;align-items:baseline;padding:8px 2px;border-bottom:1px dashed var(--line)}
  .tev:last-child{border-bottom:0}
  /* filler rows that keep a paged list the same height on its short last page */
  .tev.ghost{border-bottom-color:transparent;pointer-events:none}
  .tev .td{font-family:var(--font-mono);font-size:11px;color:var(--amber);
    white-space:nowrap;min-width:88px;font-variant-numeric:tabular-nums}
  .tev .tw{font-size:12.5px;color:var(--txt)}
  .tev .tv{font-family:var(--font-mono);font-size:11px;color:var(--dim);margin-left:auto;
    white-space:nowrap;padding-left:10px}
  /* configuration health check — diagnosis rows, never tuning advice */
  .chk{display:flex;gap:11px;align-items:flex-start;padding:11px 13px;border-radius:9px;
    border:1px solid var(--line);border-left:3px solid var(--dim);background:var(--panel);margin-top:9px}
  .chk .ci{flex:0 0 auto;width:19px;height:19px;color:var(--dim);margin-top:1px}
  .chk .ci svg{width:19px;height:19px}
  .chk .ct2{font-size:13.5px;color:var(--txt);font-weight:600;margin-bottom:2px}
  .chk .cd{font-size:12.5px;color:var(--muted);line-height:1.6}
  .chk.bad{border-left-color:#ff5468} .chk.bad .ci{color:#ff5468}
  .chk.warn{border-left-color:var(--amber)} .chk.warn .ci{color:var(--amber)}
  .chk.ok{border-left-color:#4ade80} .chk.ok .ci{color:#4ade80}
  /* current graphics settings, shown as raw values (the tier names aren't knowable) */
  .setg{display:grid;grid-template-columns:repeat(auto-fit,minmax(176px,1fr));gap:7px;margin-top:4px}
  .setg .st{display:flex;justify-content:space-between;gap:10px;align-items:baseline;
    padding:7px 11px;border:1px solid var(--line);border-radius:7px;background:var(--panel2)}
  .setg .st b{font-family:var(--font-mono);font-size:12.5px;color:var(--cyan);font-weight:600}
  .setg .st span{font-size:11.5px;color:var(--muted)}
  /* live crash / disconnect alerts — fixed so they land wherever you're reading */
  #alerts{position:fixed;right:18px;bottom:18px;z-index:900;display:flex;flex-direction:column;
    gap:9px;max-width:min(430px,calc(100vw - 36px))}
  .alert{display:flex;gap:11px;align-items:flex-start;padding:13px 14px;border-radius:11px;
    border:1px solid var(--line);border-left:3px solid var(--amber);
    background:var(--panel);box-shadow:0 10px 30px rgba(0,0,0,.42);animation:alertIn .26s ease-out}
  @keyframes alertIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
  .alert.bad{border-left-color:#ff5468}
  .alert .ai{flex:0 0 auto;width:20px;height:20px;color:var(--amber);margin-top:1px}
  .alert.bad .ai{color:#ff5468}
  .alert .ai svg{width:20px;height:20px}
  .alert .at{font-size:13.5px;font-weight:700;color:var(--txt);margin-bottom:3px}
  .alert .ad{font-size:12px;color:var(--muted);line-height:1.55}
  .alert .ax{margin-left:auto;background:none;border:0;color:var(--dim);cursor:pointer;
    font-size:17px;line-height:1;padding:0 2px}
  .alert .ax:hover{color:var(--txt)}
  .alert .amore{margin-top:7px;font-family:var(--font-mono);font-size:10.5px;color:var(--dim)}
  /* ---- watchdog: scanner head + per-channel status lights ---- */
  /* A dot alone reads as decoration. The sweeping bar is the thing that says "this is
     running right now", and it is the first element in the card for that reason. */
  .wscan{border:1px solid var(--line);border-radius:10px;background:var(--panel2);
    padding:14px 16px;margin-bottom:13px}
  .wscan-top{display:flex;align-items:center;gap:11px;flex-wrap:wrap}
  .wscan .wdot{width:11px;height:11px;border-radius:50%;background:#4ade80;flex:none;
    animation:sPulse 1.9s ease-out infinite}
  .wscan.hot .wdot{background:var(--amber);animation-duration:.85s}
  .wscan.dead .wdot{background:#5a6e81;animation:none}
  .wscan .wst{font-family:var(--font-display);font-size:15px;letter-spacing:.02em;color:var(--txt)}
  .wscan.hot .wst{color:var(--amber)}
  .wscan .wsub{font-family:var(--font-mono);font-size:11px;letter-spacing:.07em;
    color:var(--dim);margin-left:auto}
  .wtrack{position:relative;height:5px;border-radius:3px;overflow:hidden;margin-top:12px;
    background:rgba(120,150,180,.14)}
  .wtrack::after{content:"";position:absolute;top:0;bottom:0;width:34%;border-radius:3px;
    background:linear-gradient(90deg,transparent,#4ade80,transparent);
    animation:wsweep 2.6s cubic-bezier(.5,0,.5,1) infinite}
  .wscan.hot .wtrack::after{background:linear-gradient(90deg,transparent,var(--amber),transparent);
    animation-duration:1.15s}
  .wscan.dead .wtrack::after{animation:none;opacity:.25;
    background:linear-gradient(90deg,transparent,#5a6e81,transparent)}
  @keyframes wsweep{0%{left:-34%}100%{left:100%}}
  @media(prefers-reduced-motion:reduce){
    .wtrack::after{animation:none;left:0;width:100%;opacity:.5}
    .wscan .wdot{animation:none}}
  .slights{display:grid;grid-template-columns:repeat(auto-fit,minmax(232px,1fr));gap:10px}
  .slight{display:flex;align-items:center;gap:12px;padding:13px 15px;border-radius:9px;
    border:1px solid var(--line);background:var(--panel2);font-family:var(--font-mono)}
  .slight .dot{flex:0 0 auto;width:10px;height:10px;border-radius:50%;background:var(--dim);
    box-shadow:0 0 0 0 rgba(0,0,0,0)}
  .slight.live .dot{background:#4ade80;animation:sPulse 1.9s ease-out infinite}
  .slight.idle .dot{background:var(--cyan);opacity:.75}
  .slight.off  .dot{background:#5a6e81;opacity:.5}
  @keyframes sPulse{0%{box-shadow:0 0 0 0 rgba(74,222,128,.55)}70%{box-shadow:0 0 0 7px rgba(74,222,128,0)}
    100%{box-shadow:0 0 0 0 rgba(74,222,128,0)}}
  .slight .sn{font-size:13.5px;color:var(--txt);font-weight:700;letter-spacing:.05em}
  .slight .sm{margin-left:auto;text-align:right;font-size:11px;color:var(--dim);line-height:1.5}
  .slight .sm b{color:var(--txt);font-weight:700;letter-spacing:.04em}
  .slight.live .sm b{color:#4ade80}
  .wbar{display:flex;flex-wrap:wrap;gap:9px;margin-top:12px}
  .wbar span{font-family:var(--font-mono);font-size:11px;letter-spacing:.06em;color:var(--dim);
    border:1px solid var(--line);border-radius:7px;padding:7px 11px;background:var(--panel2)}
  .wbar b{color:var(--cyan);font-weight:700}
  /* paste-the-path escape hatch: the native folder dialog can end up behind the
     browser on some setups, and there has to be a way through that doesn't depend on
     winning a fight with Windows over window focus */
  .pastefall{margin-top:9px}
  /* only raised while the picker is actually open — as a standing warning it would
     read as a defect on the setups where the dialog does come to the front */
  .pastehint{display:none;font-size:11px;line-height:1.45;color:var(--amber);
    border-left:2px solid var(--amber);padding-left:8px;margin-bottom:9px}
  .pastehint.on{display:block}
  .linky{background:none;border:0;padding:0;cursor:pointer;font-size:11.5px;
    color:var(--dim);text-decoration:underline;text-underline-offset:3px}
  .linky:hover{color:var(--cyan)}
  .pasterow{display:none;gap:7px;margin-top:9px;flex-direction:column}
  .pasterow.on{display:flex}
  .pasterow input{font-family:var(--font-mono);font-size:11.5px;color:var(--txt);
    background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:8px 10px}
  .pasterow input:focus{outline:none;border-color:var(--cyan)}
  /* ---- blueprint library: compact filter pills + search, list rows reuse .tline ---- */
  .vpill.sm{font-size:12px;padding:6px 11px;border-radius:8px}
  .vpill.sm .vc{font-size:10px;margin-left:5px;opacity:.75}
  .bpbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:6px 0 12px}
  .bpq{font-family:var(--font-mono);font-size:11.5px;color:var(--txt);background:var(--panel2);
    border:1px solid var(--line);border-radius:8px;padding:7px 10px;flex:1;min-width:190px}
  .bpq:focus{outline:none;border-color:var(--cyan)}
  .bpk{font-family:var(--font-mono);font-size:10px;letter-spacing:.04em;color:var(--dim);margin-left:9px}
  .bpx{color:var(--amber);font-weight:600}
  .bph{font-family:var(--font-mono);font-size:10px;color:var(--dim)}
  .bpsep{width:1px;height:22px;background:var(--line);margin:0 4px}
  /* collection board: armour sets, weapon size ladders, component grid */
  .bsets{display:flex;flex-direction:column}
  .bset,.blad{display:flex;gap:12px;align-items:center;padding:7px 2px;border-bottom:1px dashed var(--line);font-size:12.5px}
  .bset .bsn,.blad .bsn{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .bsl{display:flex;gap:4px;flex:none}
  .bsq,.bdot{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border:1px solid var(--dim);
    font-family:var(--font-mono);font-size:10px;font-style:normal;color:var(--dim);box-sizing:border-box}
  .bsq{border-radius:3px} .bdot{border-radius:50%;width:17px;height:17px}
  .bsq.on,.bdot.on{background:var(--cyan);border-color:var(--cyan);color:var(--bg);font-weight:700}
  .bsq.na,.bdot.na{border-style:dotted;opacity:.3}
  .bsc{font-family:var(--font-mono);font-size:11px;width:34px;text-align:right;flex:none}
  .bsgo{font-size:11px;color:var(--amber);width:150px;flex:none;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .bsgo.done{color:var(--green)} .bsgo.dim{color:var(--dim)}
  .bsets .foot-link{align-self:flex-start;margin-top:8px}
  .bgrid-wrap{overflow-x:auto}
  .bgrid{border-collapse:separate;border-spacing:3px;width:100%;font-size:12px}
  .bgrid th{font-family:var(--font-mono);font-weight:400;font-size:10px;letter-spacing:.08em;color:var(--dim);text-transform:uppercase;padding:4px 6px;text-align:left;white-space:nowrap}
  .bgrid thead th{text-align:center}
  .bgrid td{text-align:center;padding:9px 4px;border:1px solid var(--line);border-radius:4px;font-family:var(--font-mono);
    background:rgba(var(--heat-rgb),calc(var(--f,0)*.5))}
  .bgrid td b{font-weight:700;color:var(--txt)} .bgrid td span{color:var(--dim);font-size:10px}
  .bgrid td.full{border-color:var(--cyan)} .bgrid td.na{border-style:dotted;background:transparent}
  @media (max-width:900px){ .bsgo{display:none} }
  /* pager under a long list */
  .pager{display:flex;align-items:center;gap:12px;margin-top:12px;flex-wrap:wrap}
  .pager .pgi{font-family:var(--font-mono);font-size:10.5px;letter-spacing:.09em;color:var(--dim)}
  .pager .foot-link[disabled]{opacity:.35;cursor:default;pointer-events:none}
  /* ---- collapsible detail panels ---- */
  details.fold{border:1px solid var(--line);border-radius:9px;background:var(--panel2);
    margin-top:9px;overflow:hidden}
  details.fold>summary{cursor:pointer;list-style:none;padding:10px 13px;display:flex;
    align-items:center;gap:9px;font-size:12px;color:var(--muted);user-select:none}
  details.fold>summary::-webkit-details-marker{display:none}
  details.fold>summary::before{content:"▸";color:var(--cyan);font-size:10px;transition:transform .15s}
  details.fold[open]>summary::before{transform:rotate(90deg)}
  details.fold>summary:hover{color:var(--txt)}
  details.fold>summary b{color:var(--txt);font-weight:600}
  details.fold>summary .cnt{margin-left:auto;font-family:var(--font-mono);font-size:10px;color:var(--dim)}
  /* section footnotes: one line, folded detail */
  details.fold.nf{margin-top:26px;background:transparent;border:0;border-top:1px solid var(--line);border-radius:0;overflow:visible}
  details.fold.nf>summary{padding:14px 0 0;font-size:11.5px;color:var(--dim);line-height:1.6;align-items:baseline}
  details.fold.nf>summary .nfm{margin-left:auto;padding-left:12px;font-family:var(--font-mono);font-size:10px;letter-spacing:.09em;color:var(--dim);text-transform:uppercase;flex:none}
  details.fold.nf[open]>summary .nfm{opacity:.45}
  details.fold.nf .nfb{padding:10px 0 2px 17px;font-size:11.5px;color:var(--dim);line-height:1.6}
  details.fold .foldbody{padding:2px 13px 11px}
  /* ---- setting level meter ---- */
  .setg2{display:grid;grid-template-columns:repeat(auto-fit,minmax(216px,1fr));gap:8px}
  .sitem{padding:9px 11px;border:1px solid var(--line);border-radius:8px;background:var(--panel2)}
  .sitem .sk2{font-size:11.5px;color:var(--muted);display:flex;justify-content:space-between;gap:8px}
  .sitem .sk2 b{color:var(--txt);font-family:var(--font-mono);font-size:11.5px}
  .lvl{display:flex;gap:3px;margin-top:6px}
  .lvl i{flex:1;height:4px;border-radius:2px;background:var(--track-line)}
  .lvl i.on{background:linear-gradient(90deg,var(--cyan),rgba(91,209,230,.6))}
  .lvl.max i.on{background:linear-gradient(90deg,var(--amber),rgba(240,180,60,.6))}
  /* ---- beta badge + action grid ---- */
  .beta{display:inline-flex;align-items:center;gap:5px;padding:2px 7px;border-radius:4px;
    border:1px solid rgba(240,180,60,.45);background:rgba(240,180,60,.1);color:var(--amber);
    font-family:var(--font-mono);font-size:8.5px;letter-spacing:.16em;vertical-align:middle}
  .acts{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:9px}
  .act{padding:12px 13px;border:1px solid var(--line);border-radius:9px;background:var(--panel2)}
  .act .at2{font-size:12.5px;color:var(--txt);font-weight:600;margin-bottom:3px}
  .act .ad2{font-size:11.5px;color:var(--dim);line-height:1.5;margin-bottom:9px}
  .act .row{display:flex;gap:7px;flex-wrap:wrap}
  .act button{font-family:inherit}
  .act .warn{color:var(--amber)}
  /* shareable summary block */
  .sharebox{margin-top:12px}
  .sharebox textarea{width:100%;box-sizing:border-box;min-height:132px;resize:vertical;
    background:var(--panel2);color:var(--txt);border:1px solid var(--line);border-radius:9px;
    padding:12px 13px;font-family:var(--font-mono);font-size:11.5px;line-height:1.65}

  .card{position:relative;background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
    border-radius:14px;padding:18px;box-shadow:var(--card-shadow)}
  /* flight-recorder corner brackets (all four corners) */
  .card::before,.card::after{content:"";position:absolute;width:13px;height:13px;
    border-color:var(--cyan);opacity:.45;pointer-events:none;z-index:1}
  .card::before{top:6px;left:6px;border-left:1.5px solid;border-top:1.5px solid;border-top-left-radius:3px}
  .card::after{bottom:6px;right:6px;border-right:1.5px solid;border-bottom:1.5px solid;border-bottom-right-radius:3px}
  .card h3{margin:0 0 14px;font-size:13px;font-weight:700;color:var(--txt);
    text-transform:uppercase;letter-spacing:.6px;display:flex;align-items:center;gap:8px}
  /* HUD tick before every card title */
  .card h3::before{content:"";width:3px;height:12px;background:var(--cyan);border-radius:1px;
    box-shadow:0 0 6px rgba(91,209,230,.5);flex:none}
  .card h3 .u{color:var(--dim);font-weight:400;text-transform:none;letter-spacing:0;font-size:11.5px}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .grid3{display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:16px}
  @media(max-width:820px){.grid2,.grid3{grid-template-columns:1fr}}

  /* patch tabs */
  .tabs{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 4px}
  .tab{border:1px solid var(--line);background:var(--panel);color:var(--muted);
    padding:8px 15px;border-radius:10px;cursor:pointer;font-size:13px;font-weight:600;
    transition:.15s;user-select:none}
  .tab:hover{color:var(--txt);border-color:var(--hover-line)}
  .tab.active{color:#06121f;background:linear-gradient(90deg,var(--amber),#ffca5f);
    border-color:var(--amber)}
  .tab.all.active{background:linear-gradient(90deg,var(--cyan),#8fe6f2)}

  /* legacy PU/AC venue toggle */
  .vtoggle{display:flex;gap:10px;margin:2px 0 6px;flex-wrap:wrap}
  .vpill{font-family:inherit;font-size:14px;font-weight:600;cursor:pointer;
    border:1px solid var(--line);background:var(--panel);color:var(--muted);
    padding:10px 18px;border-radius:11px;transition:.14s}
  .vpill:hover:not(:disabled){color:var(--txt);border-color:var(--hover-line)}
  .vpill.on{color:#06121f;background:linear-gradient(90deg,var(--amber),#ffca5f);border-color:var(--amber)}
  .vpill:disabled{opacity:.4;cursor:not-allowed}
  .vpill .vc{font-family:var(--mono);font-size:11.5px;opacity:.8;margin-left:7px}

  /* bars */
  .bars{display:flex;flex-direction:column;gap:11px}
  .bar-row{display:block}
  .bar-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:5px}
  .bar-row .nm{color:var(--txt);font-size:13.5px;font-weight:600;line-height:1.2}
  .bar-row .rk{color:var(--dim);font-weight:700;margin-right:7px}
  .bar-track{background:var(--track);border-radius:6px;height:14px;overflow:hidden;border:1px solid var(--track-line)}
  .bar-fill{height:100%;border-radius:6px;background:linear-gradient(90deg,var(--cyan),#2b9fb3)}
  .bar-row .vv{color:var(--muted);font-variant-numeric:tabular-nums;font-size:12.5px;white-space:nowrap}
  .bar-row .bsub{font-family:var(--font-mono);color:var(--dim);font-size:10.5px;
    letter-spacing:.03em;margin:-1px 0 6px 22px}
  .bar-row .bsub b{color:var(--cyan);font-weight:400}
  .tool .bar-fill{background:linear-gradient(90deg,var(--purple),#7d5bb0)}

  /* first-flight date list */
  .flist{display:flex;flex-direction:column}
  .frow{display:flex;justify-content:space-between;align-items:baseline;gap:14px;
    padding:10px 2px;border-bottom:1px solid rgba(30,42,56,.55)}
  .frow:last-child{border-bottom:0}
  .frow .fnum{color:var(--dim);font-weight:700;font-size:12px;margin-right:9px;
    font-variant-numeric:tabular-nums}
  .frow .fship{font-size:14px;font-weight:600;flex:1;min-width:0}
  .frow .fsub{color:var(--muted);font-size:11.5px;font-weight:400;margin-left:8px}
  .frow .fdate{font-family:ui-monospace,Consolas,monospace;font-size:13px;color:var(--amber);
    font-variant-numeric:tabular-nums;white-space:nowrap;letter-spacing:.02em}

  .empty{color:var(--dim);font-size:12.5px;padding:8px 0}
  .legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--muted);margin-top:10px}
  .legend i{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:5px;vertical-align:middle}

  .mono{font-variant-numeric:tabular-nums}
  .kpi .v{font-variant-numeric:tabular-nums}

  /* showcase classification chips */
  .show-chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:7px}
  .chip-c{font-size:10.5px;padding:3px 8px;border-radius:6px;border:1px solid var(--line);
    background:var(--tile);color:var(--muted);text-transform:uppercase;letter-spacing:.4px;white-space:nowrap}
  .chip-c.sz{color:#06121f;font-weight:700;border:0}
  .chip-c.sz.Small{background:#6fbf7f}.chip-c.sz.Medium{background:#42d0e6}
  .chip-c.sz.Large{background:#f4a92a}.chip-c.sz.Capital{background:#ff5468}
  .chip-c.sz.Vehicle,.chip-c.sz.Gravlev{background:#8aa0b8}
  .chip-c.energy{border-color:#3fd0e6;color:#3fd0e6}
  .chip-c.ballistic{border-color:#f4a92a;color:#f4a92a}
  .chip-c.hybrid{border-color:#b98bff;color:#b98bff}

  /* RSI profile strip */
  .profile{display:flex;align-items:center;gap:16px;background:linear-gradient(180deg,var(--panel),var(--panel2));
    border:1px solid var(--line);border-radius:14px;padding:14px 18px;margin:4px 0 8px}
  .profile img{width:58px;height:58px;border-radius:10px;object-fit:cover;border:1px solid var(--line);background:var(--tile)}
  .profile .pmain{display:flex;flex-direction:column;gap:2px}
  .profile .ph{font-size:17px;font-weight:800}
  .profile .psub{color:var(--muted);font-size:12.5px}
  .profile .pfacts{margin-left:auto;display:flex;gap:22px;flex-wrap:wrap;text-align:right}
  .profile .pf .l{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--dim)}
  .profile .pf .v{font-size:13.5px;color:var(--txt);font-weight:600}
  @media(max-width:680px){.profile .pfacts{margin-left:0;width:100%;text-align:left}}
  .note{color:var(--dim);font-size:11.5px;margin-top:26px;line-height:1.6;border-top:1px solid var(--line);padding-top:14px}
  svg{display:block;width:100%;height:auto;overflow:visible}
  .tt{fill:var(--txt);font-size:11px}
  .axis{fill:var(--muted);font-size:11px}
  rect.hover:hover{opacity:.82;cursor:default}
  rect.hitcol{cursor:pointer}
  rect.hitcol:hover{fill:rgba(91,209,230,.07)}
  .chart-reset{font-family:var(--font-mono);font-size:11px;letter-spacing:.05em;text-transform:none;
    cursor:pointer;color:var(--cyan);background:transparent;border:1px solid var(--line);
    border-radius:7px;padding:5px 11px;transition:.13s;white-space:nowrap}
  .chart-reset:hover{border-color:var(--cyan);background:rgba(91,209,230,.09)}
  /* donut: tilted 3D ring + leader-line labels (one wide SVG) */
  .donut-wrap{display:block;padding-top:6px}
  .donut-svg{width:100%;max-width:640px;margin:0 auto;display:block;
    filter:drop-shadow(0 0 14px rgba(91,209,230,.12)) drop-shadow(0 8px 16px rgba(0,0,0,.3))}
  .dseg{transition:opacity .12s}
  .dseg:hover{opacity:.85;cursor:default}
  .patch-meta{color:var(--muted);font-size:12.5px;margin:2px 0 0}

  /* app shell: sidebar + main */
  .app{display:flex;min-height:100vh}
  .sidebar{width:222px;flex:0 0 222px;border-right:1px solid var(--line);
    background:var(--sidebar-bg);position:sticky;top:0;height:100vh;display:flex;
    flex-direction:column;padding:18px 12px;gap:4px;z-index:20}
  /* the logo doubles as a home button — resets channel/account/patch to defaults */
  .brand{display:flex;align-items:center;gap:10px;padding:4px 8px 15px;width:100%;
    background:none;border:0;font-family:inherit;text-align:left;cursor:pointer;
    border-radius:10px;transition:.13s}
  .brand:hover .brand-mark{filter:drop-shadow(0 0 10px rgba(91,209,230,.75))}
  .brand:hover .brand-name{color:var(--cyan)}
  .brand:focus-visible{outline:1px solid var(--cyan);outline-offset:2px}
  .brand-mark{width:28px;height:34px;flex:none;filter:drop-shadow(0 0 6px rgba(91,209,230,.35))}
  .brand-mark .c1{fill:var(--cyan)}
  .brand-mark .c2{fill:currentColor;opacity:.82}
  .brand-mark .c3{stroke:var(--cyan);stroke-width:3;stroke-linecap:round}
  .brand-name{font-family:var(--font-display);font-weight:700;font-size:22px;letter-spacing:4px;
    line-height:1;color:var(--txt)}
  .brand-sub{font-family:var(--font-mono);font-size:7.5px;letter-spacing:1.5px;color:var(--muted);
    margin-top:5px;white-space:nowrap}
  nav a{position:relative;display:flex;align-items:center;gap:10px;padding:10px 13px;border-radius:10px;
    color:var(--muted);font-size:14px;font-weight:600;cursor:pointer;text-decoration:none;transition:.13s}
  nav a span{font-size:15px;width:23px;height:23px;display:flex;align-items:center;justify-content:center}
  nav a span .csr-ic{width:22px;height:22px;display:block}
  .csr-ic{width:1em;height:1em}
  .show-img.placeholder .csr-ic{width:60px;height:60px;color:var(--dim);opacity:.65;stroke-width:1.4}
  /* inline HUD icons that replaced emoji across tiles, titles, callouts & buttons */
    .kpi .v .csr-ic{width:23px;height:23px;color:var(--cyan);vertical-align:-2px;margin-right:8px;stroke-width:1.6}
    .row-title .csr-ic{width:18px;height:18px;color:var(--cyan);vertical-align:-2px;margin-right:6px;stroke-width:1.6}
    .callout .ci .csr-ic{width:24px;height:24px;color:var(--cyan);stroke-width:1.6}
    .mbtn .csr-ic,.dct .csr-ic{width:18px;height:18px;vertical-align:-2px;margin-right:6px;stroke-width:1.6}
    .iconbtn .csr-ic{width:20px;height:20px;stroke-width:1.6}
    .vpill .csr-ic{width:17px;height:17px;vertical-align:-2px;margin-right:5px;stroke-width:1.6}
  /* the watcher's heartbeat, in the nav — green while CSR is tailing your logs, and
     faster while Star Citizen is actually writing one. Hidden entirely for a saved
     copy of the page, where nothing is running behind it. */
  .navlive{display:none;width:7px;height:7px;border-radius:50%;background:#4ade80;
    margin-left:auto;flex:none;box-shadow:0 0 0 0 rgba(74,222,128,.55)}
  .navlive.on{display:block;animation:navpulse 2.4s ease-in-out infinite}
  .navlive.hot{animation-duration:1s;background:var(--amber);
    box-shadow:0 0 0 0 rgba(244,169,42,.55)}
  @keyframes navpulse{
    0%{box-shadow:0 0 0 0 rgba(74,222,128,.5);opacity:1}
    70%{box-shadow:0 0 0 6px rgba(74,222,128,0);opacity:.55}
    100%{box-shadow:0 0 0 0 rgba(74,222,128,0);opacity:1}}
  @media(prefers-reduced-motion:reduce){.navlive.on{animation:none}}
  nav a:hover{background:var(--panel);color:var(--txt)}
  nav a.active{background:linear-gradient(90deg,rgba(244,169,42,.18),rgba(63,208,230,.06));
    color:var(--txt);box-shadow:inset 2px 0 0 var(--amber)}
  /* CSS tooltip for the collapsed (icon-only) sidebar */
  nav a::after{content:attr(data-label);position:absolute;left:calc(100% + 10px);top:50%;
    transform:translateY(-50%);background:var(--panel);color:var(--txt);font-family:var(--font-mono);
    font-size:11px;letter-spacing:.06em;padding:6px 10px;border:1px solid var(--line);border-radius:7px;
    white-space:nowrap;opacity:0;pointer-events:none;transition:opacity .12s;z-index:30;
    box-shadow:0 8px 22px rgba(0,0,0,.45);display:none}
  .side-foot{margin-top:auto;color:var(--dim);font-size:10.5px;padding:10px 12px;line-height:1.5;
    display:flex;flex-direction:column;gap:12px}
  .side-foot #gen{line-height:1.55}
  main{flex:1;min-width:0}
  /* The bar itself still spans the window so its rule and blur reach both edges; the
     controls inside are capped to the same 1180px column the content uses, so they
     line up with the cards below instead of drifting off to the right. */
  .topbar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:16px;
    flex-wrap:wrap;padding:14px 26px;border-bottom:1px solid var(--line);
    background:var(--topbar-bg);backdrop-filter:blur(6px);
    max-width:1232px;box-sizing:border-box}
  .tb-title{display:flex;flex-direction:column;gap:2px}
  .tb-titlerow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  #secTitle{font-size:19px;font-weight:800}
  .scope-badge{font-family:var(--font-mono);font-size:10px;letter-spacing:.09em;text-transform:uppercase;
    color:var(--cyan);border:1px solid var(--line);border-radius:20px;padding:3px 10px;white-space:nowrap}
  .scope-badge.patch{color:var(--amber);border-color:rgba(244,169,42,.42)}
  .tb-sub{color:var(--muted);font-size:12px}.tb-sub b{color:var(--cyan)}
  /* topbar controls: account + patch dropdowns, theme toggle */
  .tb-controls{margin-left:auto;display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap}
  .selwrap{display:flex;flex-direction:column;gap:4px}
  .selwrap>span{font-size:9.5px;text-transform:uppercase;letter-spacing:.14em;color:var(--dim);padding-left:2px}
  .selwrap select{font-family:inherit;font-size:13px;font-weight:600;color:var(--txt);
    background:var(--panel);border:1px solid var(--line);border-radius:9px;
    padding:8px 32px 8px 12px;cursor:pointer;appearance:none;-webkit-appearance:none;min-width:150px;
    transition:.13s;
    background-image:url('data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2210%22 height=%226%22%3E%3Cpath d=%22M1 1l4 4 4-4%22 stroke=%22%23808fa2%22 stroke-width=%221.6%22 fill=%22none%22 stroke-linecap=%22round%22 stroke-linejoin=%22round%22/%3E%3C/svg%3E');
    background-repeat:no-repeat;background-position:right 12px center}
  .selwrap select:hover{border-color:var(--hover-line)}
  .selwrap select:focus{outline:none;border-color:var(--cyan)}
  /* The patch list is "Career" and a set of two-digit versions — the 150px floor meant
     for handle-length names is dead space here. The ‹ › steppers flank it, so it reads
     as a control rather than a stray box at this size. */
  .selwrap.patch select{font-weight:700;min-width:0;width:112px}
  /* patch stepper: ‹ dropdown › for walking patches without opening the list */
  .patchnav{display:flex;align-items:flex-end;gap:6px}
  .stepbtn{align-self:flex-end;display:flex;align-items:center;justify-content:center;
    cursor:pointer;color:var(--muted);background:var(--panel);border:1px solid var(--line);
    border-radius:9px;padding:0;width:30px;height:34px;transition:.13s}
  .stepbtn:hover:not(:disabled){border-color:var(--hover-line);color:var(--cyan)}
  .stepbtn:disabled{opacity:.34;cursor:default}
  .stepbtn .csr-ic{width:18px;height:18px;stroke-width:2}
  .acctnav{display:flex;align-items:flex-end;gap:6px;position:relative}
  /* one-time coach mark: only ever shown to people who actually have several
     accounts, and only until they answer it (choice remembered in this browser) */
  .coach{display:none;position:absolute;top:calc(100% + 12px);right:0;z-index:60;width:300px;
    background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--cyan);
    border-radius:12px;padding:14px 15px;box-shadow:0 16px 44px rgba(0,0,0,.6);
    animation:coachIn .28s ease}
  .coach.on{display:block}
  .coach::before{content:"";position:absolute;top:-7px;right:16px;width:12px;height:12px;
    background:var(--panel);border-left:1px solid var(--cyan);border-top:1px solid var(--cyan);
    transform:rotate(45deg)}
  @keyframes coachIn{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
  .coach-t{font-weight:800;font-size:13.5px;color:var(--cyan);margin-bottom:6px}
  .coach-b{font-size:12px;line-height:1.6;color:var(--muted)}
  .coach-b b{color:var(--txt)}
  .coach-a{display:flex;gap:8px;margin-top:12px}
  .coach-a .mbtn{font-size:11.5px;padding:7px 11px}
  @media(max-width:760px){.coach{right:auto;left:0;width:min(300px,86vw)}}
  .stepbtn.pin.on{color:var(--amber);border-color:rgba(240,180,60,.5);background:rgba(240,180,60,.08)}
  .stepbtn.pin.on:hover{color:var(--amber)}
  /* One square for every top-bar button. They were 44px while the bell came out at 40
     and the status light at 34, which read as three sizes of the same thing. */
  .iconbtn{align-self:flex-end;display:inline-flex;align-items:center;justify-content:center;
    font-size:15px;line-height:1;cursor:pointer;color:var(--txt);width:34px;height:34px;
    background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:0;transition:.13s}
  .iconbtn:hover{border-color:var(--hover-line)}
  #refreshBtn{font-size:17px}
  /* alert bell — the standing record of everything the watcher has raised. The
     toasts are transient by design; this is where they go once you dismiss them. */
  .bellwrap{position:relative;align-self:flex-end}
  .iconbtn.bell{position:relative;overflow:visible;color:var(--muted)}
  .iconbtn.bell svg{width:18px;height:18px;display:block}
  .bellwrap.has .iconbtn.bell{color:#ff5468}
  .bellwrap.has .iconbtn.bell{border-color:rgba(235,110,110,.5)}
  .bbadge{display:none;position:absolute;top:-6px;right:-6px;min-width:17px;height:17px;
    padding:0 4px;border-radius:9px;background:#ff5468;color:#fff;font-family:var(--font-mono);
    font-size:10px;font-weight:700;line-height:17px;text-align:center;
    box-shadow:0 0 0 2px var(--panel)}
  .bbadge.on{display:block;animation:alertIn .22s ease-out}
  .bellmenu{display:none;position:absolute;top:calc(100% + 10px);right:0;z-index:70;width:330px;
    background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
    border-radius:12px;box-shadow:0 16px 44px rgba(0,0,0,.6);text-align:left;overflow:hidden}
  .bellmenu.on{display:block;animation:coachIn .2s ease}
  .bm-head{display:flex;align-items:center;justify-content:space-between;gap:9px;
    padding:11px 13px;border-bottom:1px solid var(--line);font-family:var(--font-mono);
    font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
  .bm-clear{background:none;border:0;color:var(--dim);cursor:pointer;font-family:var(--font-mono);
    font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:0}
  .bm-clear:hover{color:var(--txt)}
  .bm-list{max-height:min(60vh,420px);overflow-y:auto}
  .bm-ev{display:flex;gap:10px;padding:11px 13px;border-bottom:1px solid var(--line);
    border-left:3px solid var(--amber)}
  .bm-ev:last-child{border-bottom:0}
  .bm-ev.bad{border-left-color:#ff5468}
  .bm-ev.new{background:rgba(255,84,104,.06)}
  .bm-ev .bi{flex:0 0 auto;width:16px;height:16px;color:var(--amber);margin-top:2px}
  .bm-ev.bad .bi{color:#ff5468}
  .bm-ev .bi svg{width:16px;height:16px}
  .bm-ev .bt{font-size:12.5px;font-weight:700;color:var(--txt)}
  .bm-ev .bd{font-size:11.5px;color:var(--muted);line-height:1.55;margin-top:2px}
  .bm-ev .bw{font-family:var(--font-mono);font-size:9.5px;letter-spacing:.1em;color:var(--dim);margin-top:4px}
  .bm-ev .bw a{color:var(--accent);cursor:pointer;text-decoration:none;margin-left:9px}
  .bm-ev .bw a:hover{text-decoration:underline}
  .bm-empty{padding:20px 13px;text-align:center;font-size:11.5px;color:var(--dim)}
  /* CSR status light — green pulse while the app is serving this page, grey when
     you're looking at a saved copy with nothing running behind it */
  .statuswrap{position:relative;align-self:flex-end}
  /* Just the light. The words "CSR ON" cost 50px of top bar to say what a green
     pulsing dot already says; the full state moves to a hover tooltip, and the click
     still opens the menu that can actually do something about it. */
  .statusbtn{display:flex;align-items:center;justify-content:center;cursor:pointer;
    font-family:var(--font-mono);font-size:10px;letter-spacing:.14em;color:var(--muted);
    background:var(--panel);border:1px solid var(--line);border-radius:9px;
    padding:0;width:34px;height:34px;transition:.13s;position:relative}
  .statusbtn:hover{border-color:var(--hover-line);color:var(--txt)}
  .statusbtn .st-tx{display:none}
  .statusbtn::after{content:attr(data-tip);position:absolute;top:calc(100% + 9px);right:0;
    background:var(--panel);color:var(--txt);font-family:var(--font-mono);font-size:11px;
    letter-spacing:.05em;padding:7px 11px;border:1px solid var(--line);border-radius:7px;
    white-space:nowrap;opacity:0;pointer-events:none;transition:opacity .12s;z-index:40;
    box-shadow:0 8px 22px rgba(0,0,0,.45)}
  .statusbtn:hover::after,.statusbtn:focus-visible::after{opacity:1}
  .dot{width:9px;height:9px;border-radius:50%;flex:none;background:var(--dim);
    box-shadow:0 0 0 0 rgba(0,0,0,0)}
  .online .dot{background:#4ade80;animation:pulse 2.1s ease-in-out infinite}
  .offline .dot{background:#6b7a88}
  .busy .dot{background:var(--amber);animation:pulse .9s ease-in-out infinite}
  @keyframes pulse{
    0%,100%{box-shadow:0 0 0 0 rgba(74,222,128,.55)}
    70%{box-shadow:0 0 0 6px rgba(74,222,128,0)}}
  .busy .dot{animation-name:pulseAmber}
  @keyframes pulseAmber{
    0%,100%{box-shadow:0 0 0 0 rgba(244,169,42,.6)}
    70%{box-shadow:0 0 0 6px rgba(244,169,42,0)}}
  .statusmenu{display:none;position:absolute;top:calc(100% + 10px);right:0;z-index:70;width:264px;
    background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
    border-radius:12px;padding:14px;box-shadow:0 16px 44px rgba(0,0,0,.6);text-align:left}
  .statusmenu.on{display:block;animation:coachIn .2s ease}
  .sm-head{display:flex;align-items:center;gap:9px;font-weight:800;font-size:13.5px;margin-bottom:7px}
  .sm-body{font-size:11.5px;line-height:1.6;color:var(--muted)}
  .sm-acts{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  .sm-acts .mbtn{font-size:11.5px;padding:7px 11px}
  .sm-acts .mbtn.danger{border-color:rgba(235,110,110,.45);color:#eb6e6e;background:rgba(235,110,110,.08)}
  .sm-acts .mbtn.danger:hover{background:rgba(235,110,110,.18);color:#ffd9d9}
  @media(max-width:760px){.tb-controls{margin-left:0;width:100%}}

  /* refresh confirm modal + progress overlay */
  .modal-back{position:fixed;inset:0;background:rgba(4,8,13,.72);backdrop-filter:blur(3px);
    display:none;align-items:center;justify-content:center;z-index:60;padding:24px}
  .modal-back.on{display:flex}
  .modal{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
    border-radius:16px;padding:28px 28px 24px;max-width:430px;width:100%;text-align:center;
    box-shadow:0 24px 70px rgba(0,0,0,.55)}
  .modal .mh{font-family:var(--font-display);font-weight:700;font-size:19px;letter-spacing:.5px;margin-bottom:9px}
  .modal .mb{color:var(--muted);font-size:13.5px;line-height:1.6;margin-bottom:22px}
  .modal .mactions{display:flex;gap:10px;justify-content:center}
  .mbtn{font-family:var(--font-display);font-weight:700;font-size:14px;letter-spacing:.5px;cursor:pointer;
    border-radius:10px;padding:11px 20px;border:1px solid var(--line);transition:.13s}
  .mbtn.ghost{background:transparent;color:var(--muted)}
  .mbtn.ghost:hover{color:var(--txt);border-color:var(--hover-line)}
  .mbtn.go{color:#06121f;background:linear-gradient(90deg,var(--cyan),#8fe6f2);border:0}
  .mbtn.go:hover{filter:brightness(1.08)}
  .mspin{width:34px;height:34px;border-radius:50%;border:3px solid var(--line);border-top-color:var(--cyan);
    animation:csrspin .8s linear infinite;margin:2px auto 16px}
  @keyframes csrspin{to{transform:rotate(360deg)}}
  /* data / backup modal */
  .modal.data{max-width:540px}
  /* three transfer routes (export / import logs / import backup) need more room */
  .modal.data:has(.datarow.three){max-width:760px}
  .dmnote{font-size:11.5px;color:var(--dim);margin:2px 0 0}
  .onlinebar{display:flex;align-items:center;gap:14px;text-align:left;margin-top:14px;
    padding:12px 14px;border:1px solid var(--line);border-radius:11px;background:var(--panel2)}
  .onlinebar .ol-t{font-weight:700;font-size:13px}
  .onlinebar .ol-s{font-size:11.5px;color:var(--muted);line-height:1.55;margin-top:3px}
  .onlinebar .mbtn{flex:none;white-space:nowrap}
  #olWhen{color:var(--dim);font-family:var(--font-mono);font-size:10.5px}
  .datarow{display:flex;gap:12px;text-align:left;margin-bottom:6px;align-items:stretch}
  .dcard{flex:1 1 0;min-width:0;display:flex;flex-direction:column;border:1px solid var(--line);
    border-radius:12px;padding:16px 15px;background:var(--panel2)}
  .dct{font-family:var(--font-display);font-weight:700;font-size:13.5px;letter-spacing:.4px;margin-bottom:7px}
  .dcd{color:var(--muted);font-size:12px;line-height:1.55;margin-bottom:14px;flex:1 1 auto}
  .dcard .mbtn{width:100%;padding:10px 12px;font-size:12.5px;box-sizing:border-box;margin-top:auto}
  @media(max-width:520px){.datarow{flex-direction:column}}
  @media(max-width:820px){.datarow.three{flex-wrap:wrap}.datarow.three .dcard{flex:1 1 45%}}
  /* about / legal modal */
  .modal.about{max-width:560px;text-align:left;max-height:86vh;overflow:auto;padding:26px 28px 22px}
  .about-head{display:flex;align-items:center;gap:14px;border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:6px}
  .about-name{font-family:var(--font-display);font-weight:700;font-size:17px;letter-spacing:.5px}
  .about-ver{font-family:var(--font-mono);font-size:11px;color:var(--muted);letter-spacing:.04em;margin-top:3px}
  .about-body p{color:var(--muted);font-size:12.8px;line-height:1.65;margin:9px 0}
  .about-body b{color:var(--txt);font-weight:600}
  .ab-k{font-family:var(--font-mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--dim);margin-right:4px}
  .ab-sec{font-family:var(--font-mono);font-size:10.5px;text-transform:uppercase;letter-spacing:.14em;
    color:var(--cyan);margin:18px 0 2px}
  .ab-warranty{font-size:11.5px;color:var(--dim);border-top:1px solid var(--line);padding-top:12px;margin-top:16px}
  .foot-link{font-family:var(--font-mono);font-size:11px;letter-spacing:.05em;cursor:pointer;color:var(--muted);
    background:transparent;border:1px solid var(--line);border-radius:7px;padding:6px 10px;transition:.13s;width:100%}
  .foot-link:hover{color:var(--cyan);border-color:var(--hover-line)}
  /* .foot-link is width:100% for the sidebar footer it was written for. In a button
     row it has to size to its own label, and an <a> has to be boxed like a <button>
     or it renders as a bare underlined link sitting next to one. */
  .row{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
  .row .foot-link,.pager .foot-link{width:auto;display:inline-flex;align-items:center;
    justify-content:center;gap:7px;text-decoration:none;font-size:11.5px;padding:9px 14px;line-height:1}
  .row .foot-link .csr-ic,.pager .foot-link .csr-ic{width:15px;height:15px;flex:none;stroke-width:1.7}
  .row .foot-link.danger{border-color:rgba(235,110,110,.45);color:#eb6e6e;background:rgba(235,110,110,.08)}
  .foot-disc{color:var(--dim);font-size:9.5px;line-height:1.5;margin-top:9px;font-family:var(--font-mono);letter-spacing:.03em}
  .content{padding:22px 26px 70px;max-width:1180px}
  .limited-note{margin:14px 26px 0;max-width:1180px;padding:10px 14px;border-radius:8px;
    font-size:12.5px;line-height:1.5;color:var(--text);
    background:rgba(240,180,60,.10);border:1px solid rgba(240,180,60,.42);
    border-left:3px solid var(--amber)}
  .limited-note b{color:var(--amber);font-family:var(--font-display);letter-spacing:.02em}
  .limited-note a{color:var(--cyan)}
  @media(max-width:760px){.limited-note{margin:12px 14px 0}
    .sidebar{width:64px;flex-basis:64px;padding:18px 10px;align-items:center}
    .brand{justify-content:center;padding:4px 0 12px}.brand-tx{display:none}
    nav{width:100%;display:flex;flex-direction:column;align-items:center;gap:4px}
    /* gap:0 + font-size:0 so the invisible text label doesn't shove the icon off-centre */
    /* collapsed rail: the dot rides the icon's top-right corner instead of the row end */
    .navlive{position:absolute;top:7px;right:7px;margin-left:0}
    nav a{justify-content:center;gap:0;font-size:0;padding:0;width:44px;height:44px;border-radius:11px}
    nav a span{font-size:18px;width:auto}
    /* symmetric active pill (no left inset bar when icon-only) */
    nav a.active{box-shadow:none;background:linear-gradient(180deg,rgba(91,209,230,.20),rgba(91,209,230,.08));
      border:1px solid rgba(91,209,230,.4)}
    nav a.active span{color:var(--cyan)}
    nav a::after{display:block}                 /* enable the hover tooltip */
    nav a:hover::after{opacity:1}               /* ...and actually show it on hover */
    .side-foot{display:none}.content{padding:18px 14px 60px}}

  .row-title{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);
    margin:26px 0 12px;display:flex;align-items:center;gap:9px}
  .row-title::before{content:"▲";color:var(--cyan);font-size:8.5px}
  .row-title:first-child{margin-top:2px}
  .row-title .tag{color:var(--dim);font-weight:400;text-transform:none;letter-spacing:0;font-size:11px}

  /* grouped sub-sections (Combat = ship / FPS / looting). A numbered badge, an icon
     tile and a full-width rule make each group read as its own block. */
  .gsec{margin:30px 0 0;padding:18px 0 4px;border-top:1px solid var(--line);position:relative}
  .gsec:first-of-type{margin-top:20px}
  .gsec::before{content:"";position:absolute;top:-1px;left:0;width:86px;height:2px;
    background:linear-gradient(90deg,var(--cyan),transparent)}
  .ghead{display:flex;align-items:center;gap:13px;margin-bottom:16px}
  .gnum{font-family:var(--font-mono);font-size:10px;letter-spacing:.14em;color:var(--cyan);
    border:1px solid var(--line);border-radius:6px;padding:4px 7px;background:rgba(91,209,230,.06)}
  .gic{width:42px;height:42px;flex:none;display:flex;align-items:center;justify-content:center;
    border:1px solid var(--line);border-radius:10px;background:var(--panel);color:var(--cyan)}
  .gic .csr-ic{width:24px;height:24px;stroke-width:1.6}
  .gt{font-family:var(--font-display);font-size:16px;font-weight:800;letter-spacing:.05em;
    text-transform:uppercase;color:var(--txt);line-height:1.15}
  .gs{font-family:var(--font-mono);font-size:11px;color:var(--dim);margin-top:3px;letter-spacing:.02em}
  .gbody>.hero{margin-top:0}
  @media(max-width:760px){.gnum{display:none}.gt{font-size:14px}.gs{font-size:10px}}

  /* heatmap */
  .heat{display:grid;grid-template-columns:34px repeat(24,1fr);gap:2px}
  .heat .hc{aspect-ratio:1;border-radius:2px;background:var(--tile)}
  .heat .hl{font-size:9.5px;color:var(--dim);display:flex;align-items:center}
  .heat .hx{font-size:8.5px;color:var(--dim);text-align:center}

  /* org cards */
  .orgs{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media(max-width:700px){.orgs{grid-template-columns:1fr}}
  .org-card{display:flex;gap:14px;align-items:center;background:linear-gradient(180deg,var(--panel),var(--panel2));
    border:1px solid var(--line);border-radius:14px;padding:15px 17px}
  .org-card img{width:52px;height:52px;border-radius:10px;object-fit:cover;background:var(--tile);border:1px solid var(--line)}
  .org-card .ologo{width:52px;height:52px;border-radius:10px;background:var(--tile);display:flex;
    align-items:center;justify-content:center;font-size:22px;border:1px solid var(--line)}
  .org-card .ologo .csr-ic{width:30px;height:30px;color:var(--cyan);stroke-width:1.5}
  .org-card .oname{font-weight:800;font-size:15px}
  .org-card .osub{color:var(--muted);font-size:12px;margin-top:2px}
  .org-card .obadge{margin-left:auto;text-align:right}
  .org-card .obadge .k{font-size:10px;text-transform:uppercase;color:var(--dim);letter-spacing:.5px}
  .org-card .obadge .m{font-size:15px;font-weight:700;color:var(--cyan)}
  .kind-main{color:var(--amber);font-size:10px;text-transform:uppercase;letter-spacing:.6px;font-weight:700}
  .kind-aff{color:var(--purple);font-size:10px;text-transform:uppercase;letter-spacing:.6px;font-weight:700}
  .note{color:var(--dim);font-size:11.5px;margin-top:26px;line-height:1.6;border-top:1px solid var(--line);padding-top:14px}
  /* section explainer callout */
  .callout{display:flex;gap:13px;align-items:flex-start;
    background:linear-gradient(180deg,var(--panel),var(--panel2));
    border:1px solid var(--line);border-left:3px solid var(--cyan);border-radius:12px;
    padding:14px 17px;margin:2px 0 18px;font-size:12.8px;line-height:1.6;color:var(--muted);box-shadow:var(--card-shadow)}
  .callout .ci{font-size:19px;flex:none;line-height:1.3}
  .callout b{color:var(--txt);font-weight:600}
  .callout .ct{color:var(--cyan)}
  .clink{color:var(--cyan);cursor:pointer;font-weight:600;white-space:nowrap;
    border-bottom:1px solid transparent;transition:border-color .12s}
  .clink:hover{border-bottom-color:var(--cyan)}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <button class="brand" id="homeBtn" type="button" title="Back to Overview — resets channel, account and patch to their defaults">
      <svg class="brand-mark" viewBox="0 0 90 110" fill="none" aria-label="CSR logo">
        <polygon class="c1" points="45,4 86,58 68,58 45,30 22,58 4,58"/>
        <polygon class="c2" points="45,44 70,78 52,78 45,68 38,78 20,78"/>
        <line class="c3" x1="45" y1="90" x2="45" y2="104"/>
      </svg>
      <div class="brand-tx"><div class="brand-name">CSR</div>
        <div class="brand-sub">CITIZEN SERVICE RECORD</div></div>
    </button>
    <nav id="nav">
      <a data-sec="overview" data-label="Overview" class="active"><span>📊</span> Overview</a>
      <a data-sec="flight" data-label="Flight &amp; Travel"><span>🚀</span> Flight &amp; Travel</a>
      <a data-sec="combat" data-label="Combat"><span>💀</span> Combat</a>
      <a data-sec="legacy" data-label="Legacy Combat"><span>⚔️</span> Legacy Combat</a>
      <a data-sec="missions" data-label="Missions"><span>🎯</span> Missions</a>
      <a data-sec="economy" data-label="Economy"><span>💰</span> Economy</a>
      <a data-sec="blueprints" data-label="Blueprints"><span>📐</span> Blueprints</a>
      <a data-sec="activity" data-label="Activity"><span>📆</span> Activity</a>
      <a data-sec="system" data-label="Your Machine"><span>🖥️</span> Your Machine</a>
      <a data-sec="stability" data-label="Stability &amp; Crashes"><span>🩺</span> Stability<i class="navlive" id="navLive" title="CSR is watching your logs"></i></a>
      <a data-sec="orgs" data-label="Orgs &amp; Profile"><span>🪪</span> Orgs &amp; Profile</a>
    </nav>
    <div class="side-foot">
      <button class="foot-link" id="aboutBtn">About &amp; legal</button>
      <div id="gen"></div>
      <div class="foot-disc">Fan-made · not affiliated with CIG / RSI</div>
    </div>
  </aside>
  <main>
    <div class="topbar">
      <div class="tb-title">
        <div class="tb-titlerow"><span id="secTitle">Overview</span><span id="secScope" class="scope-badge"></span></div>
        <span class="tb-sub">Citizen <b class="who" id="who"></b></span></div>
      <div class="tb-controls">
        <label class="selwrap" id="chanWrap" style="display:none"><span>Channel</span><select id="chanSel"></select></label>
        <div class="acctnav" id="acctWrap">
          <label class="selwrap"><span>Account</span><select id="acctSel"></select></label>
          <button class="stepbtn pin" id="mainBtn" type="button" title="Set as your main account"></button>
          <div class="coach" id="mainHint">
            <div class="coach-t">Multiple accounts detected</div>
            <div class="coach-b">CSR auto-selects the account with the highest activity as your
              main — currently <b id="hintWho">this one</b>. If that isn&rsquo;t your main account,
              choose another from the list and press <b>&#9733;</b>.</div>
            <div class="coach-a"><button class="mbtn go" id="hintSet">&#9733; Keep as main</button>
              <button class="mbtn ghost" id="hintNo">Not now</button></div>
          </div>
        </div>
        <div class="patchnav">
          <button class="stepbtn" id="patchPrev" title="Previous patch" aria-label="Previous patch"></button>
          <label class="selwrap patch"><span>Patch</span><select id="patchSel"></select></label>
          <button class="stepbtn" id="patchNext" title="Next patch" aria-label="Next patch"></button>
        </div>
        <button class="iconbtn" id="dataBtn" title="Backup &amp; transfer — export/import your career" aria-label="Backup and transfer" style="display:none">💾</button>
        <button class="iconbtn" id="refreshBtn" title="Refresh data — re-scan your logs" aria-label="Refresh data" style="display:none">⟳</button>
        <button class="iconbtn" id="themeBtn" title="Toggle light / dark" aria-label="Toggle theme">🌙</button>
        <div class="bellwrap" id="bellWrap" style="display:none">
          <button class="iconbtn bell" id="bellBtn" type="button" title="Crash &amp; disconnect alerts" aria-label="Alerts">🔔<span class="bbadge" id="bellBadge"></span></button>
          <div class="bellmenu" id="bellMenu">
            <div class="bm-head"><span>Alerts</span><button class="bm-clear" id="bellClear">Clear all</button></div>
            <div class="bm-list" id="bellList"></div>
          </div>
        </div>
        <div class="statuswrap">
          <button class="statusbtn" id="statusBtn" type="button" aria-label="CSR status">
            <span class="dot"></span><span class="st-tx" id="statusTx">CSR</span>
          </button>
          <div class="statusmenu" id="statusMenu">
            <div class="sm-head"><span class="dot"></span><span id="smState">Checking…</span></div>
            <div class="sm-body" id="smBody"></div>
            <div class="sm-acts" id="smActs"></div>
          </div>
        </div>
      </div>
    </div>
    <div class="limited-note" id="limitedNote" style="display:none"></div>
    <div class="content" id="content"></div>
  </main>
</div>

<div class="modal-back" id="refreshModal">
  <div class="modal">
    <div class="mh">Refresh data</div>
    <div class="mb">Pull in your latest sessions. Old log backups never change once Star Citizen has rotated them, so a quick refresh only needs to read what's new.</div>
    <div class="datarow">
      <div class="dcard">
        <div class="dct">Quick refresh</div>
        <div class="dcd">Reads only new or changed logs — usually a few seconds. This is what you want almost every time.</div>
        <button class="mbtn go" id="rfGo">Quick refresh</button>
      </div>
      <div class="dcard">
        <div class="dct">Full re-scan</div>
        <div class="dcd">Re-reads every log from scratch. Slower, but use it after a CSR update so improved parsing is applied to your whole history.</div>
        <button class="mbtn ghost" id="rfFull">Full re-scan</button>
      </div>
    </div>
    <div class="mactions"><button class="mbtn ghost" id="rfCancel">Cancel</button></div>
  </div>
</div>
<div class="modal-back" id="offlineModal">
  <div class="modal">
    <div class="mh">CSR isn&rsquo;t running</div>
    <div class="mb">This page is still showing your last saved dashboard, but the CSR app that scans your logs has been closed — so it can&rsquo;t refresh right now.<br><br>
      Start <b>CSR</b> again, then use Refresh. Everything already on this page stays readable in the meantime.</div>
    <div class="mactions"><button class="mbtn go" id="offClose">Back to dashboard</button></div>
  </div>
</div>
<div class="modal-back" id="refreshBusy">
  <div class="modal"><div class="mspin"></div>
    <div class="mh" id="rfBusyTx">Scanning your logs…</div>
    <div class="mb">Reading your Game.logs — hang tight, the page will reload automatically.</div></div>
</div>
<div class="modal-back" id="dataModal">
  <div class="modal data">
    <div class="mh">Backup &amp; transfer</div>
    <div class="mb">CSR keeps a running <b>archive</b> of every session it has scanned (<span id="dmCount">—</span> on file), so your career survives even if Star Citizen deletes old logs.</div>
    <div class="datarow three">
      <div class="dcard">
        <div class="dct" data-ic="export">Export backup</div>
        <div class="dcd">Save your whole career as a single file — keep it safe, or move it to another PC.</div>
        <button class="mbtn go" id="dmExport">Download archive</button>
      </div>
      <div class="dcard">
        <div class="dct" data-ic="transfer">Import logs</div>
        <div class="dcd">Copied your <b>Game.log</b> folder from another PC? Point CSR at it — no need to install CSR over there first.</div>
        <button class="mbtn go" id="dmLogsBtn">Choose log folder…</button>
        <div class="pastefall">
          <div class="pastehint" id="dmHint">Windows opens that dialog <b>behind</b> this
            window — look in your taskbar. Or just paste the path below.</div>
          <button class="linky" id="dmPasteToggle">or paste the folder path</button>
          <div class="pasterow" id="dmPasteRow">
            <input type="text" id="dmPastePath" spellcheck="false"
                   placeholder="D:\path\to\the\copied\logbackups">
            <button class="mbtn go" id="dmPasteGo">Use this folder</button>
          </div>
        </div>
      </div>
      <div class="dcard">
        <div class="dct" data-ic="import">Import backup</div>
        <div class="dcd">Load <b>.json</b> backups exported from CSR. Pick one or several.</div>
        <button class="mbtn ghost" id="dmImportBtn">Choose backup file(s)…</button>
        <input type="file" id="dmFile" accept=".json,.csr,application/json" multiple style="display:none">
      </div>
    </div>
    <div class="mb dmnote">Everything merges and de-duplicates by session, so importing twice never double-counts.</div>
    <div class="onlinebar">
      <div>
        <div class="ol-t">Online data</div>
        <div class="ol-s">Ship &amp; weapon art, manufacturer logos, your RSI dossier and orgs.
          Kept indefinitely because it rarely changes — update it if you&rsquo;ve joined an org,
          changed your avatar, or artwork failed to load. <span id="olWhen"></span></div>
      </div>
      <button class="mbtn ghost" id="olBtn">Update now</button>
    </div>
    <div class="mb" id="dmMsg" style="min-height:18px;margin-top:4px"></div>
    <div class="mactions"><button class="mbtn ghost" id="dmClose">Close</button></div>
  </div>
</div>

<div class="modal-back" id="aboutModal">
  <div class="modal about">
    <div class="about-head">
      <svg class="brand-mark" style="width:34px;height:41px" viewBox="0 0 90 110" fill="none">
        <polygon class="c1" points="45,4 86,58 68,58 45,30 22,58 4,58"/>
        <polygon class="c2" points="45,44 70,78 52,78 45,68 38,78 20,78"/>
        <line class="c3" x1="45" y1="90" x2="45" y2="104"/></svg>
      <div><div class="about-name">CSR — Citizen Service Record</div>
        <div class="about-ver">Version __CSR_VERSION__ · a Star Citizen career dashboard</div></div>
    </div>
    <div class="about-body">
      <p><span class="ab-k">Made by</span> <b>archelium</b> · <a class="lk" href="https://archelium.com" target="_blank" rel="noopener">archelium.com</a><br>
         <span class="ab-k">Contact</span> <a class="lk" href="mailto:__CSR_CONTACT__">__CSR_CONTACT__</a></p>

      <div class="ab-sec">Disclaimer</div>
      <p>CSR is an <b>unofficial, fan-made tool</b>. It is <b>not affiliated with, endorsed, sponsored, or approved by
         Cloud Imperium Games (CIG) or Roberts Space Industries (RSI)</b>. “Star Citizen”, “Squadron 42”, and related
         names, logos and imagery are trademarks of Cloud Imperium Rights LLC. All ship names, artwork and game data
         belong to their respective owners and are shown here for informational, non-commercial use.</p>

      <div class="ab-sec">Privacy</div>
      <p>CSR runs <b>entirely on your computer</b>. It reads only your local Star Citizen log files and your public RSI
         profile. There is <b>no analytics, no tracking, and none of your data is sent anywhere</b>. Ship/weapon artwork
         and your public profile are fetched from official sources only while the dashboard is being built.</p>

      <div class="ab-sec">Data sources</div>
      <p>Ships &amp; art — RSI Ship Matrix. Item names — Star Citizen Wiki. Profile &amp; orgs — your public RSI pages.
         Fonts — Chakra Petch &amp; Share Tech Mono (Google Fonts, OFL).</p>

      <div class="ab-sec">Found a bug or have feedback?</div>
      <p>Reports genuinely help — the button below opens your email app with a short template (and the version
         pre-filled). Nothing is sent automatically.</p>

      <p class="ab-warranty">Provided “as is”, without warranty of any kind. Some figures are inferred from log text and
         may be approximate — see the notes on each section.</p>
    </div>
    <div class="mactions"><button class="mbtn ghost" id="bugBtn">📮 Report a bug</button>
      <button class="mbtn go" id="aboutClose">Close</button></div>
  </div>
</div>

<script>
const DATA = /*__DATA__*/;
const $ = s => document.querySelector(s);
const fmt = n => n.toLocaleString();
const auec = n => n>=1e9?(n/1e9).toFixed(2)+'B':n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(0)+'k':fmt(n);
const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const DOW = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
let current = 'all';
// channel (LIVE / PTU / TP) → account → patch. Career stats are per-channel.
let currentChannel = DATA.default_channel;
const C = () => DATA.ch[currentChannel] || {primary:'Citizen',accounts:[],acct:{}};

// ---- main account -------------------------------------------------------- //
// CSR guesses your main by session count, which is wrong for anyone whose alt has
// more hours (a fresh reinstall, an org alt, a shared PC). You can pin the real one;
// the choice is stored per channel in this browser and wins over the guess.
const MAIN_KEY='csr_main_acct';
function mainMap(){ try{ return JSON.parse(localStorage.getItem(MAIN_KEY)||'{}'); }catch(e){ return {}; } }
function savedMain(ch){
  const h=mainMap()[ch];
  return (h && C().acct && C().acct[h]) ? h : null;   // ignore a handle that's gone
}
function setMain(ch,handle){
  const m=mainMap();
  if(handle) m[ch]=handle; else delete m[ch];
  try{ localStorage.setItem(MAIN_KEY,JSON.stringify(m)); }catch(e){}
}
// One-time prompt so multi-account users learn the ★ exists instead of silently
// getting whichever handle happened to have the most sessions.
const HINT_KEY='csr_main_hint';
function hintSeen(){ try{ return localStorage.getItem(HINT_KEY)==='1'; }catch(e){ return true; } }
function markHintSeen(){ try{ localStorage.setItem(HINT_KEY,'1'); }catch(e){} }
function defaultAccount(){ return savedMain(currentChannel) || C().primary; }
let currentAccount = defaultAccount();
const A = () => C().acct[currentAccount] || {career:{},patches:{},order:[],showcase:{},patch_playtime:[]};

function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

// ---- original SC-styled HUD icon set (inline SVG, inherits currentColor) ----
const _SVG = inner => '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '+
  'stroke-linecap="round" stroke-linejoin="round" class="csr-ic" aria-hidden="true">'+inner+'</svg>';
const ICON = {
  overview:_SVG('<rect x="3.5" y="3.5" width="7" height="7" rx="1.3"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.3"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.3"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.3"/>'),
  flight:_SVG('<path d="M4 14.5l8-7.5 8 7.5"/><path d="M7 20l5-4.5 5 4.5"/>'),
  combat:_SVG('<circle cx="12" cy="12" r="7.5"/><path d="M12 1.8v3.4M12 18.8v3.4M1.8 12h3.4M18.8 12h3.4"/><circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none"/>'),
  legacy:_SVG('<path d="M6 6l12 12M18 6L6 18"/><path d="M5 9.2l3-3M19 9.2l-3-3"/>'),
  missions:_SVG('<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3a1 1 0 011-1h4a1 1 0 011 1v1"/><path d="M8.6 12.4l2.4 2.4 4.4-5.2"/>'),
  travel:_SVG('<path d="M12 2.6l8 9.4-8 9.4-8-9.4z"/><circle cx="12" cy="12" r="2.4"/>'),
  economy:_SVG('<circle cx="12" cy="12" r="8"/><path d="M12 7v10M14.2 9.3c-.7-.7-1.7-1-2.7-1-1.5 0-2.7.9-2.7 2s1.2 2 2.7 2 2.7.9 2.7 2-1.2 2-2.7 2c-1 0-2-.3-2.7-1"/>'),
  activity:_SVG('<path d="M2.5 12h4l2.6-6.5 4 13 2.4-6.5H21"/>'),
  orgs:_SVG('<path d="M12 2.6l7.5 3v6.4c0 4.2-3.2 7.4-7.5 9.4-4.3-2-7.5-5.2-7.5-9.4V5.6z"/><path d="M9 12.4l2.4 2.4 4-4.6"/>'),
  system:_SVG('<rect x="6.6" y="6.6" width="10.8" height="10.8" rx="1.8"/><rect x="10.1" y="10.1" width="3.8" height="3.8" rx=".8"/><path d="M9.6 3.3v3.3M14.4 3.3v3.3M9.6 17.4v3.3M14.4 17.4v3.3M3.3 9.6h3.3M3.3 14.4h3.3M17.4 9.6h3.3M17.4 14.4h3.3"/>'),
  memory:_SVG('<rect x="2.6" y="7.8" width="18.8" height="8.6" rx="1.4"/><path d="M6.2 16.4v2.4M10.1 16.4v2.4M13.9 16.4v2.4M17.8 16.4v2.4"/><path d="M6.4 11h3.2M12.2 11h5.4"/>'),
  display:_SVG('<rect x="2.7" y="4.3" width="18.6" height="12.4" rx="1.8"/><path d="M8.4 20.5h7.2M12 16.7v3.8"/><path d="M6.2 8.1h5.2"/>'),
  pulse:_SVG('<path d="M2.6 12.4h4.2l2-4.6 3.2 9.2 2.2-6 1.6 3.2h5.6"/>'),
  ship:_SVG('<path d="M12 2.4c2.9 2.7 4.1 6.6 4.1 10.8L12 16.4 7.9 13.2c0-4.2 1.2-8.1 4.1-10.8z"/><path d="M7.9 12.6L4 17.6l4-1.4M16.1 12.6L20 17.6l-4-1.4"/><circle cx="12" cy="9.4" r="1.7"/>'),
  gun:_SVG('<path d="M3 8.6h14.2V11H14l-1.2 3.4h-2.2V11H8.4v4.2H6.1V11H3z"/><path d="M17.2 8.6H21v1.2h-3.8"/>'),
  fleet:_SVG('<path d="M9 2.6c2.2 2 3.1 5 3.1 8.2L9 13.2 5.9 10.8C5.9 7.6 6.8 4.6 9 2.6z"/><path d="M16.5 8.2c1.6 1.5 2.3 3.7 2.3 6l-2.3 1.8-2.3-1.8c0-2.3.7-4.5 2.3-6z" opacity=".55"/>'),
  target:_SVG('<circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="4.3"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>'),
  deaths:_SVG('<path d="M6 11.4C6 7.8 8.7 5 12 5s6 2.8 6 6.4c0 1.9-.9 3.2-1.8 4V18a1 1 0 01-1 1H8.8a1 1 0 01-1-1v-2.6C6.9 14.6 6 13.3 6 11.4z"/><circle cx="9.6" cy="11.6" r="1.25" fill="currentColor" stroke="none"/><circle cx="14.4" cy="11.6" r="1.25" fill="currentColor" stroke="none"/><path d="M10.4 19v-2M13.6 19v-2"/>'),
  kills:_SVG('<path d="M12 3l1.7 4.2 4.5-1.3-1.3 4.5L21 12l-4.1 1.6 1.3 4.5-4.5-1.3L12 21l-1.7-4.2-4.5 1.3 1.3-4.5L3 12l4.1-1.6L5.8 5.9l4.5 1.3z"/><circle cx="12" cy="12" r="2"/>'),
  damage:_SVG('<path d="M12 2.8c3 3 5 5.6 5 9.1a5 5 0 01-10 0c0-1.7.7-3.1 1.8-4.3.3 1.2 1 2 2 2.3-.5-2.7.4-5.3 1.2-7.1z"/>'),
  time:_SVG('<circle cx="12" cy="13.6" r="7"/><path d="M12 13.6V9.6"/><path d="M9.4 2.9h5.2"/><path d="M12 2.9v2.6"/><path d="M18.6 7.2L20 5.8"/>'),
  calendar:_SVG('<rect x="4" y="5.4" width="16" height="14.6" rx="2"/><path d="M4 9.6h16"/><path d="M8 3.4v3M16 3.4v3"/><path d="M8 13h2.2M13.8 13H16M8 16.4h2.2M13.8 16.4H16"/>'),
  space:_SVG('<circle cx="12" cy="12" r="2.6"/><ellipse cx="12" cy="12" rx="9" ry="3.5" transform="rotate(28 12 12)"/><circle cx="19.7" cy="8.7" r="1" fill="currentColor" stroke="none"/>'),
  planet:_SVG('<circle cx="11" cy="11" r="5.6"/><ellipse cx="11" cy="11" rx="9.6" ry="3.3" transform="rotate(-24 11 11)"/>'),
  satellite:_SVG('<path d="M10.6 5.1l3.3 3.3-2.6 2.6-3.3-3.3z"/><path d="M14 9l4.4 4.4a5.3 5.3 0 01-5.3 0"/><path d="M4 20a8.2 8.2 0 018.2-8.2"/><circle cx="4.4" cy="19.6" r="1.2" fill="currentColor" stroke="none"/><path d="M15 4.5l4.5 4.5"/>'),
  tools:_SVG('<path d="M14.6 6.4a3.4 3.4 0 00-4.4 4.3L4 16.9 6.1 19l6.2-6.2a3.4 3.4 0 004.3-4.4l-2.1 2.1-1.9-.5-.5-1.9z"/><path d="M14.5 14.5L19 19"/>'),
  scales:_SVG('<path d="M12 4v16"/><path d="M7 7h10"/><path d="M7 7l-3 5.5a2.6 2.6 0 005.2 0z"/><path d="M17 7l-3 5.5a2.6 2.6 0 005.2 0z" opacity=".85"/><path d="M8.5 20h7"/>'),
  credits:_SVG('<ellipse cx="12" cy="7" rx="6.4" ry="2.5"/><path d="M5.6 7v5c0 1.4 2.8 2.5 6.4 2.5s6.4-1.1 6.4-2.5V7"/><path d="M5.6 12v4.6c0 1.4 2.8 2.5 6.4 2.5s6.4-1.1 6.4-2.5V12"/>'),
  spend:_SVG('<ellipse cx="10" cy="7" rx="6" ry="2.4"/><path d="M4 7v5c0 1.3 2.7 2.4 6 2.4 1 0 2-.1 2.8-.3"/><path d="M4 12v4.4c0 1.3 2.7 2.4 6 2.4"/><path d="M17 12v7M17 19l2.4-2.4M17 19l-2.4-2.4"/>'),
  shop:_SVG('<path d="M3.5 4.5h2l1.9 10.4h9.2l1.7-7.7H7"/><circle cx="9" cy="19" r="1.4"/><circle cx="16.2" cy="19" r="1.4"/>'),
  cargo:_SVG('<path d="M12 2.8l8.2 4.3v9.8L12 21.2l-8.2-4.3V7.1z"/><path d="M3.8 7.1L12 11.4l8.2-4.3M12 11.4v9.8"/>'),
  profile:_SVG('<rect x="3" y="5.4" width="18" height="13.2" rx="2"/><circle cx="8.6" cy="11" r="2.2"/><path d="M5.4 16c.4-1.7 1.7-2.5 3.2-2.5s2.8.8 3.2 2.5"/><path d="M14 10.2h4M14 13.4h4"/>'),
  hall:_SVG('<path d="M3.5 20.5h17"/><path d="M5 20.5V9.5l7-4 7 4v11"/><path d="M8.5 20.5v-6h7v6"/><path d="M5 9.5h14"/>'),
  members:_SVG('<circle cx="8.4" cy="9" r="2.6"/><circle cx="15.6" cy="9" r="2.6"/><path d="M4 18.6c.5-2.7 2.2-4.1 4.4-4.1 1.3 0 2.4.5 3.1 1.4"/><path d="M12.5 15.9c.7-.9 1.8-1.4 3.1-1.4 2.2 0 3.9 1.4 4.4 4.1"/>'),
  npc:_SVG('<rect x="5" y="8" width="14" height="11" rx="2.6"/><path d="M12 4.6v3.4"/><circle cx="12" cy="4" r="1.2" fill="currentColor" stroke="none"/><circle cx="9.6" cy="13" r="1.15" fill="currentColor" stroke="none"/><circle cx="14.4" cy="13" r="1.15" fill="currentColor" stroke="none"/><path d="M9.8 16h4.4"/><path d="M5 11.6H3.4M20.6 11.6H19"/>'),
  warning:_SVG('<path d="M12 4l8.5 15.2H3.5z"/><path d="M12 9.8v4.2"/><circle cx="12" cy="16.7" r="1" fill="currentColor" stroke="none"/>'),
  yes:_SVG('<circle cx="12" cy="12" r="8.4"/><path d="M8.3 12.2l2.6 2.6 4.8-5.6"/>'),
  no:_SVG('<circle cx="12" cy="12" r="8.4"/><path d="M9 9l6 6M15 9l-6 6"/>'),
  day:_SVG('<circle cx="12" cy="12" r="3.9"/><path d="M12 2.6v2.5M12 18.9v2.5M2.6 12h2.5M18.9 12h2.5M5.1 5.1l1.8 1.8M17.1 17.1l1.8 1.8M18.9 5.1l-1.8 1.8M6.9 17.1l-1.8 1.8"/>'),
  night:_SVG('<path d="M20 14.4A8 8 0 019.6 4 8 8 0 1020 14.4z"/>'),
  backup:_SVG('<path d="M5 4.5h11L19.5 8v11.4a1 1 0 01-1 1H5.5a1 1 0 01-1-1v-14a1 1 0 011-1z"/><path d="M8 4.5v4h6v-4"/><rect x="8" y="12.4" width="8" height="6"/>'),
  export:_SVG('<path d="M12 3.4v9.6"/><path d="M8 9l4 4 4-4"/><path d="M5 16.5v2.5a1 1 0 001 1h12a1 1 0 001-1v-2.5"/>'),
  import:_SVG('<path d="M12 13.4V3.8"/><path d="M8 7.8l4-4 4 4"/><path d="M5 16.5v2.5a1 1 0 001 1h12a1 1 0 001-1v-2.5"/>'),
  bug:_SVG('<path d="M12 8.5v10"/><ellipse cx="12" cy="13.2" rx="4.1" ry="5.1"/><path d="M9.6 7a2.4 2.4 0 014.8 0"/><path d="M8 10.4L5.2 8.6M16 10.4l2.8-1.8M7.9 13.4H4.4M16.1 13.4h3.5M8 16.6l-2.8 1.8M16 16.6l2.8 1.8"/>'),
  quit:_SVG('<path d="M14 3.6H6a1 1 0 00-1 1v14.8a1 1 0 001 1h8"/><path d="M10.5 12h9.5M17 9l3 3-3 3"/>'),
  reload:_SVG('<rect x="8" y="3.2" width="8" height="6.6" rx="1"/><rect x="8.8" y="9.8" width="6.4" height="10.4" rx="1"/><path d="M10.2 6h3.6"/>'),
  refresh:_SVG('<path d="M20 6.5v5h-5"/><path d="M4 17.5v-5h5"/><path d="M18.4 11a6.6 6.6 0 00-11.4-2.7L4 11M20 13l-3 2.7A6.6 6.6 0 015.6 13"/>'),
  transfer:_SVG('<path d="M4 8.5h13"/><path d="M13.5 5l3.5 3.5-3.5 3.5"/><path d="M20 15.5H7"/><path d="M10.5 12L7 15.5 10.5 19"/>'),
  prev:_SVG('<path d="M14.5 5.5L8 12l6.5 6.5"/>'),
  next:_SVG('<path d="M9.5 5.5L16 12l-6.5 6.5"/>'),
  stability:_SVG('<rect x="2.8" y="4.4" width="18.4" height="12.8" rx="2.2"/><path d="M6 11.4h2.4l1.5-3.2 2.4 6.4 1.6-3.2H18"/><path d="M8.8 20.4h6.4"/><path d="M12 17.2v3.2"/>'),
  bell:_SVG('<path d="M18 16.4H6l1.5-2.3V10a4.5 4.5 0 019 0v4.1z"/><path d="M10.2 19a1.9 1.9 0 003.6 0"/><path d="M12 5.5V3.6"/>'),
  star:_SVG('<path d="M12 3.4l2.6 5.7 6.2.7-4.6 4.2 1.3 6.1L12 17l-5.5 3.1 1.3-6.1L3.2 9.8l6.2-.7z"/>'),
  starOn:_SVG('<path d="M12 3.4l2.6 5.7 6.2.7-4.6 4.2 1.3 6.1L12 17l-5.5 3.1 1.3-6.1L3.2 9.8l6.2-.7z" fill="currentColor"/>'),
  blueprints:_SVG('<rect x="3.5" y="3.5" width="17" height="17" rx="1.8"/><path d="M3.5 9h17"/><path d="M9 9v11.5"/><path d="M12.5 13h5M12.5 16.5h3.5"/>')
};
function icon(name){ return ICON[name]||''; }

// ---- external link helpers ----
const RSI='https://robertsspaceindustries.com';
const wikiURL = name => 'https://starcitizen.tools/'+encodeURIComponent((name||'').replace(/ /g,'_'));
const citizenURL = h => `${RSI}/citizens/${encodeURIComponent(h||'')}`;
const orgURL = sid => `${RSI}/orgs/${encodeURIComponent(sid||'')}`;
function link(url, txt, cls){ return url ? `<a href="${url}" target="_blank" rel="noopener" class="${cls||''}">${txt}</a>` : txt; }

// ---- reusable builders ----
function kpiRow(cards){
  return `<div class="hero">`+cards.map(([i,v,l,s,cls])=>
    `<div class="kpi ${cls||''}"><div class="v">${i} ${v}</div><div class="l">${l}</div><div class="s">${s||''}</div></div>`).join('')+`</div>`;
}
// Quantum jumps are only in the log from patch 4.4 onward — older builds have no
// player-attributable jump event, so those scopes read N/A rather than a false 0.
function qtTile(v){
  return v.qt_known===false
    ? [icon('space'), 'N/A', 'Quantum jumps', 'not logged before patch 4.4', 'na']
    : [icon('space'), fmt(v.qt), 'Quantum jumps', v.qt_targets ? `arrivals · ${fmt(v.qt_targets)} targets set` : 'arrivals'];
}
// Your ASOP vehicle count only appears in the log from 4.8 ("Retrieved N entitlements
// out of M vehicules"). Older builds report an "entitlements" figure that counts pledge
// items, not ships — it reads 42, 68, then 209, 227 while the real fleet is ~60 — so it
// is deliberately NOT used rather than showing a number that is wrong.
function fleetTile(n){
  return n ? [icon('fleet'), n, 'Fleet size', 'ships at ASOP (all-time)']
           : [icon('fleet'), 'N/A', 'Fleet size', 'not logged before patch 4.8', 'na'];
}
function cardHTML(title, unit, innerId, extra){
  return `<div class="card" ${extra||''}><h3>${title}${unit?` <span class="u">— ${unit}</span>`:''}</h3><div id="${innerId}"></div></div>`;
}
function barsCard(title, unit, innerId){
  return `<div class="card"><h3>${title}${unit?` <span class="u">— ${unit}</span>`:''}</h3><div class="bars" id="${innerId}"></div></div>`;
}
// A titled group of cards. Used where one tab holds several distinct subjects (the
// Combat tab covers ship combat, on-foot combat and looting) — the numbered badge and
// full-width rule make the boundaries obvious instead of relying on a small caption.
function group(n, ic, title, sub, body){
  return `<section class="gsec">`+
    `<div class="ghead"><div class="gnum">${String(n).padStart(2,'0')}</div>`+
    `<div class="gic">${icon(ic)}</div>`+
    `<div class="gtx"><div class="gt">${title}</div><div class="gs">${sub||''}</div></div></div>`+
    `<div class="gbody">${body}</div></section>`;
}
// ISO YYYY-MM-DD -> DD-Mon-YYYY (e.g. 2025-07-27 -> 27-Jul-2025)
const ddmm = s => { const p=(s||'').split('-'); return p.length===3 ? `${String(p[2]).padStart(2,'0')}-${MON[+p[1]-1]||p[1]}-${p[0]}` : (s||''); };
// flight-recorder HUD chips: take-offs + first/last flown, for a ship
function shipMetaHTML(to,first,last){
  const cell=(k,v,cls)=>`<span class="mchip ${cls||''}"><span class="mk">${k}</span><span class="mv">${v}</span></span>`;
  let out='';
  if(to) out+=cell('TAKE-OFFS',fmt(to),'to');
  if(first) out+=cell('FIRST FLOWN',ddmm(first));
  if(last&&last!==first) out+=cell('LAST FLOWN',ddmm(last));
  return out?`<div class="mchips">${out}</div>`:'';
}
// HUD chips for a gun card: reloads (from AmmoRepool) + times carried in loadout.
// The RELOADS chip is always rendered so cards stay visually consistent: a real count
// when the scope recorded reloads, otherwise N/A (that build never logged them).
function gunMetaHTML(reloads,carried,known){
  const cell=(k,v,cls,ttl)=>`<span class="mchip ${cls||''}"${ttl?` title="${ttl}"`:''}><span class="mk">${k}</span><span class="mv">${v}</span></span>`;
  let out = known
    ? cell('RELOADS',fmt(reloads||0),'to','Magazine top-ups recorded for this weapon')
    : cell('RELOADS','N/A','na','This patch’s game build never wrote reload events to the log');
  if(carried) out+=cell('CARRIED',fmt(carried),'','Times stowed on your back or holstered');
  return `<div class="mchips">${out}</div>`;
}
// "Mon D, YYYY" / "Month D, YYYY" -> DD-Mon-YYYY (for the scraped RSI enlist date)
function fmtLongDate(s){
  const m=(s||'').match(/^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$/); if(!m) return s||'';
  const names=['january','february','march','april','may','june','july','august','september','october','november','december'];
  const i=names.findIndex(n=>n.startsWith(m[1].toLowerCase().slice(0,3)));
  return `${String(m[2]).padStart(2,'0')}-${i>=0?MON[i]:m[1]}-${m[3]}`;
}
function toItems(pairs, color, link){
  // link: true|'wiki' -> SC wiki ; 'citizen' -> RSI player profile ; else none
  const url = n => (link==='citizen') ? citizenURL(n) : (link ? wikiURL(n) : null);
  return (pairs||[]).map(([n,c])=>({label:n, full:n, value:c, disp:fmt(c), color, href:url(n)}));
}

// ---- showcase (top ships / guns with art + classification) ----
function chip(txt, cls){ return txt ? `<span class="chip-c ${cls||''}">${esc(txt)}</span>` : ''; }
function showCard(item, rank, kind){
  const glyph = kind==='ship'?ICON.ship:ICON.gun;
  const url = wikiURL(item.name);   // wiki has richer specs for both ships & guns
  // manufacturer logo sits as a badge on the art (bigger + on the right); the
  // maker name shows as text only when there's no logo
  const badge = item.logo ? `<div class="show-logo-badge"><img src="${item.logo}" alt="${esc(item.man||'')}" title="${esc(item.man||'')}" loading="lazy"></div>` : '';
  const img = item.img
    ? `<div class="show-img" style="background-image:url('${item.img}')"><div class="show-rank">#${rank}</div>${badge}</div>`
    : `<div class="show-img placeholder">${glyph}<div class="show-rank">#${rank}</div>${badge}</div>`;
  const manSub = (!item.logo && item.man) ? `<div class="show-sub">${esc(item.man)}</div>` : '';
  let chips, sub, cnt;
  if(kind==='ship'){
    chips = `<div class="show-chips">${chip(item.size,'sz '+(item.size||''))}${chip(item.role)}</div>`;
    sub = manSub;
    cnt = `<div class="show-count" title="Distinct play sessions this ship appeared in — not individual flights">${fmt(item.count)} sessions flown</div>`+
          shipMetaHTML(item.takeoffs, item.first, item.last);
  } else {
    chips = `<div class="show-chips">${chip(item.type)}${chip(item.ammo, (item.ammo||'').toLowerCase())}</div>`;
    sub = manSub;
    cnt = `<div class="show-count g" title="How many times you drew this weapon into your hand — not shots fired (SC doesn't log those)">drawn ${fmt(item.count)}×</div>`+
          gunMetaHTML(item.reloads, item.carried, item.reloads_known!==false);
  }
  const name = `<div class="show-name">${link(url, esc(item.name)+' ↗', 'lk')}</div>`;
  const man = (!item.logo && item.code) ? `<span class="show-man" title="${esc(item.man||'Manufacturer')}">${esc(item.code)}</span>` : '';
  return `<div class="show-card">${img}<div class="show-body">${man}${name}${chips}${sub}${cnt}</div></div>`;
}
function showcaseHTML(){
  const sc = (A().showcase && A().showcase[current]) || {ships:[],guns:[]};
  const scope = current==='all' ? 'career' : ('patch '+current);
  const ships = sc.ships.map((s,i)=>showCard(s,i+1,'ship')).join('') || '<div class="empty">No ships here.</div>';
  const guns = sc.guns.map((g,i)=>showCard(g,i+1,'gun')).join('') || '<div class="empty">No guns here.</div>';
  return `<div class="row-title">Signature ships <span class="tag">· most-flown · ${scope} · art from RSI</span></div>`+
    `<div class="showcase">${ships}</div>`+
    `<div class="row-title">Signature guns <span class="tag">· most-drawn · ${scope}</span></div>`+
    `<div class="showcase">${guns}</div>`;
}
function profileHTML(){
  const p = A().profile; if(!p) return '';
  const av = p.avatar ? `<img src="${p.avatar}" alt="">` : '';
  const org = p.org_name ? `${esc(p.org_name)} [${esc(p.org_sid||'')}] · ${esc(p.org_rank||'')}` : 'No affiliation';
  const facts = [['Citizen record', p.record],['Rank', p.rank],['Enlisted', fmtLongDate(p.enlisted)],['Fluency', p.fluency],['Location', p.location]]
    .filter(f=>f[1]).map(f=>`<div class="pf"><div class="l">${f[0]}</div><div class="v">${esc(f[1])}</div></div>`).join('');
  const orgL = p.org_sid ? link(orgURL(p.org_sid), org, 'lk') : org;
  return `<div class="profile">${av}<div class="pmain"><div class="ph">${link(citizenURL(p.handle), esc(p.handle)+' ↗','lk')}</div><div class="psub">${orgL}</div></div><div class="pfacts">${facts}</div></div>`;
}
// ---- weekday x hour heatmap ----
function heatmap(el, grid){
  let max=1; grid.forEach(r=>r.forEach(x=>{ if(x>max) max=x; }));
  let h='<div class="heat"><div class="hl"></div>';
  for(let x=0;x<24;x++) h+=`<div class="hx">${x%3===0?x:''}</div>`;
  DOW.forEach((d,r)=>{ h+=`<div class="hl">${d}</div>`;
    for(let c=0;c<24;c++){ const v=grid[r][c]; const a=v?(0.12+0.88*v/max):0;
      h+=`<div class="hc" style="background:rgba(var(--heat-rgb),${a.toFixed(2)})" title="${d} ${c}:00 — ${v} sessions"></div>`; } });
  h+='</div>'; el.innerHTML=h;
}

// ---- vertical bar chart with Y-axis gridlines + labels ----
function niceMax(v){
  if(v<=0) return 1;
  const p=Math.pow(10,Math.floor(Math.log10(v)));
  for(const m of [1,1.2,1.5,2,2.5,3,4,5,6,8,10]){ if(p*m>=v) return p*m; }
  return p*10;
}
function vbars(el, items, opts={}){
  el.classList.add('hud-sweep');
  const rot=!!opts.rot;
  const W=opts.w||380, H=opts.h||210;
  const padL=36, padR=12, padT=14, padB=rot?52:26;
  const plotW=W-padL-padR, plotH=H-padT-padB;
  const max=niceMax(Math.max(1,...items.map(d=>d.value)));
  const n=items.length, step=plotW/n, bw=Math.min(opts.maxbw||40, step*0.7);
  const yOf=v=>padT+plotH*(1-v/max);
  let s=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">`;
  // gridlines + y labels
  const lines=4;
  for(let g=0; g<=lines; g++){
    const val=max*g/lines, y=yOf(val);
    s+=`<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W-padR}" y2="${y.toFixed(1)}" style="stroke:var(--grid)"/>`;
    s+=`<text class="axis" x="${padL-6}" y="${(y+3).toFixed(1)}" text-anchor="end">${opts.fmtY?opts.fmtY(val):Math.round(val)}</text>`;
  }
  const click=!!opts.onClick;
  items.forEach((d,i)=>{
    const bx=padL + i*step + (step-bw)/2, by=yOf(d.value), bh=padT+plotH-by;
    s+=`<rect class="hover" x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(0,bh).toFixed(1)}" rx="2.5" fill="${d.color||'var(--cyan)'}"><title>${esc(d.full||d.label)}: ${d.disp!==undefined?d.disp:d.value}</title></rect>`;
    const lx=bx+bw/2, ly=H-padB+13;
    if(rot) s+=`<text class="axis" x="${lx.toFixed(1)}" y="${ly}" text-anchor="end" transform="rotate(-45 ${lx.toFixed(1)} ${ly})">${esc(d.label)}</text>`;
    else if(d.label!=='') s+=`<text class="axis" x="${lx.toFixed(1)}" y="${ly}" text-anchor="middle">${esc(d.label)}</text>`;
  });
  // full-height transparent hit columns for click-to-filter
  if(click){
    items.forEach((d,i)=>{
      const cx0=padL + i*step;
      s+=`<rect class="hitcol" data-i="${i}" x="${cx0.toFixed(1)}" y="${padT}" width="${step.toFixed(1)}" height="${(H-padT-4).toFixed(1)}" fill="transparent"><title>${esc(d.full||d.label)}: ${d.disp!==undefined?d.disp:d.value} — click to filter</title></rect>`;
    });
  }
  s+='</svg>';
  el.innerHTML=s;
  if(click){
    el.querySelectorAll('rect.hitcol').forEach(r=>
      r.addEventListener('click',()=>opts.onClick(items[+r.dataset.i], +r.dataset.i)));
  }
}

// ---- horizontal bar list ----
function hbars(el, items, opts={}){
  if(!items.length){ el.innerHTML='<div class="empty">None recorded here.</div>'; return; }
  const max=Math.max(1,...items.map(d=>d.value));
  el.innerHTML = items.map((d,i)=>{
    const pct=Math.max(2, 100*d.value/max);
    const nm = d.href ? link(d.href, esc(d.label), 'lk') : esc(d.label);
    const sub = d.subHtml ? `<div class="bsub">${d.subHtml}</div>` : (d.sub ? `<div class="bsub">${esc(d.sub)}</div>` : '');
    return `<div class="bar-row ${d.tool?'tool':''}">`+
      `<div class="bar-head"><div class="nm"><span class="rk">${i+1}</span>${nm}</div>`+
      `<div class="vv">${d.disp!==undefined?d.disp:d.value}</div></div>`+sub+
      `<div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div></div>`;
  }).join('');
}

// ---- donut: tilted 3D ring with leader-line labels (SC star-map vibe) ----
let _dn=0;
function donut(el, parts, center){
  el.classList.add('hud-sweep');
  const total=parts.reduce((a,p)=>a+p.value,0);
  const active=parts.filter(p=>p.value>0);
  const id='dn'+(++_dn);
  const W=400, H=250, cx=200, cy=112, tilt=0.58,
        Rx=76, Ry=Rx*tilt, rin=0.56, rx=Rx*rin, ry=Ry*rin, depth=18;
  const O=a=>[cx+Rx*Math.cos(a), cy+Ry*Math.sin(a)];
  const I=a=>[cx+rx*Math.cos(a), cy+ry*Math.sin(a)];
  const F=n=>(+n).toFixed(2);
  let s=`<svg class="donut-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">`+
    `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">`+
    `<stop offset="0" style="stop-color:var(--track-line)"/><stop offset="1" style="stop-color:var(--bg)"/></linearGradient></defs>`;

  if(total<=0 || !active.length){
    s+=`<ellipse cx="${cx}" cy="${cy}" rx="${F((Rx+rx)/2)}" ry="${F((Ry+ry)/2)}" fill="none" `+
       `stroke-width="${F(Rx-rx)}" style="stroke:var(--track-line)"/>`+
       `<text x="${cx}" y="${F(cy+6)}" text-anchor="middle" style="fill:var(--muted);font-size:24px;font-weight:800;font-family:var(--font-display)">${center.big}</text></svg>`;
    el.innerHTML=`<div class="donut-wrap">${s}</div>`; return;
  }

  // 3D depth rim — extrude the front (lower) half of the outer silhouette
  const [ax,ay]=O(0), [bx,by]=O(Math.PI);
  s+=`<path d="M${F(ax)} ${F(ay)} A${F(Rx)} ${F(Ry)} 0 0 1 ${F(bx)} ${F(by)} L${F(bx)} ${F(by+depth)} `+
     `A${F(Rx)} ${F(Ry)} 0 0 0 ${F(ax)} ${F(ay+depth)} Z" fill="url(#${id})" stroke="rgba(0,0,0,.28)" stroke-width="0.5"/>`;

  // top faces (annular elliptical sectors)
  const segs=[]; let ang=-Math.PI/2;
  if(active.length===1){
    const p=active[0];
    s+=`<path class="dseg" fill-rule="evenodd" fill="${p.color}" d="`+
       `M${F(cx-Rx)} ${F(cy)} a${F(Rx)} ${F(Ry)} 0 1 0 ${F(2*Rx)} 0 a${F(Rx)} ${F(Ry)} 0 1 0 ${F(-2*Rx)} 0 Z `+
       `M${F(cx-rx)} ${F(cy)} a${F(rx)} ${F(ry)} 0 1 0 ${F(2*rx)} 0 a${F(rx)} ${F(ry)} 0 1 0 ${F(-2*rx)} 0 Z">`+
       `<title>${esc(p.label)}: ${fmt(p.value)} (100%)</title></path>`;
    segs.push({p, am:-Math.PI/2});
  } else {
    active.forEach(p=>{
      const a1=ang, a2=ang+2*Math.PI*p.value/total, am=(a1+a2)/2; ang=a2;
      const large=(a2-a1)>Math.PI?1:0;
      const [o1x,o1y]=O(a1),[o2x,o2y]=O(a2),[i2x,i2y]=I(a2),[i1x,i1y]=I(a1);
      s+=`<path class="dseg" fill="${p.color}" d="M${F(o1x)} ${F(o1y)} A${F(Rx)} ${F(Ry)} 0 ${large} 1 ${F(o2x)} ${F(o2y)} `+
         `L${F(i2x)} ${F(i2y)} A${F(rx)} ${F(ry)} 0 ${large} 0 ${F(i1x)} ${F(i1y)} Z">`+
         `<title>${esc(p.label)}: ${fmt(p.value)} (${Math.round(100*p.value/total)}%)</title></path>`;
      segs.push({p, am});
    });
  }
  // center total (sits in the hole)
  s+=`<text x="${cx}" y="${F(cy+7)}" text-anchor="middle" style="fill:var(--txt);font-size:27px;font-weight:800;font-family:var(--font-display)">${center.big}</text>`;

  // leader lines + labels, de-collided vertically on each side
  const en=segs.map(g=>{ const [ex,ey]=O(g.am); return {...g, ex, ey,
    side:(Math.cos(g.am)>=0?1:-1), ideal:cy+(Ry+8)*Math.sin(g.am)}; });
  [1,-1].forEach(side=>{
    const grp=en.filter(g=>g.side===side).sort((a,b)=>a.ideal-b.ideal);
    if(!grp.length) return;
    const gap=26, top=18, bot=H-16; let prev=-1e9;
    grp.forEach(g=>{ g.ly=Math.max(g.ideal, prev+gap); prev=g.ly; });
    const over=grp[grp.length-1].ly-bot; if(over>0) grp.forEach(g=>g.ly-=over);
    const un=top-grp[0].ly; if(un>0) grp.forEach(g=>g.ly+=un);
    grp.forEach(g=>{
      const labelX=side===1?W-6:6, knee=side===1?cx+Rx+16:cx-Rx-16, anchor=side===1?'end':'start';
      s+=`<polyline points="${F(g.ex)},${F(g.ey)} ${F(knee)},${F(g.ly)} ${F(labelX+(side===1?-2:2))},${F(g.ly)}" fill="none" stroke="${g.p.color}" stroke-width="1.4" opacity="0.6"/>`+
         `<circle cx="${F(g.ex)}" cy="${F(g.ey)}" r="2.4" fill="${g.p.color}"/>`+
         `<text x="${labelX}" y="${F(g.ly-3)}" text-anchor="${anchor}" style="fill:var(--txt);font-size:12.5px;font-weight:600;font-family:var(--font-display)">${esc(g.p.label)}</text>`+
         `<text x="${labelX}" y="${F(g.ly+11)}" text-anchor="${anchor}" style="fill:var(--muted);font-size:11px;font-family:var(--font-mono)">${fmt(g.p.value)} · ${Math.round(100*g.p.value/total)}%</text>`;
    });
  });
  s+='</svg>';
  el.innerHTML=`<div class="donut-wrap">${s}</div>`;
}

const NOTE = noteFold(
  `Built from your client logs. Playtime, ships, missions, deaths, travel and loadout are real; kills, K/D and accuracy aren't in the log any more.`,
  `CIG moved combat server-side in the 2026 builds. A weapon's drawn count is how often you pulled it into your hand, not shots fired. Reloads come from the game's reload events (magazine swaps from 4.10), carried from your stow and holster slots. Armour, attachments and props are left out of the loadout lists. Ship deaths are the only death type logged.`);
// Patch labels. A numeric version reads "Patch 4.8"; a Tech-Preview feature branch
// carries no version at all (Branch: scp-crafting) so it is named after the feature
// it was previewing, and 'unknown' is the catch-all for anything else.
// "scp-crafting" -> "Crafting"
function pFeature(p){
  return p.replace(/^scp-/,'').replace(/[-_]+/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
}
// full label for headings and the dropdown
function pLabel(p){
  if(p==='unknown') return 'Unversioned build';
  if(/^\d/.test(p)) return 'Patch '+p;
  return pFeature(p)+' Preview';
}
// short label for chart axes, where space is tight
function pShort(p){
  if(p==='unknown') return '—';
  if(/^\d/.test(p)) return p;
  return pFeature(p)+' TP';
}
// the build string a patch actually ran, when we know it (a feature branch's only
// version) — shown so a preview isn't just a bare name
function pBuild(p){
  const v=(A().patches[p]||{}).build;
  return (v && !/^\d/.test(p)) ? v : '';
}
const scopeLabel = () => current==='all' ? 'entire career' : pLabel(current).toLowerCase();
function metaLine(v){ return `<div class="patch-meta">${scopeLabel()} · ${v.first?ddmm(v.first):'—'} → ${v.last?ddmm(v.last):'—'} · ${fmt(v.hours)} h · ${fmt(v.sessions)} sessions · ${v.active_days} active days</div>`; }
// One plain sentence on the page; the detail one click away. Most people only need to
// know whether to trust the number. The rest can open it.
function noteFold(line, more){
  if(!more) return `<div class="note">${line}</div>`;
  return `<details class="fold nf"><summary>${line}<span class="nfm">more</span></summary><div class="nfb">${more}</div></details>`;
}
function callout(icon, html){ return `<div class="callout"><span class="ci">${icon}</span><div>${html}</div></div>`; }

// ==== SECTION RENDERERS ====
const SECTIONS = {
  overview:{title:'Overview', fn:secOverview},
  flight:{title:'Flight & Travel', fn:secFlight},
  combat:{title:'Combat', fn:secCombat},
  legacy:{title:'Legacy Combat', fn:secLegacy},
  missions:{title:'Missions', fn:secMissions},
  // Travel merged into Flight (it only had 2 stats; fuel/distance/POI aren't logged).
  // Kept as an alias so an existing bookmark or in-page link still resolves.
  travel:{title:'Flight & Travel', fn:secFlight, alias:'flight'},
  economy:{title:'Economy', fn:secEconomy},
  blueprints:{title:'Blueprints', fn:secBlueprints},
  activity:{title:'Activity', fn:secActivity},
  system:{title:'Your Machine', fn:secSystem},
  stability:{title:'Stability & Crashes', fn:secStability},
  orgs:{title:'Orgs & Profile', fn:secOrgs},
};

// Compact secondary stat — a horizontal strip rather than another big tile, so the
// snapshot reads as "four headline numbers, then supporting detail" instead of eight
// equal-weight cards wrapping into a lopsided 6 + 2.
function miniTile(ic, val, label, na){
  return `<div class="mini${na?' na':''}"><span class="mi">${ic}</span>`+
    `<span class="mv">${val}</span><span class="ml">${label}</span></div>`;
}
function secOverview(v){
  const c=v;
  const qt=qtTile(c);
  const K=`<div class="snap">`+
    kpiRow([
      [icon('time'), fmt(Math.round(c.hours)), 'Hours played', `longest ${c.longest_h} h session`],
      [icon('calendar'), c.active_days, 'Days played', `${fmt(c.sessions)} sessions · avg ${c.avg_min} min`],
      [icon('ship'), c.ships_unique, 'Distinct ships', `types piloted`],
      [icon('missions'), fmt(c.missions.total), 'Missions', `${c.missions.rate}% completed`],
    ])+
    `<div class="minis">`+
      miniTile(icon('deaths'), fmt(c.deaths), 'Ship deaths')+
      miniTile(qt[0], qt[1], 'Quantum jumps', qt[4]==='na')+
      miniTile(icon('cargo'), fmt(c.containers_looted||0), 'Containers looted')+
      miniTile(icon('gun'), fmt(c.drawn_total||0), 'Weapons drawn')+
      miniTile(icon('transfer'), fmt(c.transfers||0), 'Items moved')+
      miniTile(icon('space'), fmt(c.takeoffs_total||0), 'Take-offs')+
    `</div></div>`;
  const reset = current!=='all' ? `<button class="chart-reset" id="patchReset">↩ View career</button>` : '';
  const chartCard = `<div class="card"><h3 style="display:flex;align-items:center;justify-content:space-between;gap:12px">`+
    `<span>Playtime by patch <span class="u">— click a bar to filter</span></span>${reset}</h3>`+
    `<div id="patchChart"></div></div>`;
  $('#content').innerHTML = profileHTML()+
    `<div class="row-title">Snapshot <span class="tag">${scopeLabel()}</span></div>`+K+
    chartCard+showcaseHTML()+`${NOTE}`;
  // axis gets the short name; the hover card carries the full name, the build string
  // (a feature preview's only version) and when you played it
  const pfull = k => {
    const p=A().patches[k]||{}, v=pBuild(k);
    const when = p.first ? ddmm(p.first)+(p.last&&p.last!==p.first?' → '+ddmm(p.last):'') : '';
    return pLabel(k)+(v?' · build '+v:'')+(when?' · '+when:'');
  };
  const items = A().patch_playtime.map(([k,h])=>({key:k, label:pShort(k), value:h, disp:h+' h', full:pfull(k),
    color: k===current?'var(--amber)':'var(--cyan)'}));
  vbars($('#patchChart'), items, {h:240, w:Math.max(520, items.length*70), maxbw:52, fmtY:x=>x+'h',
    onClick:(it)=>{ current = (current===it.key) ? 'all' : it.key; renderPatches(); render(); }});
  const rb=$('#patchReset'); if(rb) rb.onclick=()=>{ current='all'; renderPatches(); render(); };
}

function secFlight(v){
  // per-session/per-hour rates only mean something where jumps were actually logged
  const jps = (v.qt_known!==false && v.sessions) ? (v.qt/v.sessions).toFixed(1) : null;
  const jph = (v.qt_known!==false && v.hours>0) ? (v.qt/v.hours).toFixed(1) : null;
  $('#content').innerHTML=metaLine(v)+
    group(1,'ship','Ships &amp; hulls','What you flew — hulls, sizes and roles',
      kpiRow([
        [icon('ship'), v.ships_unique, 'Distinct ships', `types piloted`],
        [icon('flight'), fmt(v.ships_flown_total), 'Ship boardings', `sessions × ship`],
        [icon('space'), fmt(v.takeoffs_total||0), 'Take-offs', `flights recorded`],
        fleetTile(v.fleet_max),
      ])+
      cardHTML('Ship size mix','sessions by hull size','sizeChart')+
      `<div class="grid2" style="margin-top:16px">`+
      barsCard('Most-flown ships','sessions flown · take-offs · first / last','shipBars')+
      barsCard('Roles flown','sessions by ship role','roleBars')+`</div>`
    )+
    group(2,'satellite','Travel &amp; navigation','Where you went — quantum travel and the systems you visited',
      kpiRow([
        qtTile(v),
        [icon('planet'), v.systems.length, 'Systems visited', `of 3 in the 'verse`],
        (jps!==null ? [icon('flight'), jps, 'Jumps / session', `average per play session`]
                    : [icon('flight'), 'N/A', 'Jumps / session', `no jump data`, 'na']),
        (jph!==null ? [icon('time'), jph, 'Jumps / hour', `travel intensity`]
                    : [icon('time'), 'N/A', 'Jumps / hour', `no jump data`, 'na']),
      ])+
      barsCard('Systems visited','sessions with location activity there','sysBars')+
      noteFold(
        `Systems come from location names in the log. Fuel and distance aren't logged.`,
        `Stanton, Pyro and Nyx are the only live systems; a session counts for one if any location there shows up. Refuelling is a server-side transaction and the client only logs an error with no amount, so fuel used and refuel spend can't be shown. Fuel pods bought at a shop count under Economy — that's a purchase, not a refuel.`)
    )+
    group(3,'planet','Where you\u2019ve been','Read from the HUD notices the game shows you — jurisdictions, armistice zones, hangar requests',
      (v.hud_known ? kpiRow([
        [icon('hall'), fmt(v.hangars||0), 'Hangars requested', 'ASOP hangar calls completed'],
        [icon('planet'), fmt(v.lz_visits||0), 'Landing-zone visits', 'armistice-zone stays, 10 min apart or more'],
        [icon('space'), fmt((v.juris||[]).length), 'Jurisdictions', 'whose space you entered'],
        (()=>{ const p=(v.juris||[]).filter(([k])=>/Ungoverned|Rough & Ready|People/.test(k)).reduce((a,[,s])=>a+s,0);
               return p ? [icon('warning'), fmt(p), 'Pyro sessions', 'entered Ungoverned / Rough & Ready / People\u2019s Alliance space'] : [icon('warning'), '0', 'Pyro sessions', 'no lawless space entered', 'na']; })(),
      ])+barsCard('Jurisdictions entered','sessions · total entries','jurisBars')
      : `<div class="card"><div class="empty">The HUD notices these come from are only in the log from patch 4.5 onward.</div></div>`)+
      noteFold(
        `From the HUD notices the game writes to the log, 4.5 onward.`,
        `A jurisdiction is who owns the space: UEE for open Stanton space, the four corporations for their planets, Ungoverned, Rough &amp; Ready and People\u2019s Alliance for Pyro, Klescher for prison. Armistice-zone notices fire at every hangar door, so entries within ten minutes count as one visit.`))
  ;
  const shipItems=(v.ships_top||[]).map(([n,c,first,last,to])=>({label:n, full:n, value:c,
    disp:`${fmt(c)} <span class="vv-u">sess</span>`, href:wikiURL(n),
    subHtml: shipMetaHTML(to,first,last)}));
  hbars($('#shipBars'), shipItems);
  const sz=v.ship_size.map(([n,c],i)=>({label:n,value:c,color:['#f4a92a','#5bd1e6','#6fbf7f','#ff5468','#b98bff','#ff9147'][i%6]}));
  donut($('#sizeChart'), sz, {big:v.ships_unique, small:'ships'});
  hbars($('#roleBars'), toItems(v.ship_role,'#5bd1e6'));
  hbars($('#sysBars'), toItems(v.systems,'#b98bff'));
  const jb=$('#jurisBars'); if(jb) hbars(jb, (v.juris||[]).map(([k,s,n])=>({label:k, value:s, disp:`${fmt(s)} <span class="vv-u">sess</span> · ${fmt(n)} entries`})));
}

function secCombat(v){
  const dc=v.death_causes;
  const guns=v.weapons_top.filter(w=>!w[2]), tools=v.weapons_top.filter(w=>w[2]);
  const lb=v.loot_boxes||{};
  const lootSub=['Small','Medium','Large','Other'].filter(k=>lb[k]).map(k=>fmt(lb[k])+' '+k[0]).join(' · ')||'crates & lockers';
  const info = callout(icon('satellite'),
    `Current-patch combat (${scopeLabel()}). The 2026 builds only log your ship deaths — no kills, K/D or damage, and no on-foot deaths. `+
    `Loadout is ranked by how often you drew each weapon. Your old kills are under `+
    `<a class="clink" onclick="gotoSec('legacy')">Legacy Combat</a> →.`);
  $('#content').innerHTML=metaLine(v)+info+
    group(1,'ship','Ship combat','How you lost ships — all the client log records',
      kpiRow([
        [icon('deaths'), v.deaths, 'Ship deaths', `ships you lost`],
        [icon('kills'), dc.collision, 'By collision', `flew into something`],
        [icon('damage'), dc.other, 'Destroyed / other', `shot down, hazards…`],
      ])+
      cardHTML('How your ship was lost','ship-destruction deaths only','causeChart')+
      `<div style="margin-top:16px">`+barsCard('Ships you died in most','deaths per ship','deathShips')+`</div>`
    )+
    group(2,'gun','FPS combat','Your on-foot loadout — ranked by how often you drew each weapon',
      kpiRow([
        [icon('gun'), v.guns_distinct||guns.length, 'Guns carried', `distinct weapons`],
        (v.reloads_total
          ? [icon('reload'), fmt(v.reloads_total), 'Reloads', `magazines topped up`]
          : [icon('reload'), 'N/A', 'Reloads', `not logged in this patch`, 'na']),
        [icon('tools'), v.tools_distinct||tools.length, 'Tools carried', `distinct utility`],
      ])+
      `<div class="grid2">`+
      barsCard('Favorite guns','times drawn','gunBars')+
      barsCard('Tools & utility','times drawn','toolBars')+`</div>`
    )+
    group(3,'cargo','Looting &amp; inventory','What you hauled — client-side events, so this spans every patch',
      kpiRow([
        [icon('cargo'), fmt(v.containers_looted||0), 'Containers looted', lootSub],
        [icon('transfer'), fmt(v.transfers||0), 'Items transferred', `moved between inventories`],
        [icon('deaths'), fmt(v.corpse_loots||0), 'Corpse loots (approx.)', `gear stripped off NPC bodies`],
      ])
    )+
    `${NOTE}`;
  hbars($('#gunBars'), toItems(guns.map(w=>[w[0],w[1]]), null, true));
  hbars($('#toolBars'), toItems(tools.map(w=>[w[0],w[1]]), null, true).map(x=>({...x,tool:true})));
  donut($('#causeChart'), [{label:'Collision',value:dc.collision,color:'#ff9147'},{label:'Destroyed / other',value:dc.other,color:'#ff5468'}], {big:v.deaths, small:'ship deaths'});
  hbars($('#deathShips'), toItems(v.death_ships,'#ff5468', true));
}

function renderCombat(v, L, o){
  const head=(o.prefix||'');
  if(!L || !L.has_data){
    $('#content').innerHTML=head+
      `<div class="card"><div class="empty" style="padding:24px 4px;font-size:14px;line-height:1.7">${o.empty}</div></div>`;
    if(o.after) o.after();
    return;
  }
  const sh=L.ship, fp=L.fps;
  $('#content').innerHTML=head+metaLine(v)+
    group(1,'ship','Ship combat','Kills and losses while flying — from the era the log recorded them',
      kpiRow([
        [icon('target'), fmt(sh.kill_pvp), 'PvP ship kills', `players downed`],
        [icon('deaths'), fmt(sh.death_pvp), 'PvP ship deaths', `killed by players`],
        [icon('scales'), sh.pvp_kd, 'Ship PvP K/D', `ratio`],
        [icon('npc'), fmt(sh.kill_pve), 'PvE ship kills', `NPC ships`],
        [icon('ship'), fmt(L.veh_kills), 'Ships destroyed', `vehicles blown up`],
        [icon('kills'), fmt(L.ram_kills), 'Ram kills', `by collision`],
      ])+
      `<div class="grid2">`+
      barsCard('Deadliest ship weapons','kills dealt','lcWpnS')+
      barsCard('Ships you rammed with','ram / collision kills','lcRam')+`</div>`
    )+
    group(2,'gun','FPS combat','Kills and losses on foot',
      kpiRow([
        [icon('target'), fmt(fp.kill_pvp), 'PvP FPS kills', `players downed`],
        [icon('deaths'), fmt(fp.death_pvp), 'PvP FPS deaths', `killed by players`],
        [icon('scales'), fp.pvp_kd, 'FPS PvP K/D', `ratio`],
        [icon('npc'), fmt(fp.kill_pve), 'PvE FPS kills', `NPCs on foot`],
        [icon('warning'), fmt(L.suicide), 'Suicides', `self-inflicted`],
      ])+
      barsCard('Deadliest FPS weapons','kills dealt','lcWpnF')
    )+
    group(3,'members','Rivals',o.rivalsTag,
      `<div class="grid2">`+
      barsCard('Players you killed most','PvP kills','lcKilled')+
      barsCard('Players who killed you most','PvP deaths','lcBy')+`</div>`
    )+
    `<div class="note">${o.note}</div>`;
  hbars($('#lcWpnS'), toItems(L.top_weapons_ship, '#ff9147', true));
  hbars($('#lcRam'), toItems(L.top_ram_ships, '#ff5468', true));
  hbars($('#lcWpnF'), toItems(L.top_weapons_fps, '#42d0e6', true));
  hbars($('#lcKilled'), toItems(L.top_killed, '#4ade80', 'citizen'));
  hbars($('#lcBy'), toItems(L.top_killedby, '#ff5468', 'citizen'));
  if(o.after) o.after();
}

let legacyVenue='pu';
function _lgKills(L){ return L ? (L.ship.kills+L.fps.kills) : 0; }
function secLegacy(v){
  const lg=v.legacy||{}, pu=lg.pu, ac=lg.ac;
  const puHas=pu&&pu.has_data, acHas=ac&&ac.has_data;
  if(legacyVenue==='pu'&&!puHas&&acHas) legacyVenue='ac';
  if(legacyVenue==='ac'&&!acHas&&puHas) legacyVenue='pu';
  const pill=(k,label,L,has)=>`<button class="vpill ${legacyVenue===k?'on':''}" data-v="${k}" ${has?'':'disabled'}>`+
    `${label}<span class="vc">${has?fmt(_lgKills(L))+' kills':'no data'}</span></button>`;
  const anyData = puHas || acHas;
  const info = callout(icon('legacy'),
    `Real kills, deaths and K/D — but only from 4.3 and earlier; CIG stopped logging combat client-side in 4.4, which is why `+
    `<a class="clink" onclick="gotoSec('combat')">Combat</a> can only show ship deaths. The toggle keeps the `+
    `<b class="ct">Persistent Universe</b> apart from <b class="ct">Arena Commander</b>, where scores get lopsided by design.`);
  const prefix=`<div class="row-title">Legacy combat <span class="tag">real PvP / PvE — pre-4.4 patches (4.3 &amp; below)</span></div>`+
    (anyData ? info : '')+
    `<div class="vtoggle">${pill('pu',icon('space')+' Persistent Universe',pu,puHas)}${pill('ac',icon('combat')+' Arena Commander',ac,acHas)}</div>`;
  const wire=()=>document.querySelectorAll('#content .vpill').forEach(b=>b.onclick=()=>{
    if(!b.disabled&&b.dataset.v!==legacyVenue){ legacyVenue=b.dataset.v; secLegacy(view()); }});
  const opts = legacyVenue==='pu' ? {
      prefix, after:wire, rivalsTag:'open-world PvP · ship + on-foot',
      empty:`No <b>persistent-universe</b> combat for <b>${scopeLabel()}</b> — pick a pre-4.4 patch (4.3 or below) or Career.`,
      note:`Open-world combat only; Arena Commander is on the other toggle. Ship vs FPS is split by the killing blow. Nothing after 4.3 — CIG stopped logging combat client-side.`
    } : {
      prefix, after:wire, rivalsTag:'lobby PvP · ship + on-foot',
      empty:`No <b>Arena Commander</b> matches for <b>${scopeLabel()}</b> — pick a pre-4.4 patch (4.3 or below) or Career.`,
      note:`Arena Commander matches, kept out of your PU record. Scores here get lopsided by design — private matches, reward farming, feeding a friend.`
    };
  renderCombat(v, lg[legacyVenue], opts);
}

function secEconomy(v){
  const fleet = A().career.fleet_max;   // account-level (ASOP list), not per-patch
  const comm = v.commodity_spend||0;
  const K=kpiRow([
    [icon('spend'), auec(v.spend||0), 'aUEC spent', `on items${comm?' & cargo':''} this scope`],
    [icon('shop'), fmt(v.purchases), 'Purchases', `buy transactions`],
    [icon('hall'), (v.shops||[]).length, 'Stores used', `distinct shops`],
    fleetTile(fleet),
  ]);
  // Always rendered, even at zero. Hiding it when you bought no bulk cargo made the
  // page change shape between patches, which reads as missing data rather than "none".
  const commRow = group(2,'cargo','Cargo &amp; commodities','Bulk trade goods, split from ordinary item purchases',
    kpiRow([
      (comm ? [icon('cargo'), auec(comm), 'Spent on cargo', `${fmt(v.commodity_buys||0)} commodity buys`]
            : [icon('cargo'), '0', 'Spent on cargo', `no bulk cargo bought in this scope`, 'na']),
      [icon('shop'), auec(v.item_spend||0), 'Spent on items', `ships, gear, gun mags…`],
    ]));
  $('#content').innerHTML=metaLine(v)+
    group(1,'spend','Spending','Where your aUEC went — every shop purchase your client sent',
      K+`<div class="grid2">`+barsCard('Top items bought','ranked by aUEC spent · qty shown','itemBars')+
      barsCard('What you buy','purchases by item category','catBars')+`</div>`)+commRow+
    noteFold(
      `Spend is real — the log carries the price of every purchase. Balance, earnings and sales aren't logged.`,
      `Purchases are grouped by item category rather than store; the shop codes in the log aren't reliable brand names. Fleet size is your ASOP list read at a terminal, so it includes ships bought with aUEC as well as pledges.`);
  hbars($('#itemBars'), (v.top_items||[]).map(([n,amt,q])=>({label:n, value:amt, disp:auec(amt)+' · ×'+fmt(q), href:wikiURL(n)})));
  hbars($('#catBars'), (v.buy_cats||[]).map(([n,c])=>({label:n, value:c, disp:fmt(c)+' buys'})));
}

// ---- Blueprints (4.7+) ----
// The game announces each blueprint you receive as a HUD notification, and that is the
// ONLY trace it leaves in the client log — there is no ownership list to reconcile
// against. Owned rows are the ones CSR has seen you receive. The Missing side comes from
// the SC-Wiki's extraction of the game data (DATA.bp_catalog, ~1,600 blueprints), so
// "missing" means "never seen received in your logs on this PC", not "confirmed absent".
// 25 to a page for the same reason the crash list pages: 1,600 rows in a scroll box is
// a haystack.
const BP_PAGE=25;
let bpPage=0, bpCat='all', bpQ='', bpMode='owned';
const bpSkin = n => (n||'').replace(/\s*"[^"]*"\s*/g,' ').replace(/\s+/g,' ').trim();   // 'Arclight "Midnight" Pistol' -> wiki page for the gun
function bpOwnedKeys(){
  // the all-time library of this account — Missing is judged against everything you
  // own, even while a patch scope narrows the Owned list to what arrived then
  const s=new Set(); ((A().career||{}).blueprints||[]).forEach(r=>{ if(r[6]) s.add(r[6]); else s.add('n:'+r[0].toLowerCase()); }); return s;
}
function bpMissingRows(){
  const cat=DATA.bp_catalog; if(!cat) return [];
  const own=bpOwnedKeys();
  // same shape as an owned row: [name, cat, kind, first, recv, sess, key, alias] + [default, missions]
  return cat.rows.filter(r=>!own.has(r[0]) && !own.has('n:'+r[1].toLowerCase()))
    .map(r=>[r[1], r[3], r[4], null, 0, 0, r[0], '', r[5], r[6]])
    .sort((a,b)=>a[1]===b[1]? a[0].localeCompare(b[0]) : a[1].localeCompare(b[1]));
}
function bpBase(v){ return bpMode==='missing' ? bpMissingRows() : (v.blueprints||[]); }
function bpRows(v){
  let rows=bpBase(v);
  if(bpCat!=='all') rows=rows.filter(r=>r[1]===bpCat);
  if(bpQ){ const q=bpQ.toLowerCase(); rows=rows.filter(r=>r[0].toLowerCase().includes(q)||(r[2]||'').toLowerCase().includes(q)||r[1].toLowerCase().includes(q)||(r[7]||'').toLowerCase().includes(q)); }
  return rows;
}
function bpListHTML(v){
  const rows=bpRows(v), base=bpBase(v);
  if(!rows.length){
    const msg = !base.length ? (bpMode==='missing' ? 'Nothing missing — or no catalogue to compare against.' : 'No blueprints received in this scope.') : 'Nothing matches that filter.';
    return `<div class="tline"><div class="tev"><span class="tw" style="color:var(--dim)">${msg}</span></div></div>`;
  }
  const pages=Math.ceil(rows.length/BP_PAGE); bpPage=Math.min(bpPage,pages-1);
  const slice=rows.slice(bpPage*BP_PAGE,(bpPage+1)*BP_PAGE);
  const pad=Array.from({length:Math.max(0,BP_PAGE-slice.length)},
    ()=>`<div class="tev ghost"><span class="td">&nbsp;</span><span class="tw">&nbsp;</span></div>`).join('');
  const body=slice.map(r=>{
    const [n,cat,kind,first,recv,sess,key,alias,dflt,missions]=r;
    const nm=link(wikiURL(bpSkin(n)),esc(n),'lk');
    const aka=alias?`<span class="bpk" title="as your UI showed it">shown as ${esc(alias)}</span>`:'';
    if(bpMode==='missing'){
      const how = dflt ? 'known from the start' : (missions ? `${missions} unlocking mission${missions===1?'':'s'}` : 'no mission source listed');
      return `<div class="tev"><span class="td" style="color:var(--dim)">missing</span>`+
        `<span class="tw">${nm}${kind?`<span class="bpk">${esc(kind)}</span>`:''}</span>`+
        `<span class="tv">${esc(cat)} <span class="bph">· ${how}</span></span></div>`;
    }
    return `<div class="tev"><span class="td">${first?ddmm(first):'—'}</span>`+
      `<span class="tw">${nm}${kind?`<span class="bpk">${esc(kind)}</span>`:''}${aka}</span>`+
      `<span class="tv">${esc(cat)}${recv>1?` <span class="bpx" title="received ${recv} times across ${sess} session${sess===1?'':'s'}">×${recv}</span>`:''}</span></div>`;
  }).join('');
  const nav=pages>1?`<div class="pager"><button class="foot-link" data-bpg="${bpPage-1}" ${bpPage?'':'disabled'}>← ${bpMode==='missing'?'Back':'Newer'}</button>`+
    `<span class="pgi">${bpPage*BP_PAGE+1}–${bpPage*BP_PAGE+slice.length} of ${fmt(rows.length)}</span>`+
    `<button class="foot-link" data-bpg="${bpPage+1}" ${bpPage<pages-1?'':'disabled'}>${bpMode==='missing'?'Next':'Older'} →</button></div>`:'';
  return `<div class="tline">${body}${pad}</div>${nav}`;
}
// ---- the collection board ----
// A collector's question is not "how far along is Armour" but "which set am I one
// piece from finishing" — so armour is shown as sets with a square per slot, ship
// weapons as size ladders (one family per row, S1..S6), and components as a type × size
// grid. Sets and families come from the catalogue (DATA.bp_catalog.sets / .fams); the
// page only decides which squares are filled. Singles are not sets and stay in the list.
let bpSetTab='partial', bpSetAll=false, bpFamAll=false;
const BP_SLOTS={'Armor':['Helmet','Core','Arms','Legs','Backpack'], 'Flight suits':['Helmet','Suit']};
function bpBoardHTML(catalog){
  const own=bpOwnedKeys(); const has=(k,n)=>own.has(k)||own.has('n:'+(n||'').toLowerCase());
  // --- armour sets
  const sets=(catalog.sets||[]).map(s=>{ const pcs=s[3].map(p=>({si:p[0],key:p[1],n:p[2],on:has(p[1],p[2])}));
    const o=pcs.filter(p=>p.on).length; return {name:s[0],cat:s[1],w:s[2],pcs,o,t:pcs.length}; });
  const B={partial:sets.filter(s=>s.o&&s.o<s.t), complete:sets.filter(s=>s.o===s.t), untouched:sets.filter(s=>!s.o)};
  B.partial.sort((a,b)=>(a.t-a.o)-(b.t-b.o)||b.o-a.o||a.name.localeCompare(b.name));
  B.complete.sort((a,b)=>b.t-a.t||a.name.localeCompare(b.name));
  B.untouched.sort((a,b)=>b.t-a.t||a.name.localeCompare(b.name));
  if(!B[bpSetTab].length) bpSetTab=B.partial.length?'partial':B.complete.length?'complete':'untouched';
  const tabs=[['partial','Started'],['complete','Complete'],['untouched','Untouched']].map(([k,l])=>
    `<button class="vpill sm ${bpSetTab===k?'on':''}" data-bpst="${k}">${l} <span class="vc">${fmt(B[k].length)}</span></button>`).join('');
  const LIM=24, list=B[bpSetTab], shown=bpSetAll?list:list.slice(0,LIM);
  const setRow=s=>{ const labels=BP_SLOTS[s.cat]||[];
    const sq=labels.map((L,i)=>{ const p=s.pcs.filter(x=>x.si===i);
      if(!p.length) return `<i class="bsq na" title="no ${L.toLowerCase()} in the catalogue for this set"></i>`;
      return p.map(x=>`<i class="bsq ${x.on?'on':''}" title="${esc(x.n)} — ${x.on?'owned':'missing'}">${L[0]}</i>`).join(''); }).join('');
    const togo=s.pcs.filter(p=>!p.on).map(p=>labels[p.si]||'').filter(Boolean);
    const tail= s.o===s.t ? `<span class="bsgo done">complete</span>`
      : s.o ? `<span class="bsgo" title="${esc(togo.join(', '))}">${esc(togo.join(', '))} to go</span>`
      : `<span class="bsgo dim">${s.t} pieces</span>`;
    const sub=[s.w, s.cat==='Flight suits'?'Flight suit':''].filter(Boolean).join(' · ');
    return `<div class="bset"><span class="bsn">${esc(s.name)}${sub?`<span class="bpk">${esc(sub)}</span>`:''}</span>`+
      `<span class="bsl">${sq}</span><span class="bsc">${s.o}<span class="u">/${s.t}</span></span>${tail}</div>`; };
  const more=list.length>LIM?`<button class="foot-link" data-bpsa="1">${bpSetAll?'Show fewer':`Show all ${fmt(list.length)}`}</button>`:'';
  const setsCard=`<div class="card"><h3>Armour sets <span class="u">— ${fmt(B.complete.length)} complete · ${fmt(B.partial.length)} started · ${fmt(B.untouched.length)} untouched</span></h3>`+
    `<div class="bpbar">${tabs}</div><div class="bsets">${shown.map(setRow).join('')||'<div class="empty">Nothing here.</div>'}${more}</div></div>`;
  // --- ship-weapon size ladders
  const fams=(catalog.fams||[]).map(f=>{ const sz=f[2].map(p=>({s:p[0],key:p[1],n:p[2],on:has(p[1],p[2])}));
    return {name:f[0],kind:f[1],sz,o:sz.filter(p=>p.on).length,t:sz.length}; });
  const ladders=fams.filter(f=>f.t>1), singles=fams.filter(f=>f.t===1);
  const maxS=Math.max(1,...ladders.flatMap(f=>f.sz.map(p=>p.s)));
  ladders.sort((a,b)=>(b.o/b.t)-(a.o/a.t)||b.o-a.o||b.t-a.t||a.name.localeCompare(b.name));
  const started=ladders.filter(f=>f.o), rest=ladders.filter(f=>!f.o);
  const lad=f=>{ const dots=Array.from({length:maxS},(_,i)=>i+1).map(s=>{ const p=f.sz.find(x=>x.s===s);
      if(!p) return `<i class="bdot na"></i>`;
      return `<i class="bdot ${p.on?'on':''}" title="${esc(p.n)} — ${p.on?'owned':'missing'}">${s}</i>`; }).join('');
    return `<div class="blad"><span class="bsn">${esc(f.name)}<span class="bpk">${esc(f.kind)}</span></span><span class="bsl">${dots}</span><span class="bsc">${f.o}<span class="u">/${f.t}</span></span></div>`; };
  const restH=rest.length?(bpFamAll?rest.map(lad).join(''):'')+`<button class="foot-link" data-bpfa="1">${bpFamAll?'Hide':'Show'} the ${fmt(rest.length)} you haven\u2019t started</button>`:'';
  const singlesOwned=singles.filter(f=>f.o).length;
  const ladCard=`<div class="card"><h3>Ship weapons by size <span class="u">— one family per row, S1 to S${maxS}</span></h3>`+
    `<div class="bsets">${started.map(lad).join('')||'<div class="empty">No ladder started yet.</div>'}${restH}</div>`+
    (singles.length?`<div class="bph" style="margin-top:10px">+ ${fmt(singles.length)} one-size weapons, ${fmt(singlesOwned)} owned</div>`:'')+`</div>`;
  // --- component grid
  const cell={}, types={}, sizes=new Set();
  catalog.rows.forEach(r=>{ if(r[3]!=='Ship components') return;
    const parts=(r[4]||'').split(' \u00b7 '); const typ=parts[0]||'Other'; const sz=parts.slice(1).find(p=>/^S\d+$/.test(p))||'\u2014';
    sizes.add(sz); const c=cell[typ+'|'+sz]=cell[typ+'|'+sz]||{o:0,t:0}; c.t++; if(has(r[0],r[1])) c.o++; types[typ]=(types[typ]||0)+1; });
  const cols=[...sizes].sort((a,b)=>a==='\u2014'?1:b==='\u2014'?-1:+a.slice(1)-+b.slice(1));
  const trows=Object.entries(types).sort((a,b)=>b[1]-a[1]).map(([typ])=>`<tr><th>${esc(typ)}</th>`+cols.map(s=>{ const c=cell[typ+'|'+s];
    if(!c) return `<td class="na"></td>`;
    return `<td style="--f:${(c.o/c.t).toFixed(2)}" class="${c.o===c.t?'full':''}" title="${esc(typ)} ${s}: ${c.o} of ${c.t} owned"><b>${c.o}</b><span>/${c.t}</span></td>`; }).join('')+'</tr>').join('');
  const gridCard=`<div class="card"><h3>Ship components <span class="u">— owned / in the catalogue, by type and size</span></h3>`+
    `<div class="bgrid-wrap"><table class="bgrid"><thead><tr><th></th>${cols.map(s=>`<th>${s}</th>`).join('')}</tr></thead><tbody>${trows}</tbody></table></div></div>`;
  return setsCard+`<div class="grid2">${ladCard}${gridCard}</div>`;
}
// owned / total per category, on one shared scale so the bars are comparable
function bpProgressHTML(ownedCats, totalCats){
  const cats=[...new Set([...totalCats.map(c=>c[0]), ...ownedCats.map(c=>c[0])])];
  const own=Object.fromEntries(ownedCats), tot=Object.fromEntries(totalCats);
  const rows=cats.map(c=>({c, o:own[c]||0, t:Math.max(tot[c]||0, own[c]||0)})).sort((a,b)=>b.t-a.t);
  if(!rows.length) return '<div class="empty">None recorded here.</div>';
  const max=Math.max(1,...rows.map(r=>r.t));
  return rows.map((r,i)=>`<div class="bar-row"><div class="bar-head"><div class="nm"><span class="rk">${i+1}</span>${esc(r.c)}</div>`+
    `<div class="vv">${fmt(r.o)} <span class="u">/ ${fmt(r.t)}</span></div></div>`+
    `<div class="bar-track" style="position:relative"><div class="bar-fill" style="width:${(100*r.t/max).toFixed(1)}%;opacity:.22"></div>`+
    `<div class="bar-fill" style="width:${(100*r.o/max).toFixed(1)}%;position:absolute;left:0;top:0"></div></div></div>`).join('');
}
function secBlueprints(v){
  const car=A().career||{};
  const catalog=DATA.bp_catalog||null;
  if(!catalog) bpMode='owned';
  const owned=v.blueprints||[];
  const missing=catalog?bpMissingRows():[];
  const base=bpBase(v);
  // category pills follow the side you're looking at
  const catCount={}; base.forEach(r=>{ catCount[r[1]]=(catCount[r[1]]||0)+1; });
  const cats=Object.entries(catCount).sort((a,b)=>b[1]-a[1]);
  if(bpCat!=='all' && !cats.some(([c])=>c===bpCat)) bpCat='all';
  const total=catalog?catalog.n:0, ownedAll=car.bp_unique||0;
  const pct=total?Math.round(100*ownedAll/total):0;
  const K=kpiRow([
    catalog ? [icon('blueprints'), `${fmt(ownedAll)} <span class="u">/ ${fmt(total)}</span>`, 'Blueprints owned', `${pct}% of the crafting catalogue · ${esc(String(catalog.version||'').split('-')[0])}`]
            : [icon('blueprints'), fmt(ownedAll), 'Blueprints owned', 'distinct · all-time on this account'],
    current==='all' ? [icon('import'), fmt(v.bp_receipts||0), 'Times received', `${fmt(v.bp_repeats||0)} received more than once`]
                    : [icon('import'), fmt(v.bp_unique||0), 'Received in '+esc(pLabel(current)), `${fmt(v.bp_receipts||0)} notifications`],
    catalog ? [icon('overview'), fmt(missing.length), 'Still missing', 'never seen received in your logs']
            : [icon('overview'), fmt(cats.length), 'Categories', cats.length?esc(cats[0][0])+' is the largest':'—'],
    v.bp_since ? [icon('day'), ddmm(v.bp_since), 'First blueprint', 'earliest receipt in this scope']
               : [icon('day'), 'N/A', 'First blueprint', 'not logged before patch 4.7', 'na'],
  ]);
  const modes = catalog ? `<button class="vpill sm ${bpMode==='owned'?'on':''}" data-bpm="owned">Owned <span class="vc">${fmt(owned.length)}</span></button>`+
    `<button class="vpill sm ${bpMode==='missing'?'on':''}" data-bpm="missing">Missing <span class="vc">${fmt(missing.length)}</span></button><span class="bpsep"></span>` : '';
  const pills=`<button class="vpill sm ${bpCat==='all'?'on':''}" data-bpc="all">All <span class="vc">${fmt(base.length)}</span></button>`+
    cats.map(([c,n])=>`<button class="vpill sm ${bpCat===c?'on':''}" data-bpc="${esc(c)}">${esc(c)} <span class="vc">${fmt(n)}</span></button>`).join('');
  const bar=`<div class="bpbar">${modes}${pills}<input class="bpq" id="bpQ" type="search" placeholder="Search blueprints…" value="${esc(bpQ)}" spellcheck="false" autocomplete="off"></div>`;
  const listTitle = bpMode==='missing' ? `Not yet in your library <span class="u">— every catalogue blueprint you haven't been seen to receive</span>`
    : current==='all' ? `Your library <span class="u">— every blueprint you've received, newest first</span>`
                      : `Received in ${esc(pLabel(current))} <span class="u">— newest first</span>`;
  $('#content').innerHTML=metaLine(v)+
    group(1,'blueprints','Blueprint library','Everything the game has told you it handed over — and what it hasn\u2019t',
      K+(catalog?`<div id="bpBoard">${bpBoardHTML(catalog)}</div>`:''))+
    group(2,'overview','Every blueprint','The whole list, searchable — what you have, or what you\u2019re still missing',
      `<div class="card"><h3>${listTitle}</h3>${bar}<div id="bpList">${bpListHTML(v)}</div></div>`)+
    group(3,'activity','Unlock history','When the blueprints arrived, and how far along each category is',
      `<div class="grid2">`+cardHTML('Received per month','notifications, not distinct blueprints','bpMonths')+
      cardHTML(catalog?'Progress by category':'By category', catalog?'owned / in the catalogue':'distinct blueprints in this scope','bpCats')+`</div>`)+
    noteFold(
      `Blueprints the game announced to you, counted once each. The log has no ownership list, so this is a minimum.`,
      `Only the Received Blueprint notice is logged; the library itself sits on CIG\u2019s servers. Blueprints from before 4.7, or from another PC whose logs weren\u2019t imported, won\u2019t appear. Language packs (StarStrings, ScCompLangPack) rename items in the notice; CSR matches them back to the real item and keeps your wording as a search alias. <span class="bpx">\u00d72</span> marks a duplicate drop. PTU and Tech Preview run a copy of your account and get their own libraries. Armour sets are read off the game\u2019s item ids (pieces of one set share a variant number), ship-weapon families off the class with the size removed; single pieces aren\u2019t sets and only show in the list.${catalog?` Missing is measured against the SC-Wiki\u2019s extraction of the game data (${fmt(catalog.n)} blueprints, ${esc(String(catalog.version||'').split('-')[0])}), which includes blueprints you can\u2019t get yet — a wish-list, not a to-do list.`:''}`);
  const draw=()=>{ const el=$('#bpList'); if(!el) return; el.innerHTML=bpListHTML(v); wire(); };
  const wire=()=>document.querySelectorAll('#content [data-bpg]').forEach(b=>b.onclick=()=>{
    bpPage=+b.dataset.bpg; draw();
    const el=$('#bpList'); if(el) scrollToY(el.getBoundingClientRect().top+window.pageYOffset-160);
  });
  // pills re-render the whole section (cheap); the search box only redraws the list, so
  // typing never loses focus
  document.querySelectorAll('#content [data-bpc]').forEach(b=>b.onclick=()=>{ bpCat=b.dataset.bpc; bpPage=0; secBlueprints(v); });
  document.querySelectorAll('#content [data-bpm]').forEach(b=>b.onclick=()=>{ bpMode=b.dataset.bpm; bpCat='all'; bpPage=0; secBlueprints(v); });
  const q=$('#bpQ'); if(q) q.oninput=()=>{ bpQ=q.value; bpPage=0; draw(); };
  wire();
  // the board redraws on its own; nothing else on the page depends on its tabs
  const board=()=>{ const el=$('#bpBoard'); if(!el||!catalog) return; el.innerHTML=bpBoardHTML(catalog); wireBoard(); };
  const wireBoard=()=>{
    document.querySelectorAll('#bpBoard [data-bpst]').forEach(b=>b.onclick=()=>{ bpSetTab=b.dataset.bpst; bpSetAll=false; board(); });
    document.querySelectorAll('#bpBoard [data-bpsa]').forEach(b=>b.onclick=()=>{ bpSetAll=!bpSetAll; board(); });
    document.querySelectorAll('#bpBoard [data-bpfa]').forEach(b=>b.onclick=()=>{ bpFamAll=!bpFamAll; board(); });
  };
  wireBoard();
  const months=v.bp_months||[];
  if(months.length) vbars($('#bpMonths'), months.map(([m,n])=>({label:MON[+m.slice(5,7)-1]+' '+m.slice(2,4), full:m, value:n})), {w:560,h:200,rot:months.length>9});
  else $('#bpMonths').innerHTML='<div style="color:var(--dim);font-size:12px;padding:8px 2px">Nothing in this scope</div>';
  if(catalog){
    const totals={}; catalog.rows.forEach(r=>{ totals[r[3]]=(totals[r[3]]||0)+1; });
    const ownedCats={}; ((car.blueprints)||[]).forEach(r=>{ ownedCats[r[1]]=(ownedCats[r[1]]||0)+1; });
    $('#bpCats').innerHTML='<div class="bars">'+bpProgressHTML(Object.entries(ownedCats), Object.entries(totals))+'</div>';
  } else {
    $('#bpCats').innerHTML='<div class="bars" id="bpCatsBars"></div>';
    hbars($('#bpCatsBars'), (v.bp_cats||[]).map(([c,n])=>({label:c, value:n, disp:fmt(n)})));
  }
}

function secMissions(v){
  const m=v.missions;
  const K=kpiRow([
    [icon('missions'), fmt(m.total), 'Missions ended', `all outcomes`],
    [icon('yes'), fmt(m.complete), 'Completed', `${m.rate}% rate`],
    [icon('no'), fmt(m.fail), 'Failed', ``],
    [icon('quit'), fmt(m.abandon), 'Abandoned', ``],
  ]);
  $('#content').innerHTML=metaLine(v)+K+
    cardHTML('Outcomes','completed / failed / abandoned','missionChart')+
    `<div style="margin-top:16px">`+barsCard('Mission types','by contract category','typeBars')+`</div>`+
    ((v.contracts_named||[]).length ? `<div style="margin-top:16px">`+
      barsCard('Contracts by name','accepted · completed — as the game titled them (4.5+)','contractBars')+`</div>`+
      noteFold(`Contract names come from the HUD notices (4.5 onward), language-pack tags stripped. Accepted but not completed covers failed, abandoned, lost on log-off, or still open.`) : '');
  donut($('#missionChart'), [{label:'Completed',value:m.complete,color:'#4ade80'},{label:'Failed',value:m.fail,color:'#ff5468'},{label:'Abandoned',value:m.abandon,color:'#f4a92a'}], {big:(m.rate||0)+'%', small:m.total+' total'});
  hbars($('#typeBars'), toItems(v.mission_types,'#4ade80'));
  const cb=$('#contractBars'); if(cb) hbars(cb, (v.contracts_named||[]).map(([n,a,d,f])=>({label:n, full:n, value:a, disp:`${fmt(a)} <span class="vv-u">acc</span> · ${fmt(d)} done${f?` · ${fmt(f)} failed`:''}`})));
}


function secActivity(v){
  const K=kpiRow([
    [icon('calendar'), v.active_days, 'Active days', `days you logged in`],
    [icon('damage'), v.streak, 'Longest streak', `consecutive days`],
    [icon('time'), v.avg_min+'m', 'Avg session', `per play session`],
    [icon('night'), v.longest_h+'h', 'Longest session', `single sitting`],
  ]);
  $('#content').innerHTML=metaLine(v)+K+
    cardHTML('Playtime by month','hours per calendar month','monthChart')+
    `<div style="margin-top:16px">`+cardHTML('When you play','sessions started · weekday × hour','heatChart')+`</div>`+
    `<div class="grid3" style="margin-top:16px">`+
    cardHTML('By hour','play start time','hourChart')+
    cardHTML('By weekday','','dowChart')+
    barsCard('Session length','how long you play','distBars')+`</div>`;
  const ms=Object.keys(v.months).sort();
  const mitems=ms.map(m=>{const [y,mm]=m.split('-');return {label:MON[+mm-1]+" '"+y.slice(2), value:+(v.months[m]/3600).toFixed(1), disp:(v.months[m]/3600).toFixed(0)+' h', full:m, color:'#3fd0e6'};});
  if(mitems.length) vbars($('#monthChart'), mitems, {h:230, w:Math.max(560,mitems.length*54), rot:true, fmtY:x=>x+'h'});
  else $('#monthChart').innerHTML='<div class="empty">No month data.</div>';
  heatmap($('#heatChart'), v.weekhour);
  vbars($('#hourChart'), v.by_hour.map((c,i)=>({label:(i%3===0?String(i):''), value:c, full:i+':00', disp:c+' sessions', color:'#b98bff'})), {h:210, maxbw:16});
  vbars($('#dowChart'), v.by_dow.map((c,i)=>({label:DOW[i], value:c, full:DOW[i], disp:c+' sessions', color:'#f4a92a'})), {h:210});
  hbars($('#distBars'), (v.session_dist||[]).map(([n,c])=>({label:n, value:c, disp:fmt(c)})));
}

// ---- System & Stability ----
// Windows reports memory MINUS what the hardware reserves, so a 64 GB machine logs
// 63122 MB (61.6 GiB) and a 16 GB card logs 16045 MB. Rounding up to the nearest real
// capacity is what makes it match the sticker: step 4 for system RAM (which is always
// short by a few percent), step 1 for video memory.
const gbz = (mb, step) => {
  if(!mb) return null;
  const g=mb/1024; if(g<1) return mb+' MB';
  const s=step||1;
  return Math.ceil(g/s)*s+' GB';
};
function specRow(k, val, sub){
  return `<div class="spec${val?'':' na'}"><span class="sk">${k}</span>`+
    `<span class="sv">${esc(val||'Not recorded')}</span>`+
    (sub&&val?`<span class="ss">${esc(sub)}</span>`:'')+`</div>`;
}
// Per-patch series for a metric, in the account's own patch order. Used for both the
// stability and benchmark trends so they line up with the Overview playtime chart.
function patchSeries(pick){
  const P=A().patches||{};
  return (A().order||[]).map(k=>({k, p:P[k]})).filter(x=>x.p).map(x=>{
    const val=pick(x.p);
    return val===null||val===undefined ? null : {key:x.k, label:pShort(x.k), full:pLabel(x.k), ...val};
  }).filter(Boolean);
}
// ---- configuration health check ----
// Strictly diagnosis: each entry is something objectively misconfigured or below a
// published requirement. No entry predicts a frame-rate gain, because CSR has no
// frame-rate data to verify one against — see the note rendered under the list.
function healthChecks(r, st){
  const out=[];
  const gb = mb => Math.round((mb||0)/1024);
  if(r.gpu_better){
    out.push(['bad','no',`Star Citizen is not using your fastest graphics card`,
      `It rendered on <b>${esc(r.gpu)}</b> while <b>${esc(r.gpu_better)}</b> `+
      `(${gb(r.gpu_better_vram)} GB) sat idle. On laptops this is usually fixed in `+
      `Windows Graphics settings or your GPU control panel by forcing Star Citizen onto the discrete card.`]);
  }
  if(r.ram_mb){
    const g=Math.ceil(r.ram_mb/1024/4)*4;
    if(g<16) out.push(['bad','warning',`${g} GB system memory is below Star Citizen's minimum`,
      `The game asks for 16 GB and behaves best with 32 GB. Below 16 GB, stutter and long loads are expected rather than unusual.`]);
    else if(g<32) out.push(['warn','memory',`${g} GB system memory meets the minimum, not the recommendation`,
      `Star Citizen's stated minimum is 16 GB; 32 GB is what it recommends. This is the most common upgrade for the money.`]);
  }
  // Only meaningful when the game is on the right card — otherwise this reports the
  // idle integrated chip's memory, which is both wrong and beside the point.
  if(!r.gpu_better && r.vram_mb && r.vram_mb/1024 < 7.5)
    out.push(['warn','display',
      `${r.vram_mb<1024?r.vram_mb+' MB':gb(r.vram_mb)+' GB'} of video memory is on the low side`,
      `Star Citizen's own guidance is 8 GB or more, and texture settings are what usually have to give below that.`]);
  if(st){
    if(st.Upscaling==='0' && /available/i.test(r.dlss||''))
      out.push(['warn','pulse',`Upscaling is off, but your GPU reports DLSS as available`,
        `Your driver advertises DLSS and Star Citizen currently has upscaling disabled. Whether to use it is your call — CSR can't measure the difference.`]);
    if(st.AutoDetect==='1')
      out.push(['ok','yes',`Graphics settings were chosen automatically`,
        `<code>AutoDetect</code> is on, so Star Citizen picked these from its own launch benchmark rather than you setting them.`]);
  }
  if(!out.length) out.push(['ok','yes',`Nothing obviously misconfigured`,
    `Your hardware clears Star Citizen's stated requirements and the game is using the right graphics card.`]);
  return out;
}
function healthHTML(r, st){
  return healthChecks(r,st).map(([lvl,ic,title,detail])=>
    `<div class="chk ${lvl}"><span class="ci">${icon(ic)}</span>`+
    `<div><div class="ct2">${title}</div><div class="cd">${detail}</div></div></div>`).join('');
}
// Human-readable labels for the settings we can name with confidence. The SysSpec_*
// scale is shown as a bare number on purpose — see _SETTING_KEYS in the Python side.
// Labels as the in-game Graphics menu writes them, so what CSR shows matches what
// you see when you go looking for the setting.
const _SETLBL={AutoDetect:'Auto-detect', MotionBlur:'Motion blur', Upscaling:'Upscaling',
  UpscalingTechnique:'Upscaling technique', UpscalingModel:'Upscaling model',
  SysSpec:'Overall quality preset', SysSpec_TextureQuality:'Textures quality',
  SysSpec_TextureDetail:'Detail textures', SysSpec_TextureFiltering:'Texture filtering',
  SysSpec_TextureGround:'Ground textures', SysSpec_ShadowMaps:'Shadow maps',
  SysSpec_ShadowScreenSpace:'Screen space shadows', SysSpec_ObjectDetail:'Object detail',
  SysSpec_ObjectViewDistance:'Object view distance',
  SysSpec_PlanetVolumetricClouds:'Planet volumetric clouds quality',
  SysSpec_GasCloud:'Gas clouds',
  SysSpec_WaterSim:'Water simulation', SysSpec_WaterCaustics:'Water caustics',
  SysSpec_Fog:'Fog', SysSpec_PostProcessing:'Post effects',
  SysSpec_Shading:'Shader quality', SysSpec_VideoComms:'Video comms'};
// The stored number is a position on ONE global tier scale, but most settings only
// offer PART of it in the menu. Ground textures is the setting that proves it: the
// menu offers just Low and High, and stores High as 3 — so the number is a global
// tier, not an index into that setting's own two-item list.
const SPEC_TIERS=['Low','Medium','High','Very High','Ultra'];
const specTier=v=>SPEC_TIERS[parseInt(v,10)-1]||null;
// Values each setting actually offers, and any slot the menu renames, read off the
// in-game Graphics tab. Anything not listed here offers the full 1–5.
const _SETSCALE={
  SysSpec_TextureDetail:{opts:[1,2,3]},
  SysSpec_TextureFiltering:{opts:[1,2,3]},
  SysSpec_TextureGround:{opts:[1,3]},
  SysSpec_ShadowScreenSpace:{opts:[1,2,3,4], names:{1:'Off'}},
  SysSpec_PlanetVolumetricClouds:{opts:[1,2,3,4,5], names:{5:'Photo Mode (Slow)'}},
  SysSpec_GasCloud:{opts:[1,2,3,4]},
  SysSpec_WaterCaustics:{opts:[1,2,3,4]},
  SysSpec_WaterSim:{opts:[1,2,3,4]},
  SysSpec_Shading:{opts:[1,2,3,4]},
  SysSpec_PostProcessing:{opts:[1,2,3,4]},
  SysSpec_VideoComms:{opts:[1,2,3,4]},
};
const _FULLSCALE=[1,2,3,4,5];
const _setOpts=k=>(_SETSCALE[k]||{}).opts||_FULLSCALE;
function setTier(k,v){
  const n=parseInt(v,10); if(isNaN(n)) return null;
  const nm=(_SETSCALE[k]||{}).names||{};
  return nm[n] || (n<=0 ? 'Off' : specTier(n));
}
// The upscaling settings are enums, not quality tiers, and only the values actually
// observed against the menu are named — anything else falls back to the raw number
// rather than guessing at an ordering that hasn't been checked.
const _ENUM={
  Upscaling:{'2':'Quality (66%)'},
  UpscalingTechnique:{'2':'NVidia DLSS/DLAA'},
  UpscalingModel:{'1':'Transformer'},
};
const _ONOFF=v=>v==='1'?'On':(v==='0'?'Off':v);
const _YESNO=v=>v==='1'?'Yes':(v==='0'?'No':v);   // the menu words motion blur Yes/No
const _setName=k=>_SETLBL[k]||k.replace(/^SysSpec_?/,'').replace(/([a-z])([A-Z])/g,'$1 $2');
// The meter counts the options this setting actually has, not the global 1–5 — so a
// setting sitting at its own ceiling reads as full instead of looking short of Ultra.
function levelHTML(k,val){
  const n=parseInt(val,10); if(isNaN(n)||n<1) return '';
  const opts=_setOpts(k);
  const rank=opts.indexOf(n)+1 || opts.filter(o=>o<=n).length;
  if(rank<1) return '';
  return `<div class="lvl${rank>=opts.length?' max':''}">`+
    opts.map((_,i)=>`<i class="${i<rank?'on':''}"></i>`).join('')+`</div>`;
}
function settingsHTML(st){
  if(!st) return '';
  const item=(k,v,lvl)=>`<div class="sitem"><div class="sk2"><span>${esc(_setName(k))}</span>`+
    `<b>${esc(String(v))}</b></div>${lvl||''}</div>`;
  const toggles=['AutoDetect','MotionBlur'].filter(k=>st[k]!==undefined)
    .map(k=>item(k, k==='MotionBlur'?_YESNO(st[k]):_ONOFF(st[k]))).join('');
  const ups=['Upscaling','UpscalingTechnique','UpscalingModel'].filter(k=>st[k]!==undefined)
    .map(k=>item(k,(_ENUM[k]||{})[st[k]]||st[k])).join('');
  // No "Custom (…)" label on the master: sub-settings sitting below the preset number
  // are usually just at their own ceiling (Ultra puts ground textures at 3, its max),
  // and the menu still calls that a clean Ultra. Report the stored preset, nothing more.
  const specs=Object.keys(st).filter(k=>k.indexOf('SysSpec')===0)
    .sort((a,b)=>a==='SysSpec'?-1:b==='SysSpec'?1:_setName(a).localeCompare(_setName(b)))
    .map(k=>item(k, setTier(k,st[k])||st[k], levelHTML(k,st[k]))).join('');
  return `<div class="card"><h3>Current graphics settings <span class="u">— read from your profile</span></h3>`+
    `<div class="setg2">`+toggles+ups+specs+`</div></div>`;
}
// ---- settings guidance ----
// This is guidance, not measurement. Star Citizen DOES log per-session frame rate (see
// the Performance group), but it is a whole-session average over whatever you happened
// to be doing — so comparing before/after a settings change only means something across
// many comparable sessions, which CSR does not yet have. Until then the advice below
// stays delegated to CIG's own AutoDetect rather than inventing a rule.
// Only two values are ever written: the master preset and the AutoDetect flag. The 20
// per-feature SysSpec_* values are left alone — each has its own ceiling (this machine
// sits at 3, 4 and 5 simultaneously with AutoDetect on), so setting one by hand means
// deciding what all the others should become, which is exactly the advice CSR can't check.
// NOTE — there is deliberately no "your hardware should be on preset N" function here.
// An earlier draft had one (a VRAM/RAM/resolution rule of thumb) and it was tested
// against the only ground truth available: Star Citizen's own AutoDetect had chosen
// preset 5 for this machine, and the heuristic said 3. It also put a 4090 at 4K on 3.
// A rule that contradicts CIG's own tuning on the one case we can check is not worth
// shipping, and a whole-session FPS average is too coarse to settle it. So the advice
// below delegates the hard question to AutoDetect — which is calibrated by CIG against
// the same launch benchmark CSR reads — and otherwise offers only relative nudges the
// player can evaluate with their own eyes.
function settingsAdvice(v){
  const st=(C()||{}).settings;
  if(!st) return '';
  const cur=st.SysSpec!==undefined?parseInt(st.SysSpec,10):null;
  const auto=st.AutoDetect==='1';
  const canWrite=!!(SERVED&&C().settings);
  if(!canWrite) return `<div class="card"><div class="empty">Open CSR as an app to change settings.</div></div>`;
  const act=(k,val,label)=>`<button class="foot-link setact" data-k="${k}" data-v="${val}">${esc(label)}</button>`;
  const cards=[];
  cards.push(`<div class="act"><div class="at2">Auto-detect ${auto?'is on':'is off'}</div>`+
    `<div class="ad2">Star Citizen re-picks every setting from the benchmark it runs at launch.</div>`+
    `<div class="row">${act('AutoDetect',1,auto?'Re-run auto-detect':'Turn auto-detect on')}</div></div>`);
  if(cur!==null){
    const btns=[cur>1?act('SysSpec',cur-1,'↓ '+specTier(cur-1)):'',
                cur<5?act('SysSpec',cur+1,'↑ '+specTier(cur+1)):''].join('');
    cards.push(`<div class="act"><div class="at2">Overall quality — ${esc(specTier(cur)||cur)}</div>`+
      `<div class="ad2">CSR states no opinion on the right value. Move one step and judge by eye.</div>`+
      `<div class="row">${btns}</div></div>`);
  }
  const rp=[];
  if(st._csr_prev) rp.push(`<button class="foot-link setrevert" data-which="prev">Undo last change</button>`);
  if(st._csr_original) rp.push(`<button class="foot-link setrevert" data-which="original">Restore original</button>`);
  if(rp.length) cards.push(`<div class="act"><div class="at2">Restore</div>`+
    `<div class="ad2">Undo point ${esc(st._csr_prev||'—')}`+
    `${st._csr_original?` · original from ${esc(st._csr_original)}`:''}.</div>`+
    `<div class="row">${rp.join('')}</div></div>`);
  return `<div class="card"><h3>Change settings <span class="beta">BETA</span> `+
    `<span class="u">— use at your own risk</span></h3>`+
    `<div class="bsub" style="margin-bottom:10px">Only the overall preset and the auto-detect flag are `+
    `ever written. <b>Close Star Citizen first.</b> A copy is saved before every change.</div>`+
    `<div class="acts">${cards.join('')}</div>`+
    `<div id="setMsg" class="bsub" style="margin-top:9px"></div></div>`;
}
// ---- crash analysis ----
// Mirrors CRASH_KIND on the Python side: the game's own exception names, translated,
// with a flag for whether the player can act on it.
const CRASH_KIND={
  STATUS_CRYENGINE_GPU_CRASH:['Graphics driver crash',true],
  STATUS_CRYENGINE_OUT_OF_SYSMEM:['Ran out of system memory',true],
  STATUS_CRYENGINE_WATCH_DOG:['Hang / watchdog timeout',true],
  EXCEPTION_ACCESS_VIOLATION:['Game bug',false],
  EXCEPTION_BREAKPOINT:['Game assertion failed',false],
  STATUS_CRYENGINE_FATAL_ERROR:['Fatal engine error',false],
};
const crashLabel=e=>(CRASH_KIND[e]||[e||'Unknown crash',false])[0];
// "19-Jul-2026 22:16" — the wall clock the session was played on, already converted
// on the Python side. Older archived crashes carry only a date; they fall back to it.
function whenStamp(at, date){
  if(!at) return ddmm(date);
  const p=String(at).split('T');
  return ddmm(p[0])+(p[1]?' '+p[1].slice(0,5):'');
}
function lastCrashHTML(v){
  const c=v.last_crash;
  if(!c) return `<div class="card"><div class="empty">No crash has been recorded in this scope. `+
    `Star Citizen writes a report whenever it crashes, so this stays empty until it does.</div></div>`;
  const [label,actionable]=CRASH_KIND[c.exception]||[c.exception||'Unknown crash',false];
  const gb=mb=>mb?(mb/1024).toFixed(1)+' GB':null;
  const chips=[];
  if(c.ws_mb) chips.push(['MEMORY IN USE', gb(c.ws_mb)]);
  if(c.peak_commit_mb) chips.push(['PEAK COMMIT', gb(c.peak_commit_mb)]);
  if(c.free_mb!=null) chips.push(['FREE AT CRASH', gb(c.free_mb)]);
  if(c.seen>1) chips.push(['TIMES HIT', String(c.seen)]);
  return `<div class="card"><h3>Your last crash <span class="u">— ${esc(whenStamp(c.at, c.date))}</span></h3>`+
    `<div class="chk ${actionable?'warn':'bad'}" style="margin-top:2px">`+
    `<span class="ci">${icon(actionable?'warning':'bug')}</span><div style="flex:1">`+
    `<div class="ct2">${esc(label)}</div>`+
    `<div class="cd">${esc(c.gpu_msg || (actionable
        ? 'Star Citizen reported a fault you may be able to do something about.'
        : 'This was a fault inside Star Citizen. Nothing on your machine caused it, and no setting would have prevented it.'))}`+
    (c.seen>1?` <b>You have hit this exact crash ${c.seen} times.</b>`:'')+`</div>`+
    `<div class="mchips">`+chips.map(([k,val])=>
      `<span class="mchip"><span class="mk">${k}</span><span class="mv">${esc(val)}</span></span>`).join('')+
    `</div>`+
    `<div class="amore" style="margin-top:8px;font-family:var(--font-mono);font-size:10.5px;color:var(--dim)">`+
    `${esc(c.exception||'')}${c.code?' '+esc(c.code):''}${c.digest?' · digest '+esc(c.digest.slice(0,12)):''}</div>`+
    `<div class="row" style="margin-top:11px">`+
    `<button class="foot-link" id="bugCopy">${icon('export')} Copy a bug report</button>`+
    `<a class="foot-link" href="${IC_URL}" target="_blank" rel="noopener">`+
    `${icon('satellite')} Open the Issue Council</a></div>`+
    `<div class="bsub" id="bugMsg" style="margin-top:7px">`+
    (CRASH_SEARCH[c.exception]
      ? `Nothing is sent anywhere. Search the Issue Council for <b>${esc(CRASH_SEARCH[c.exception])}</b> `+
        `before filing — someone may already have reported it.`
      : `Nothing is sent anywhere. <b>${esc(c.exception||'This exception')}</b> is too generic to `+
        `search usefully — it only means the game touched memory it shouldn't have, and no two `+
        `are the same bug. The report below is the part worth filing.`)+
    `</div></div></div></div>`;
}
// ---- crash history: every crash, paged ----
// The aggregate above says WHAT keeps breaking; this says WHEN. 20 to a page rather
// than a scroll box, because a career's worth of crashes in a 300px scroller is a
// haystack — with pages you can at least see how many there are.
const CRASH_PAGE=20;
let crashPage=0;
function crashListHTML(v){
  const rows=v.crash_list||[];
  if(!rows.length) return '';
  const pages=Math.ceil(rows.length/CRASH_PAGE);
  crashPage=Math.min(crashPage, pages-1);
  const slice=rows.slice(crashPage*CRASH_PAGE, (crashPage+1)*CRASH_PAGE);
  const gb=mb=>mb?(mb/1024).toFixed(1)+' GB':'';
  // The last page is nearly always short. Padding it out with empty rows of the same
  // height keeps the card exactly the same size on every page, so paging doesn't make
  // the whole layout jump up under the cursor.
  const pad=Array.from({length:Math.max(0, CRASH_PAGE-slice.length)},
    // the nbsp has to be in .tw as well: it is the tallest span in a real row, so an
    // empty one makes the filler 4px short and the card still shrinks by ~50px
    ()=>`<div class="tev ghost"><span class="td">&nbsp;</span><span class="tw">&nbsp;</span></div>`).join('');
  const body=slice.map(c=>{
    const [label,act]=CRASH_KIND[c.exception]||[c.exception||'Unknown crash',false];
    const mem=[c.ws_mb?gb(c.ws_mb)+' in use':'', c.free_mb!=null?gb(c.free_mb)+' free':'']
      .filter(Boolean).join(' · ');
    return `<div class="tev"><span class="td" style="color:${act?'var(--amber)':'#8b9bb4'}">`+
      `${esc(whenStamp(c.at, c.date))}</span>`+
      `<span class="tw">${esc(label)}${c.digest?` <span class="u">${esc(c.digest.slice(0,8))}</span>`:''}</span>`+
      `<span class="tv">${esc(mem)}</span></div>`;
  }).join('');
  const nav=pages>1 ? `<div class="pager">`+
    `<button class="foot-link" data-cpg="${crashPage-1}" ${crashPage?'':'disabled'}>← Newer</button>`+
    `<span class="pgi">${crashPage*CRASH_PAGE+1}–${crashPage*CRASH_PAGE+slice.length} of ${fmt(rows.length)}</span>`+
    `<button class="foot-link" data-cpg="${crashPage+1}" ${crashPage<pages-1?'':'disabled'}>Older →</button>`+
    `</div>` : '';
  return `<div class="card" style="margin-top:16px"><h3>Every crash `+
    `<span class="u">— newest first</span></h3>`+
    `<div class="tline" id="crashList">${body}${pad}</div>${nav}</div>`;
}
// ---- taking a crash further ----
// There is no public database keyed on Star Citizen's crash codes: the wiki has one
// error-code page (30000) and none for the rest, and the Issue Council is searched by
// symptom, not by exception or by CIG's crash digest. So CSR does NOT pretend to have
// matched your crash to a known bug — it offers a SEARCH, clearly labelled as one, and
// a ready-made report, which is the thing that actually gets a bug fixed.
// navigator.clipboard is unavailable on file:// pages, which is exactly how a saved
// CSR.html gets opened — so there is always a hidden-textarea fallback.
function copyText(text, done){
  const fallback=()=>{
    const ta=document.createElement('textarea');
    ta.value=text; ta.setAttribute('readonly','');
    ta.style.cssText='position:fixed;top:-1000px;opacity:0';
    document.body.appendChild(ta); ta.select();
    let ok=false; try{ ok=document.execCommand('copy'); }catch(e){}
    ta.remove(); done(ok);
  };
  if(navigator.clipboard&&navigator.clipboard.writeText)
    navigator.clipboard.writeText(text).then(()=>done(true), fallback);
  else fallback();
}
// Terms that describe the SYMPTOM someone would actually have filed under. Where a
// crash class is too generic to search usefully, there is deliberately no entry:
// EXCEPTION_ACCESS_VIOLATION just means "the game touched memory it shouldn't have",
// and the 29 of them in this archive are 29 different bugs. Searching that returns
// other people's unrelated crashes, which is worse than offering nothing.
const CRASH_SEARCH={
  STATUS_CRYENGINE_GPU_CRASH:'GPU crash',
  STATUS_CRYENGINE_OUT_OF_SYSMEM:'out of memory',
  STATUS_CRYENGINE_WATCH_DOG:'freeze watchdog',
  STATUS_CRYENGINE_FATAL_ERROR:'fatal error',
};
// The Issue Council is an Apollo app; its deep-link search parameter is not documented
// and could not be confirmed, so CSR links to the project itself rather than shipping a
// ?search= that may quietly be ignored. The term goes on the clipboard instead.
const IC_URL='https://issue-council.robertsspaceindustries.com/projects/STAR-CITIZEN/issues';
function bugReportText(v, c){
  const r=v.rig||{}, L=[];
  L.push(`Build       ${v.build||'—'}${v.patch_label?` (${v.patch_label})`:''}`);
  L.push(`When        ${whenStamp(c.at, c.date)} (local)`);
  L.push(`Crash       ${(CRASH_KIND[c.exception]||[c.exception])[0]}`);
  L.push(`Exception   ${c.exception||'—'}${c.code?' '+c.code:''}`);
  if(c.digest) L.push(`Digest      ${c.digest}`);
  if(c.gpu_msg) L.push(`Game said   ${c.gpu_msg}`);
  if(c.ws_mb) L.push(`Memory      ${(c.ws_mb/1024).toFixed(1)} GB in use`+
    (c.free_mb!=null?`, ${(c.free_mb/1024).toFixed(1)} GB free`:''));
  if(c.seen>1) L.push(`Frequency   hit this exact signature ${c.seen} times`);
  L.push('');
  if(r.cpu) L.push(`CPU         ${r.cpu}${r.cores?` (${r.cores} threads)`:''}`);
  if(r.gpu) L.push(`GPU         ${r.gpu}${r.vram_mb?` · ${Math.round(r.vram_mb/1024)} GB`:''}`);
  if(r.driver) L.push(`Driver      ${r.driver}${r.api?` (${r.api})`:''}`);
  if(r.ram_mb) L.push(`RAM         ${Math.ceil(r.ram_mb/1024/4)*4} GB`);
  if(r.res) L.push(`Display     ${r.res}`);
  if(r.os_build) L.push(`Windows     ${r.os_build}`);
  L.push('');
  if(CRASH_SEARCH[c.exception])
    L.push(`Search the Issue Council for "${CRASH_SEARCH[c.exception]}" before filing this.`);
  L.push(`(collected from my own client log by CSR — no reproduction steps included,`);
  L.push(` add what you were doing when it happened before you submit)`);
  return L.join('\n');
}
function crashCausesHTML(v){
  const rows=v.crash_causes||[];
  if(!rows.length) return '';
  const tot=rows.reduce((a,r)=>a+r[1],0);
  const act=rows.filter(r=>(CRASH_KIND[r[0]]||[0,false])[1]).reduce((a,r)=>a+r[1],0);
  return `<div class="card" style="margin-top:16px"><h3>What has crashed you `+
    `<span class="u">— ${fmt(tot)} report${tot===1?'':'s'} from the game itself</span></h3>`+
    `<div class="tline">`+rows.map(([e,n])=>{
      const [lab,a]=CRASH_KIND[e]||[e,false];
      return `<div class="tev"><span class="td" style="color:${a?'var(--amber)':'#8b9bb4'}">${n}×</span>`+
        `<span class="tw">${esc(lab)} <span class="u">${esc(e)}</span></span>`+
        `<span class="tv">${a?'worth acting on':'game bug'}</span></div>`;
    }).join('')+`</div>`+
    `<div class="bsub" style="margin-top:10px">`+
    (act? `<b>${fmt(act)} of ${fmt(tot)}</b> were faults you can act on — the rest are bugs in Star Citizen itself.`
        : `All of these are bugs in Star Citizen itself. Nothing on your machine caused them.`)+
    `</div></div>`;
}
function fpsHTML(v){
  const f=v.fps;
  if(!f) return '';
  return kpiRow([
    [icon('pulse'), f.avg, 'Average FPS', `median ${f.median} · across ${fmt(f.n)} sessions`],
    [icon('time'), f.ms+' ms', 'Average frame time', `lower is smoother`],
    [icon('warning'), f.hitch+'%', 'Frames over 50 ms', `the stutters you actually feel`],
  ])+callout(icon('satellite'),
    `A <b>whole-session average</b> written when a level unloads, not a live counter. `+
    `Sessions here range ${f.min}–${f.max} FPS.`);
}
// ---- live watcher status + test controls ----
// Only meaningful in the local app: a standalone CSR.html has no server behind it.
function watchHTML(){
  if(!SERVED) return `<div class="card"><div class="empty">Live monitoring runs in the CSR app.</div></div>`;
  return `<div class="card"><div id="watchBody"><div class="bsub">connecting…</div></div></div>`;
}
// The nav dot is the watcher's only presence outside its own tab: green while CSR is
// tailing, amber and faster while Star Citizen is actively writing a log.
function navLive(w){
  const d=$('#navLive'); if(!d) return;
  const on=!!(SERVED && w && w.on);
  d.classList.toggle('on', on);
  d.classList.toggle('hot', !!(on && w.live));
  if(on) d.title = w.live ? 'Session live — checking every '+w.poll+'s'
                          : 'Watching your logs — checking every '+w.poll+'s';
}
function renderWatch(w){
  navLive(w);
  const el=$('#watchBody'); if(!el||!w) return;
  if(!w.on){ el.innerHTML=`<div class="wscan dead"><div class="wscan-top">`+
    `<span class="wdot"></span><span class="wst">Not watching</span>`+
    `<span class="wsub">nothing is being read</span></div><div class="wtrack"></div></div>`; return; }
  const ago=s=>s<1?'now':(s<60?Math.round(s)+'s':(s<3600?Math.round(s/60)+'m':Math.round(s/3600)+'h'));
  // "seen now ago" — ago() returns the word "now", which doesn't take the suffix.
  const seen=s=>s==null?'':(s<1?'checked just now':'checked '+ago(s)+' ago');
  // When THIS log was last written — the one fact that actually differs per channel.
  // Today's logs get a bare clock time; older ones carry the date, so a channel you
  // last touched in May doesn't read as though it were this morning.
  const MONS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const written=mt=>{
    if(!mt) return '';
    const d=new Date(mt*1000), n=new Date(), p=x=>String(x).padStart(2,'0');
    const clock=p(d.getHours())+':'+p(d.getMinutes());
    return d.toDateString()===n.toDateString() ? clock
      : p(d.getDate())+' '+MONS[d.getMonth()]+' '+clock;
  };
  const lights=(w.channels||[]).map(c=>{
    // w.game===false is authoritative: the game is gone, so nothing is live no matter
    // how fresh the log looks. undefined/null means we couldn't ask — fall back to age.
    const live=c.idle!=null&&c.idle<30&&w.game!==false;
    const cls=!c.exists?'off':(live?'live':'idle');
    const state=!c.exists?'no log':(live?'SESSION LIVE':'idle '+ago(c.idle));
    return `<div class="slight ${cls}"><span class="dot"></span>`+
      `<span class="sn">${esc(c.channel)}</span>`+
      `<span class="sm"><b>${state}</b><br>`+
      (c.exists ? `${fmt(c.kb)} KB${c.mtime?' · written '+esc(written(c.mtime)):''}`
                : 'never written on this PC')+`</span></div>`;
  }).join('');
  // Three states, because "the game is running" and "the log is being written" are
  // different questions and only the pair of them tells you where you are.
  const headline = w.live ? 'Session live — reading as you play'
    : w.game===true ? 'Star Citizen is open — waiting for it to write'
    : w.game===false ? 'Star Citizen is closed — watching for the next session'
    : 'Watching your logs';
  const scan=`<div class="wscan ${w.live?'hot':''}">`+
    `<div class="wscan-top"><span class="wdot"></span>`+
    `<span class="wst">${headline}</span>`+
    `<span class="wsub">every ${w.poll}s</span></div>`+
    `<div class="wtrack"></div></div>`;
  el.innerHTML=scan+
    `<div class="slights">${lights||'<div class="bsub">No channels watched.</div>'}</div>`+
    // "checked N ago" lives here, once: every channel is stat'ed in the same pass, so
    // printing it on each card just repeated one number three times.
    `<div class="wbar"><span>Poll <b>${w.poll}s</b></span>`+
    (w.ago!=null?`<span>All channels <b>${esc(seen(w.ago))}</b></span>`:'')+
    `<span>Events raised <b>${fmt(w.raised)}</b></span>`+
    `<span>Watching for <b>${ago(w.uptime)}</b></span></div>`;
}
// ---- inline confirmation ----
// The button arms into its own confirm rather than opening a modal, so destructive
// actions stay two-step without leaving the terminal look.
function confirmBtn(btn, label, run){
  if(btn.dataset.armed==='1') return;
  const orig=btn.innerHTML, cls=btn.className;
  btn.dataset.armed='1'; btn.innerHTML=label; btn.className=cls+' danger';
  const cancel=document.createElement('button');
  cancel.className='foot-link'; cancel.textContent='Cancel';
  btn.after(cancel);
  let done=false;
  const reset=()=>{ if(done) return; done=true;
    btn.innerHTML=orig; btn.className=cls; delete btn.dataset.armed; cancel.remove(); };
  cancel.onclick=e=>{ e.stopPropagation(); reset(); };
  btn.onclick=e=>{ e.stopPropagation(); reset(); run(); };
  setTimeout(()=>{ if(!done) reset(); }, 8000);
}
// A block the player can paste into Discord. This is how a newcomer gets a baseline
// without CSR ever sending anything anywhere — veterans post theirs, and the numbers
// become comparable because the benchmark index is the game's own normalised score.
function shareText(v){
  const r=v.rig||{}, pi=v.perf_index, oc=v.outcomes||{}, rated=v.outcomes_rated||0;
  const L=[];
  L.push(`— CSR rig & stability card —`);
  if(r.cpu) L.push(`CPU      ${r.cpu}${r.cores?` (${r.cores} threads)`:''}`);
  if(r.gpu) L.push(`GPU      ${r.gpu}${r.vram_mb?` · ${Math.round(r.vram_mb/1024)} GB`:''}`);
  if(r.ram_mb) L.push(`RAM      ${Math.ceil(r.ram_mb/1024/4)*4} GB`);
  if(r.res) L.push(`Display  ${r.res}${r.api?` · ${r.api}`:''}`);
  if(r.driver) L.push(`Driver   ${r.driver}`);
  if(pi) L.push(`SC index CPU ${pi.cpu} / GPU ${pi.gpu}  (game's own launch benchmark, ${fmt(pi.n)} launches)`);
  if(rated>=10) L.push(`Sessions ${fmt(rated)} rated · ${(100-v.rough_pct).toFixed(1)}% ended normally `+
    `(${fmt(oc.crash||0)} crash, ${fmt(oc.backend||0)} back-end)`);
  else if(rated) L.push(`Sessions ${fmt(rated)} rated — too few for a rate`);
  L.push(`Scope    ${scopeLabel()}${v.first?` · ${ddmm(v.first)} → ${ddmm(v.last)}`:''}`);
  if(v.fps) L.push(`FPS      ${v.fps.avg} avg · ${v.fps.ms} ms · ${v.fps.hitch}% frames over 50ms (${fmt(v.fps.n)} sessions)`);
  if(v.last_crash) L.push(`Crashes  ${(v.crash_causes||[]).map(([e,n])=>n+'× '+crashLabel(e)).join(', ')}`);
  return L.join('\n');
}
// A new player has no career to chart, and suppressed percentages tell them nothing.
// For thin scopes CSR answers in words instead — which is what they actually came for.
function newcomerVerdict(v, oc, rated, thin){
  if(!thin) return '';
  const bad=(oc.crash||0)+(oc.backend||0), srv=oc.backend||0;
  let s=`<b>You have ${fmt(rated)} completed session${rated===1?'':'s'} so far</b> — `+
    `not enough for a meaningful percentage, so here it is in plain terms. `;
  if(!bad) s+=`<b>Every one of them ended normally.</b> Nothing so far suggests a problem at your end.`;
  else{
    s+=`<b>${fmt(bad)}</b> ended badly`;
    s+= srv===bad ? `, and <b>all of them were Star Citizen's servers going unresponsive</b> — nothing about your PC caused those, and no upgrade would have prevented them.`
       : (srv ? `; <b>${fmt(srv)}</b> of those was the server side, not your machine.` : `.`);
    s+=` A handful of bad sessions is normal in this game — come back once you have a few dozen and the number will start to mean something.`;
  }
  return callout(icon('profile'), s);
}
// ---- back to the main menu ----
// The thing players describe as "it just booted me to the menu for no reason". Star
// Citizen records it as the PLAYER asking to disconnect, which is why it has never
// shown up in any stat. Since the 2026 builds a deliberate exit to menu leaves the SAME
// trace, so this counts both and is labelled that way — see BOOT_DISCO_RE.
function bootsHTML(v){
  const b=v.boots;
  if(!b) return `<div class="card"><div class="empty">No mid-session returns to the main menu in this scope. `+
    `CSR looks for a disconnect that puts you back in the front end while you are still `+
    `playing, followed by more play.</div></div>`;
  const rate=b.per10h!=null ? b.per10h.toFixed(2) : '—';
  const kpis=kpiRow([
    [icon('warning'), fmt(b.n), 'Returns to the menu', 'server drops and exits to menu, together'],
    [icon('time'), rate, 'Per 10 hours played', 'the rate is what to compare'],
    [icon('profile'), b.session_pct+'%', 'Sessions affected', `${fmt(b.sessions)} of your sessions`],
  ]);
  // Every row said "back to menu", which is what the whole card is about. The third
  // column now carries the gap to the drop before it — that is the thing you actually
  // want to see, because these arrive in clusters (five in one evening, 8 minutes apart).
  const recent=(b.recent||[]).length ? `<div class="card" style="margin-top:16px">`+
    `<h3>Most recent <span class="u">— newest first</span></h3><div class="tline">`+
    b.recent.map((t,i)=>{
      const p=String(t).split('T');
      const prev=b.recent[i+1];
      let gap='';
      if(prev){
        const d=(new Date(t)-new Date(prev))/60000;
        gap = d<90 ? `${Math.round(d)} min after the previous one`
            : d<2880 ? `${Math.round(d/60)} h after the previous one` : '';
      }
      return `<div class="tev"><span class="td">${esc(ddmm(p[0]))} ${esc((p[1]||'').slice(0,5))}</span>`+
        `<span class="tw"></span><span class="tv">${esc(gap)}</span></div>`;
    }).join('')+`</div></div>` : '';
  // Full width, not half: vbars renders into a viewBox that scales to the container, so
  // a 900px-wide chart squeezed into a half-width column loses its height too and the
  // bars end up as slivers.
  const trend=(b.trend||[]).length>1
    ? `<div style="margin-top:16px">`+
      barsCard('Returns to the menu per 10 hours played','by month — quiet months shown at zero','bootTrend')+
      `</div>`
    : '';
  return kpis+
    callout(icon('satellite'),
      `The game logs every one of these as “Player requested disconnect”, whether the server dropped you or you chose Exit to menu. `+
      `Since the 2026 builds the log can't tell the two apart, so both are counted. If you never use Exit to menu, these are all drops.`)+
    noteFold(`Why drops and exits can't be separated any more`,
      `Up to the 2025 builds the client wrote its own quit request before a deliberate exit, which is how CSR told them apart. In every 2026 build that line comes about 7 s after the disconnect, for drops and exits alike — checked across 274 events and one session where I knew which was which. 4.10 lets you switch shards from the menu, so some of these will be you.`)+
    trend+recent;
}
// One renderer, two tabs: `STAB` picks which half of System & Stability to draw.
function secStability(v){ return secSystem(v, true); }
function secSystem(v, STAB){
  const r=v.rig||{}, pi=v.perf_index, oc=v.outcomes||{}, rated=v.outcomes_rated||0;
  const rend=v.renderer||{}, rendTotal=Object.values(rend).reduce((a,b)=>a+b,0);
  const rendSub=rendTotal ? Object.entries(rend).sort((a,b)=>b[1]-a[1])
    .map(([k,n])=>`${k} ${Math.round(100*n/rendTotal)}%`).join(' · ') : '';

  // ---- 01 machine ----
  const specs = `<div class="specs">`+
    specRow('Processor', r.cpu, r.cores?`${r.cores} logical cores`:'')+
    specRow('Graphics', r.gpu, gbz(r.vram_mb)?`${gbz(r.vram_mb)} video memory`:'')+
    specRow('System memory', gbz(r.ram_mb,4))+
    specRow('Display mode', r.res)+
    specRow('Renderer', r.api, rendSub)+
    specRow('GPU driver', r.driver, r.api?`as reported by ${r.api}`:'')+
    specRow('Windows build', r.os_build)+
    specRow('DLSS', r.dlss)+
    `</div>`;
  // History lives behind a fold: it's reference material you consult occasionally,
  // not something worth pushing the specs off the screen every visit.
  const fold=(title,count,body)=>`<details class="fold"><summary><b>${title}</b>`+
    `<span class="cnt">${count}</span></summary><div class="foldbody">${body}</div></details>`;
  const changes=(v.rig_changes||[]);
  const changeCard = changes.length
    ? fold('Changes over time', changes.length+(changes.length===1?' change':' changes'),
        `<div class="tline">`+changes.slice().reverse().map(([d,what,snap])=>
          `<div class="tev"><span class="td">${ddmm(d)}</span><span class="tw">${esc(what.join(', '))}</span>`+
          `<span class="tv">${esc([snap.res, gbz(snap.ram_mb,4), snap.os_build].filter(Boolean).join(' · '))}</span></div>`).join('')+
        `</div>`)
    : '';
  const drivers=(v.drivers||[]);
  const driverCard = drivers.length>1
    ? fold('GPU driver history', drivers.length+' versions',
        `<div class="tline">`+drivers.slice().reverse().map(([api,dv,f,l,n])=>
          `<div class="tev"><span class="td">${ddmm(f)}</span>`+
          `<span class="tw">${esc(dv)} <span class="u">${esc(api)}</span></span>`+
          `<span class="tv">${fmt(n)} session${n===1?'':'s'}</span></div>`).join('')+
        `</div>`)
    : '';

  // ---- 02 stability ----
  // A percentage off a handful of sessions is noise dressed up as a finding — one
  // crash in three logs is not "33% unstable". Below the threshold we show the raw
  // count instead of a rate.
  const MIN_RATE=10, thin=rated<MIN_RATE;
  const okPct=(100-v.rough_pct).toFixed(1)+'%';
  const stabKpis = rated ? kpiRow([
    [icon('yes'), thin?`${fmt(oc.clean||0)}/${fmt(rated)}`:okPct, 'Sessions ended normally',
      thin?`too few rated sessions for a rate`:`${fmt(oc.clean||0)} of ${fmt(rated)}`],
    [icon('bug'), fmt(oc.crash||0), 'Crashes', `the game reported these itself`],
    [icon('warning'), fmt(oc.died||0), 'Died without a report', `kill, power loss, or driver reset`],
    [icon('satellite'), fmt(oc.backend||0), 'Back-end unresponsive', `CIG services, not your PC`],
  ]) : '';
  // Sessions archived before CSR read exit codes, whose logs Star Citizen has since
  // deleted, can never be classified. Saying so is better than quietly shrinking the base.
  const unk=oc.unknown||0;
  const unkNote = unk ? ` <b>${fmt(unk)} older session${unk===1?'':'s'}</b> in this scope `+
    `predate this feature and their logs are gone, so they can’t be rated — they’re excluded, not assumed good.` : '';
  const stabNote = callout(icon('warning'),
    `<b>Crashes</b> are ones the game reported itself. <b>Died without a report</b> means the log just stops `+
    `— kill, power loss, or a driver reset. <b>Back-end</b> is CIG's servers, not your PC.`+unkNote);

  // ---- 03 benchmark ----
  const benchKpis = pi ? kpiRow([
    [icon('system'), pi.cpu, 'CPU index', `range ${pi.cpu_min}–${pi.cpu_max}`],
    [icon('display'), pi.gpu, 'GPU index', `range ${pi.gpu_min}–${pi.gpu_max}`],
    [icon('pulse'), fmt(pi.n), 'Launches benchmarked', `higher is faster`],
  ]) : `<div class="card"><div class="empty">No launch benchmark recorded in this scope.</div></div>`;
  // Framed as a COMPARABLE SCORE, not a trend. Measured across this archive, the
  // index cannot resolve driver changes (±1–2%) or renderer changes (sign flips
  // between patches) against a per-launch spread of ~250 points. Presenting the
  // per-patch line as a trend would invite a conclusion the measurement can't carry.
  const benchNote = callout(icon('scales'),
    `Not a frame-rate — a hardware score, comparable <b>between machines</b> but too noisy to `+
    `compare against itself`+
    (pi&&pi.n>=5&&pi.gpu_max>pi.gpu_min?`: the same hardware here scores <b>${pi.gpu_min}–${pi.gpu_max}</b>`:'')+
    `. Read the average, ignore small moves.`);

  // ---- "is it me, or was it the game?" ----
  // The most useful thing this data can tell a newcomer: patches differ enormously,
  // and the patch you happened to start on colours your whole impression of SC.
  const pv=(A().patches)||{}, ranked=(A().order||[])
    .map(k=>({k, p:pv[k]})).filter(x=>x.p && (x.p.outcomes_rated||0)>=MIN_RATE)
    .map(x=>({k:x.k, pct:x.p.rough_pct||0, n:x.p.outcomes_rated}));
  let ctx='';
  if(ranked.length>=2){
    const best=ranked.reduce((a,b)=>b.pct<a.pct?b:a), worst=ranked.reduce((a,b)=>b.pct>a.pct?b:a);
    const here=ranked.find(x=>x.k===current);
    ctx = callout(icon('scales'),
      `<b>How rough a patch feels is mostly the patch, not your PC.</b> On this same machine `+
      `<b>${pLabel(worst.k)}</b> ended badly <b>${worst.pct}%</b> of the time, while `+
      `<b>${pLabel(best.k)}</b> managed <b>${best.pct}%</b> — a ${(worst.pct/Math.max(best.pct,0.1)).toFixed(0)}× `+
      `difference with nothing changed at your end.`+
      (here?` You're looking at <b>${pLabel(here.k)}</b> at <b>${here.pct}%</b>.`:'')+
      ` If you started playing during a bad one, that was the build — not something you did wrong.`);
  }

  const hasPatchTrend=(A().order||[]).length>1;
  const st=(C()||{}).settings;
  const shareCard = `<div class="card" style="margin-top:16px"><h3>Share your setup `+
    `<span class="u">— paste into Discord so others have something to compare against</span></h3>`+
    `<div class="bsub">Nothing is uploaded. CSR has no server — this is just text for you to copy.</div>`+
    `<div class="sharebox"><textarea id="shareTxt" readonly></textarea>`+
    `<button class="foot-link" id="shareCopy" style="margin-top:9px">${icon('export')} Copy to clipboard</button>`+
    `</div></div>`;

  // Order tells a story: the rig you play on → what CSR is watching right now → what
  // broke → how often it breaks. Live monitoring used to sit above Your machine and
  // three groups away from Crashes, which read as two unrelated features.
  // Split in two tabs at 8 groups: the machine you play on barely changes, while the
  // stability side is live and is what you open CSR to check. Mixing them meant
  // scrolling past your CPU model to find out what crashed you an hour ago.
  $('#content').innerHTML = STAB ? (metaLine(v)+
    group(1,'pulse','Live monitoring','Watching your logs for crashes and disconnects',
      watchHTML())+
    group(2,'bug','Crashes','What Star Citizen said went wrong',
      lastCrashHTML(v)+crashCausesHTML(v)+crashListHTML(v))+
    group(3,'no','Back to the main menu','Returned to the front end mid-session and carried on — a server drop or an exit to menu',
      bootsHTML(v))+
    group(4,'warning','Session outcomes','How your sessions ended',
      rated ? stabKpis+ctx+newcomerVerdict(v,oc,rated,thin)+stabNote+
        `<div class="grid2" style="margin-top:16px">`+
        cardHTML('How sessions ended','share of '+fmt(rated)+' sessions','outcomeChart')+
        (hasPatchTrend?barsCard('Rough sessions by patch','% ending badly','roughByPatch')
                      :'<div class="card"><div class="empty">Only one patch in this scope.</div></div>')+
        `</div>`
        : `<div class="card"><div class="empty">No completed sessions rated yet.</div></div>`)+
    `${NOTE}`
  ) : (metaLine(v)+
    group(1,'system','Your machine','What the game reports your rig as',
      specs+changeCard+driverCard)+
    group(2,'yes','Configuration check','What you are running, and anything measurably wrong',
      settingsHTML(st)+
      `<div style="margin-top:16px">`+healthHTML(r,st)+`</div>`+
      `<div style="margin-top:16px">`+settingsAdvice(v)+`</div>`)+
    group(3,'pulse','Performance','Frame rate the game recorded for itself',
      fpsHTML(v) || `<div class="card"><div class="empty">No frame-rate statistics in this scope.</div></div>`)+
    group(4,'system','Hardware score','Star Citizen’s own benchmark — comparable between machines',
      benchKpis+benchNote+
      (pi&&hasPatchTrend?`<div style="margin-top:16px">`+
        barsCard('Score per patch','differences this small are noise','gpuByPatch')+`</div>`:'')+
      shareCard)+
    `${NOTE}`);

  // ---- apply / revert settings (app mode only) ----
  const msg=(t,bad)=>{ const e=$('#setMsg'); if(e){ e.innerHTML=t; e.style.color=bad?'#ff5468':'var(--dim)'; } };
  // Every write is two-step: the button arms itself into a confirm, and the file is
  // copied to an undo point before the change lands.
  document.querySelectorAll('.setact').forEach(b=>{
    b.onclick=()=>confirmBtn(b, 'Confirm', async ()=>{
      const k=b.dataset.k, val=b.dataset.v;
      b.disabled=true; msg('Applying…');
      try{
        const r=await fetch('/api/settings-apply',{method:'POST',
          body:JSON.stringify({channel:currentChannel, changes:{[k]:+val}})});
        const j=await r.json();
        if(!j.ok){ msg(esc(j.error||j.msg||'could not apply'), true); b.disabled=false; return; }
        const ch=Object.entries(j.applied||{}).map(([kk,[a,bb]])=>`${esc(kk)}: ${a} → ${bb}`).join(', ');
        msg(`${esc(j.msg)}${ch?` (${ch})`:''} · restart Star Citizen to see it`);
        setTimeout(()=>location.reload(), 1500);
      }catch(e){ msg('Could not reach CSR.', true); b.disabled=false; }
    });
  });
  document.querySelectorAll('.setrevert').forEach(b=>{
    b.onclick=()=>confirmBtn(b, 'Confirm restore', async ()=>{
      b.disabled=true; msg('Restoring…');
      try{
        const r=await fetch('/api/settings-revert',{method:'POST',
          body:JSON.stringify({channel:currentChannel, which:b.dataset.which})});
        const j=await r.json();
        msg(esc(j.msg||''), !j.ok);
        if(j.ok) setTimeout(()=>location.reload(), 1500); else b.disabled=false;
      }catch(e){ msg('Could not reach CSR.', true); b.disabled=false; }
    });
  });

  if(SERVED) pollStatus();          // fills the Live monitoring card immediately
  document.querySelectorAll('[data-cpg]').forEach(b=>{
    b.onclick=()=>{ crashPage=+b.dataset.cpg; render();
      const el=$('#crashList'); if(el) scrollToY(el.getBoundingClientRect().top+window.pageYOffset-160); };
  });
  const bug=$('#bugCopy');
  if(bug && v.last_crash) bug.onclick=()=>{
    copyText(bugReportText(v, v.last_crash), ok=>{
      const m=$('#bugMsg');
      if(m) m.innerHTML = ok
        ? `Copied. Paste it into an <b>Issue Council</b> report and add what you were doing.`
        : `Could not reach the clipboard — select the text manually.`;
      bug.innerHTML=icon(ok?'yes':'no')+(ok?' Copied':' Copy failed');
      setTimeout(()=>{ bug.innerHTML=icon('export')+' Copy a bug report'; },2200);
    });
  };
  const bt=$('#bootTrend');
  if(bt && v.boots && (v.boots.trend||[]).length>1){
    const MONS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const lbl=k=>{ const p=k.split('-'); return (MONS[+p[1]-1]||p[1])+' '+p[0].slice(2); };
    vbars(bt, v.boots.trend.map(([k,rate,n,h])=>({
      label:lbl(k), value:rate, disp:rate.toFixed(2),
      full:`${lbl(k)} — ${n} drop${n===1?'':'s'} in ${h} h played`,
      color:rate>=3?'#ff5468':(rate>=1?'#ff9147':'var(--cyan)')})),
      {h:250, w:Math.max(560, v.boots.trend.length*74), maxbw:54, fmtY:x=>x.toFixed(1)});
  }
  const sx=$('#shareTxt');
  if(sx){
    sx.value=shareText(v);
    const btn=$('#shareCopy');
    if(btn) btn.onclick=()=>{
      sx.select();
      const done=()=>{ btn.innerHTML=icon('yes')+' Copied'; setTimeout(()=>{ btn.innerHTML=icon('export')+' Copy to clipboard'; },1800); };
      // execCommand fallback matters here: navigator.clipboard is unavailable on
      // file:// pages, which is exactly how the standalone CSR.html gets opened.
      if(navigator.clipboard&&navigator.clipboard.writeText)
        navigator.clipboard.writeText(sx.value).then(done,()=>{ try{document.execCommand('copy');done();}catch(e){} });
      else { try{ document.execCommand('copy'); done(); }catch(e){} }
    };
  }
  if(rated && STAB){
    donut($('#outcomeChart'), [
      {label:'Normal exit', value:oc.clean||0, color:'#4ade80'},
      {label:'Crash (reported)', value:oc.crash||0, color:'#ff5468'},
      {label:'Died, no report', value:oc.died||0, color:'#c2557a'},
      {label:'Back-end down', value:oc.backend||0, color:'#ff9147'},
      {label:'Other', value:oc.other||0, color:'#8b9bb4'},
    ].filter(p=>p.value>0), {big:thin?`${oc.clean||0}/${rated}`:(100-v.rough_pct).toFixed(0)+'%',
                             small:'ended normally'});
    if(hasPatchTrend){
      // Same rule per bar: a patch you launched three times can't carry a percentage.
      const all=patchSeries(p=>{
        const n=p.outcomes_rated||0; if(!n) return null;
        return {value:p.rough_pct||0, disp:(p.rough_pct||0)+'%', n};
      });
      const series=all.filter(s=>s.n>=MIN_RATE), dropped=all.length-series.length;
      const el=$('#roughByPatch');
      if(!series.length){ el.innerHTML='<div class="empty">Not enough rated sessions per patch yet.</div>'; }
      else{
        vbars(el, series.map(s=>({...s,
          full:`${s.full} — ${s.disp} of ${fmt(s.n)} sessions ended badly`,
          color:s.value>=20?'#ff5468':(s.value>=10?'#ff9147':'var(--cyan)')})),
          {h:230, w:Math.max(460, series.length*70), maxbw:48, fmtY:x=>Math.round(x)+'%'});
        // Never drop bars silently — a shorter chart would otherwise read as "no such patch".
        if(dropped) el.insertAdjacentHTML('beforeend',
          `<div class="bsub" style="margin-top:8px">${dropped} patch${dropped===1?'':'es'} hidden `+
          `— fewer than ${MIN_RATE} rated sessions.</div>`);
      }
    }
  }
  if(pi&&hasPatchTrend&&!STAB){
    const series=patchSeries(p=>{
      const q=p.perf_index; if(!q) return null;
      return {value:q.gpu, disp:String(q.gpu), lo:q.gpu_min, hi:q.gpu_max, n:q.n};
    });
    vbars($('#gpuByPatch'), series.map(s=>({...s,
      full:`${s.full} — avg ${s.disp} across ${fmt(s.n)} launches (spread ${s.lo}–${s.hi})`})),
      {h:230, w:Math.max(460, series.length*70), maxbw:48});
  }
}

function secOrgs(v){
  const orgs=(A().orgs||[]).map(o=>{
    const logo = o.logo?`<img src="${o.logo}">`:`<div class="ologo">${o.kind==='main'?icon('hall'):icon('members')}</div>`;
    const kind = o.kind==='main'?'<span class="kind-main">Main org</span>':'<span class="kind-aff">Affiliate</span>';
    const mem = o.members?`<div class="obadge"><div class="k">Members</div><div class="m">${fmt(+o.members)}</div></div>`:'';
    const rank = o.rank?` · ${esc(o.rank)}`:'';
    const nm = o.sid && o.sid!=='REDACTED' ? link(orgURL(o.sid), esc(o.name||o.sid)+' ↗','lk') : esc(o.name||'Redacted');
    return `<div class="org-card">${logo}<div><div class="oname">${nm}</div><div class="osub">${kind} · [${esc(o.sid)}]${rank}</div></div>${mem}</div>`;
  }).join('') || '<div class="empty">No public orgs found.</div>';
  $('#content').innerHTML =
    group(1,'profile','Citizen record','Your public RSI profile',profileHTML())+
    group(2,'orgs','Organizations','Public affiliations, as anyone can see them on RSI',
      `<div class="orgs">${orgs}</div>`)+
    `<div class="note">This is the same public information any player can see on your RSI profile — including affiliate orgs, which is how someone can spot an affiliation you didn't announce.</div>`;
}

// ==== NAV + PATCH ORCHESTRATION ====
let section='overview';
function view(){ return current==='all' ? A().career : A().patches[current]; }
function render(){
  $('#secTitle').textContent=SECTIONS[section].title;
  const sb=$('#secScope');
  if(sb){ sb.textContent = current==='all' ? 'Career' : pLabel(current);
    sb.classList.toggle('patch', current!=='all'); }
  renderLimitedNote();
  SECTIONS[section].fn(view());
}
// banner shown when the current view includes sessions from a game build that
// logged far less than usual (playtime counts; weapons/kills/missions may be missing)
function renderLimitedNote(){
  const el=$('#limitedNote'); if(!el) return;
  const v=view(); const n=(v&&v.limited_sessions)||0;
  if(n>0){
    const h=(v.limited_hours||0), s=n>1?'s':'', those=n>1?'those sessions':'that session';
    el.style.display='';
    el.innerHTML=`${icon('warning')} <b>Limited data</b> — ${fmt(n)} session${s} (${h} h) ran on a game `+
      `build that logged less than usual (seen on some 4.10 PTU builds). The playtime is `+
      `counted, but weapons, kills, ships and missions from ${those} weren't recorded in the log.`;
  } else { el.style.display='none'; }
}

// patch selector (dropdown — saves horizontal space vs a row of tabs), plus a
// ‹ › stepper so you can walk patches without opening the list every time.
const scopeList = () => ['all'].concat(A().order||[]);
function renderPatches(){
  const sel=$('#patchSel'); if(!sel) return;
  // A <select> is as wide as its WIDEST option, so "Career (all patches)" and
  // "Patch 4.1" were setting the width of the whole control. The field is captioned
  // PATCH directly above it, so the word is redundant inside every row.
  let h=`<option value="all">Career</option>`;
  A().order.forEach(p=>{ const v=pBuild(p);
    h+=`<option value="${p}">${pShort(p)}${v?' · '+v:''}</option>`; });
  sel.innerHTML=h; sel.value=current;
  const L=scopeList(), i=L.indexOf(current);
  const pv=$('#patchPrev'), nx=$('#patchNext');
  if(pv){ pv.innerHTML=icon('prev'); pv.disabled = i<=0; }
  if(nx){ nx.innerHTML=icon('next'); nx.disabled = i<0 || i>=L.length-1; }
}
function stepPatch(d){
  const L=scopeList(), i=L.indexOf(current);
  if(i<0) return;
  const j=i+d; if(j<0||j>=L.length) return;
  current=L[j]; renderPatches(); render();
}
$('#patchSel').onchange=e=>{ current=e.target.value; renderPatches(); render(); };
$('#patchPrev').onclick=()=>stepPatch(-1);
$('#patchNext').onclick=()=>stepPatch(1);
// keyboard: ← / → step patches (ignored while typing in a field)
document.addEventListener('keydown',e=>{
  if(e.target.matches('input,select,textarea')||e.metaKey||e.ctrlKey||e.altKey) return;
  if(e.key==='ArrowLeft') stepPatch(-1);
  else if(e.key==='ArrowRight') stepPatch(1);
});

document.querySelectorAll('#nav a').forEach(a=>{
  const s=a.querySelector('span'); if(s&&ICON[a.dataset.sec]) s.innerHTML=ICON[a.dataset.sec];  // swap emoji → HUD glyph
  a.onclick=(e)=>{ e.preventDefault();
    section=a.dataset.sec; document.querySelectorAll('#nav a').forEach(x=>x.classList.toggle('active',x===a)); render(); };
});
// swap emoji → HUD glyph on the top-bar chrome buttons & modal headings
(function(){
  const set=(sel,name,after)=>{const el=document.querySelector(sel);
    if(el) el.innerHTML=icon(name)+(after||'');};
  set('#dataBtn','backup'); set('#refreshBtn','refresh');
  set('#bugBtn','bug',' Report a bug');
  const dct=document.querySelectorAll('.dcard .dct');
  if(dct[0]) dct[0].innerHTML=icon('export')+' Export backup';
  if(dct[1]) dct[1].innerHTML=icon('import')+' Import / merge';
})();
// jump to a section from an in-content link (e.g. Combat → Legacy Combat)
// Smooth scrolling is unreliable — it is a no-op under reduced-motion settings and in
// some automation/embedded contexts, which silently left the page where it was. These
// jumps must always land, so they are instant.
function scrollToY(y){
  y=Math.max(0, Math.round(y));
  try{ window.scrollTo({top:y, behavior:'auto'}); }catch(e){ window.scrollTo(0,y); }
  if(Math.abs(window.scrollY-y)>2) document.documentElement.scrollTop=y;   // last resort
}
function gotoSec(sec){
  // follow an alias (e.g. the retired 'travel' section now lives inside 'flight')
  const s=SECTIONS[sec]; if(s&&s.alias) sec=s.alias;
  const a=document.querySelector(`#nav a[data-sec="${sec}"]`);
  if(a){ a.click(); scrollToY(0); }
}

// channel selector (LIVE / PTU / TP — hidden when only one channel was found)
const CHAN_LABEL={LIVE:'LIVE',PTU:'PTU',EPTU:'EPTU',TP:'Tech Preview',HOTFIX:'Hotfix'};
function renderChannels(){
  const wrap=$('#chanWrap'), sel=$('#chanSel'); if(!wrap||!sel) return;
  const chans=DATA.channels||[];
  if(chans.length<2){ wrap.style.display='none'; return; }
  wrap.style.display='flex';
  sel.innerHTML=chans.map(c=>{
    const n=(DATA.ch[c]||{}).log_count||0;
    return `<option value="${esc(c)}">${esc(CHAN_LABEL[c]||c)} · ${fmt(n)}</option>`;}).join('');
  sel.value=currentChannel;
}
$('#chanSel').onchange=e=>{
  currentChannel=e.target.value; currentAccount=defaultAccount(); current='all';
  $('#who').textContent=currentAccount;
  renderAccts(); renderPatches(); render(); updateGen();
};

// CSR logo = home: back to Overview with channel, account and patch reset to defaults
// (the pinned main account counts as the default, not the most-played guess).
$('#homeBtn').onclick=()=>{
  currentChannel=DATA.default_channel;
  currentAccount=defaultAccount();
  current='all';
  section='overview';
  document.querySelectorAll('#nav a').forEach(x=>x.classList.toggle('active',x.dataset.sec==='overview'));
  $('#who').textContent=currentAccount;
  renderChannels(); renderAccts(); renderPatches(); render(); updateGen();
  scrollToY(0);
};

// account selector (dropdown — hidden when this channel has only one account)
function renderAccts(){
  const wrap=$('#acctWrap'), sel=$('#acctSel'); if(!wrap||!sel) return;
  // always visible for consistency across channels — even a single account is
  // shown (as a one-entry list) rather than hiding the control entirely.
  const accts=(C().accounts||[]);
  const list=accts.length?accts:[{handle:currentAccount||'Citizen',sessions:0}];
  wrap.style.display='flex';
  sel.disabled=list.length<2;
  const pinned=savedMain(currentChannel);
  sel.innerHTML=list.map(a=>
    `<option value="${esc(a.handle)}">${a.handle===pinned?'★ ':''}${esc(a.handle)} · ${fmt(a.sessions)}</option>`).join('');
  sel.value=currentAccount;
  const b=$('#mainBtn');
  if(b){
    const isMain = pinned===currentAccount;
    // only worth offering when there's more than one account to choose between
    b.style.display = list.length<2 ? 'none' : 'flex';
    b.classList.toggle('on', isMain);
    b.innerHTML = isMain ? icon('starOn') : icon('star');
    b.title = isMain
      ? `${currentAccount} is your main account — click to unset (CSR will fall back to the most-played account)`
      : `Set ${currentAccount} as your main account for ${CHAN_LABEL[currentChannel]||currentChannel} — CSR will open here by default`;
  }
}
$('#acctSel').onchange=e=>{ currentAccount=e.target.value; current='all';
  $('#who').textContent=currentAccount; renderAccts(); renderPatches(); render(); };
$('#mainBtn').onclick=()=>{
  setMain(currentChannel, savedMain(currentChannel)===currentAccount ? null : currentAccount);
  answerHint();           // pressing the star IS an answer
  renderAccts();
};

// ---- first-run "pick your main account" coach mark ----------------------- //
// Two kinds of dismissal, and conflating them is why this never got seen: a stray
// click anywhere used to mark it permanently answered, so it vanished for good
// within a second of the dashboard loading. Now only an explicit answer is final;
// clicking elsewhere just gets it out of the way and it returns next launch.
function hideHint(){ const el=$('#mainHint'); if(el) el.classList.remove('on'); }
function answerHint(){ markHintSeen(); hideHint(); }
function maybeShowHint(tries){
  const el=$('#mainHint'); if(!el) return;
  // only when it's genuinely useful: several accounts, none pinned yet, not yet answered
  const many=(C().accounts||[]).length>1;
  if(!many || savedMain(currentChannel) || hintSeen()){ hideHint(); return; }
  // The scan terminal sits at z-index 9000 and covers the whole page. On a first run
  // it is up for the entire scan, so showing the hint underneath it means the user
  // never sees it — and it only ever fires once per load. Wait for the terminal to go.
  const term=$('#csrTerm');
  if(term && term.classList.contains('on')){
    if((tries||0) < 600) setTimeout(()=>maybeShowHint((tries||0)+1), 500);
    return;
  }
  const who=$('#hintWho'); if(who) who.textContent=currentAccount;
  el.classList.add('on');
}
$('#hintSet').onclick=()=>{ setMain(currentChannel,currentAccount); answerHint(); renderAccts(); };
$('#hintNo').onclick=answerHint;
// Deliberately NO click-anywhere dismissal. It shows once per page load, so a stray
// click used to bury it until the next reload — which is why it was never seen. It
// now stays until answered with one of the two buttons above.

// light / dark theme toggle (persisted)
function applyTheme(t){
  document.documentElement.setAttribute('data-theme', t);
  const b=$('#themeBtn'); if(b) b.innerHTML = t==='light' ? icon('day') : icon('night');
  try{ localStorage.setItem('sc_theme', t); }catch(e){}
}
let theme='dark';
try{ theme = localStorage.getItem('sc_theme') || 'dark'; }catch(e){}
applyTheme(theme);
$('#themeBtn').onclick=()=>{
  applyTheme(document.documentElement.getAttribute('data-theme')==='light' ? 'dark' : 'light');
};

// ---- local-app mode: in-page Refresh (only when served by the CSR app) ----
function modal(id,on){ const m=$('#'+id); if(m) m.classList.toggle('on', on); }
let SERVED=false;
// probe the local server; if absent (opened as a plain file), the buttons stay hidden
fetch('/api/status').then(r=>r.ok?r.json():Promise.reject()).then(s=>{
  // SERVED flips here, asynchronously — anything whose visibility depends on it has
  // to be told again, or it stays hidden from the one render that ran before this.
  SERVED=true; $('#refreshBtn').style.display=''; $('#dataBtn').style.display='';
  renderBell();
  if(s&&s.sessions!=null) $('#dmCount').textContent=fmt(s.sessions)+' sessions';
  if(s&&s.online){ ONLINE=s.online; olWhen(); }
  if(s){ CSR_CONSOLE={hidden:!!s.console_hidden,supported:!!s.console_supported}; }
  setStatus(s&&s.busy?'busy':'online');
  // the System tab hides its apply buttons until it knows CSR is app-hosted
  if(section==='system'||section==='stability') render();
}).catch(()=>setStatus('offline'));
// The polling interval is started further down, immediately after POLL_MS and
// pollStatus are declared — calling setPollRate() from here would hit the temporal
// dead zone on POLL_MS and abort the rest of this script.
$('#refreshBtn').onclick=()=>modal('refreshModal',true);
$('#rfCancel').onclick=()=>modal('refreshModal',false);
function runRefresh(full){
  modal('refreshModal',false);
  // Pre-flight: if the CSR app has been closed the scan can never run, so say that
  // plainly instead of opening a terminal that would sit there waiting on nothing.
  fetch('/api/status',{cache:'no-store'}).then(r=>r.json()).then(()=>{
    CSRTerm.start();
    fetch('/api/refresh'+(full?'?full=1':''),{method:'POST'}).then(r=>r.json()).then(res=>{
      if(res.ok){ CSRTerm.finish(()=>location.reload()); }
      else{ CSRTerm.fail('refresh failed — check the CSR window'); }
    }).catch(()=>{ CSRTerm.fail('lost contact with the CSR app — it may have been closed'); });
  }).catch(()=>modal('offlineModal',true));
}
$('#rfGo').onclick=()=>runRefresh(false);
$('#rfFull').onclick=()=>runRefresh(true);
$('#offClose').onclick=()=>modal('offlineModal',false);

// ---- CSR status light -------------------------------------------------------
// A dashboard page outlives the app that made it: you can open a saved CSR.html with
// nothing running behind it, and then Refresh/Import silently can't work. The light
// makes that state visible, and offers the two things a page CAN do to a live app.
// It cannot START CSR — a web page has no way to launch a program on your PC — so
// when it's offline the menu explains what to run instead.
let CSR_STATE='checking';
let CSR_CONSOLE={hidden:false,supported:false};
function setStatus(st, note){
  CSR_STATE=st;
  const w=$('#statusBtn').parentElement;
  w.classList.remove('online','offline','busy');
  w.classList.add(st==='busy'?'busy':st==='online'?'online':'offline');
  $('#statusTx').textContent = st==='online'?'CSR ON':st==='busy'?'WORKING':'OFFLINE';
  // the label is hidden now, so the same words go to the hover tooltip and to the
  // accessible name — a bare coloured dot has neither on its own
  const tip={online:'CSR is running — click for options',
             busy:'CSR is working — scanning your logs',
             offline:'CSR isn’t running — this is a saved copy'}[st] || 'CSR status';
  const stBtn=$('#statusBtn');
  stBtn.dataset.tip=tip; stBtn.setAttribute('aria-label', tip); stBtn.title='';
  const m={online:['CSR is running','Serving this page. Refresh, import and backup all work.'],
           busy:['Working…','CSR is scanning your logs. This page updates when it finishes.'],
           offline:['CSR isn’t running','You’re viewing a saved copy of your dashboard. It stays fully readable, but Refresh and Import need the app.']}[st];
  $('#smState').textContent=m[0];
  $('#smBody').innerHTML=(note||m[1]);
  const conBtn = CSR_CONSOLE.supported
    ? `<button class="mbtn" id="smConsole">${CSR_CONSOLE.hidden?'Show':'Hide'} console</button>` : '';
  $('#smActs').innerHTML = st==='offline'
    ? `<span class="dmnote">Start <b>CSR.exe</b> again, then reload this page.</span>`
    : conBtn+`<button class="mbtn" id="smRestart">Restart</button>`+
      `<button class="mbtn danger" id="smStop">Shut down</button>`;
  const rb=$('#smRestart'), sb=$('#smStop'), cb=$('#smConsole');
  if(rb) rb.onclick=csrRestart;
  if(sb) sb.onclick=csrShutdown;
  if(cb) cb.onclick=()=>{
    fetch('/api/console',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({show:CSR_CONSOLE.hidden})}).then(r=>r.json()).then(r=>{
        CSR_CONSOLE.hidden=r.hidden; setStatus(CSR_STATE);
      }).catch(()=>{});
  };
}
// ---- live crash / disconnect alerts ----
// Rendered wherever you happen to be reading. Dismissing tells the server, so the
// same crash doesn't reappear on the next poll or in another tab.
// Jump to the Crashes group itself. gotoSec('system') alone is a no-op when you're
// already on that tab, which is where most of the links into this live.
function gotoCrashes(){
  bellMenu(false);
  const go=()=>{
    const g=[...document.querySelectorAll('.gsec')]
      .find(s=>s.querySelector('.gt') && s.querySelector('.gt').textContent==='Crashes');
    if(!g) return;
    // Land the heading just below the sticky topbar rather than under it.
    const tb=document.querySelector('.topbar');
    const off=(tb?tb.getBoundingClientRect().height:0)+16;
    scrollToY(g.getBoundingClientRect().top+window.pageYOffset-off);
  };
  if(section!=='stability'){ gotoSec('stability'); setTimeout(go, 220); }
  else go();
}
function alertHost(){
  let el=$('#alerts');
  if(!el){ el=document.createElement('div'); el.id='alerts'; document.body.appendChild(el); }
  return el;
}
// ---- alert bell ----
// The toasts are transient on purpose — they interrupt once and get out of the way.
// The bell is where they live afterwards, for as long as the page is open. The
// durable record of real crashes is the Crashes group in System & Stability; events
// you have already dismissed are not re-sent by the server, so a reload starts clean.
const NOTIF=[]; let notifUnread=0;
function notifTime(ts){
  if(!ts) return '';
  const d=new Date(ts*1000), p=n=>String(n).padStart(2,'0');
  return p(d.getHours())+':'+p(d.getMinutes());
}
function renderBell(){
  const wrap=$('#bellWrap'); if(!wrap) return;
  wrap.style.display=SERVED?'':'none';
  const b=$('#bellBadge');
  if(b){ b.textContent=notifUnread>9?'9+':String(notifUnread); b.classList.toggle('on',notifUnread>0); }
  wrap.classList.toggle('has',notifUnread>0);
  const list=$('#bellList'); if(!list) return;
  list.innerHTML = NOTIF.length ? NOTIF.map((e,i)=>
    `<div class="bm-ev ${e.severity==='bad'?'bad':''}${i<notifUnread?' new':''}">`+
    `<span class="bi">${icon(e.severity==='bad'?'bug':'warning')}</span>`+
    `<div style="flex:1"><div class="bt">${esc(e.title)}</div>`+
    `<div class="bd">${esc(e.detail||'')}</div>`+
    `<div class="bw">${esc(notifTime(e.ts))}${e.test?' · simulated':''}`+
    (e.kind==='crash'&&!e.test?`<a onclick="gotoCrashes()">See crash history →</a>`:'')+
    `</div></div></div>`).join('')
    : `<div class="bm-empty">Nothing raised while this page has been open.</div>`;
}
function notifAdd(e){
  if(NOTIF.some(x=>x.id===e.id)) return false;
  NOTIF.unshift(e);
  if(NOTIF.length>40) NOTIF.length=40;
  notifUnread++; renderBell(); return true;
}
function bellMenu(on){
  const m=$('#bellMenu'); if(!m) return;
  m.classList.toggle('on',on);
  if(on&&notifUnread){ notifUnread=0; renderBell(); }   // opening it IS the read receipt
}
function crashExtra(e){
  const c=e.crash||{}; const bits=[];
  if(c.ws_mb) bits.push(`${(c.ws_mb/1024).toFixed(1)} GB in use`);
  if(c.sys_mb&&c.free_mb!=null) bits.push(`${(c.free_mb/1024).toFixed(1)} GB free of ${Math.ceil(c.sys_mb/1024/4)*4} GB`);
  if(e.exception) bits.push(e.exception);
  return bits.join(' · ');
}
function showAlert(e){
  if(!notifAdd(e)) return;      // already toasted this page-load — bell keeps the record
  const host=alertHost();
  const d=document.createElement('div');
  d.className='alert '+(e.severity==='bad'?'bad':'');
  d.dataset.id=e.id;
  const extra=e.kind==='crash'?crashExtra(e):'';
  d.innerHTML=`<span class="ai">${icon(e.severity==='bad'?'bug':'warning')}</span>`+
    `<div style="flex:1"><div class="at">${esc(e.title)}</div>`+
    `<div class="ad">${esc(e.detail||'')}</div>`+
    (extra?`<div class="amore">${esc(extra)}</div>`:'')+
    // Real crashes link to the history they're actually in. Simulated ones say so
    // instead: they are never written to a log, so the Crashes section would show
    // the last REAL crash and imply it was the one just fired.
    (e.kind!=='crash' ? ''
      : e.test ? `<div class="amore">Simulated — not saved to your crash history</div>`
      : `<div class="amore"><a class="clink" onclick="gotoCrashes()">See crash history →</a></div>`)+
    `</div><button class="ax" title="Dismiss">×</button>`;
  d.querySelector('.ax').onclick=()=>{
    d.remove();
    fetch('/api/events-ack',{method:'POST',body:JSON.stringify({id:e.id})}).catch(()=>{});
  };
  host.appendChild(d);
}
let POLL_MS=6000, pollTimer=null;
function setPollRate(ms){
  if(ms===POLL_MS && pollTimer) return;
  POLL_MS=ms;
  if(pollTimer) clearInterval(pollTimer);
  pollTimer=setInterval(pollStatus, POLL_MS);
}
function pollStatus(){
  return fetch('/api/status',{cache:'no-store'}).then(r=>r.ok?r.json():Promise.reject())
    .then(s=>{ if(s&&s.online){ONLINE=s.online; olWhen();}
               if(s){CSR_CONSOLE={hidden:!!s.console_hidden,supported:!!s.console_supported};}
               if(s&&s.events) s.events.forEach(showAlert);
               if(s&&s.watch) renderWatch(s.watch);
               // check in more often while Star Citizen is actually writing its log,
               // so a crash surfaces in ~5s instead of ~8s; back off when it isn't
               if(s) setPollRate(s.session_live?3000:6000);
               setStatus(s&&s.busy?'busy':'online'); return true; })
    .catch(()=>{ setStatus('offline'); return false; });
}
// keep the light honest — /api/status is a trivial read, so this costs nothing.
// Also surfaces any crash that happened while the page was closed, on first poll.
setPollRate(6000);
pollStatus();
{ const bb=$('#bellBtn'), bc=$('#bellClear');
  // swap the emoji placeholder for the HUD glyph, same as the sidebar does. This
  // rewrites the badge span, so it has to happen before the first renderBell().
  if(bb) bb.innerHTML=icon('bell')+'<span class="bbadge" id="bellBadge"></span>';
  if(bb) bb.onclick=e=>{ e.stopPropagation();
    bellMenu(!$('#bellMenu').classList.contains('on')); };
  if(bc) bc.onclick=e=>{ e.stopPropagation();
    // ack every one so the server stops re-sending them to this or any other tab
    NOTIF.forEach(v=>fetch('/api/events-ack',{method:'POST',body:JSON.stringify({id:v.id})}).catch(()=>{}));
    NOTIF.length=0; notifUnread=0; renderBell(); };
  document.addEventListener('click',e=>{ if(!e.target.closest('.bellwrap')) bellMenu(false); });
}
renderBell();
function csrShutdown(){
  $('#smBody').textContent='Shutting down…';
  fetch('/api/shutdown',{method:'POST'}).catch(()=>{});
  // the process is going away, so the reply may never land — just watch it disappear
  setTimeout(()=>{ statusMenu(false); setStatus('offline'); }, 700);
}
function csrRestart(){
  $('#smBody').textContent='Restarting — this page will reconnect on its own.';
  setStatus('busy','Restarting…');
  fetch('/api/restart',{method:'POST'}).catch(()=>{});
  // poll until it answers again, then reload. CSR restarts with --no-browser so no
  // second tab opens; this tab is the one that comes back.
  let tries=0;
  const wait=setInterval(()=>{
    tries++;
    fetch('/api/status',{cache:'no-store'}).then(r=>{
      if(r.ok){ clearInterval(wait); location.reload(); }
    }).catch(()=>{ if(tries>40){ clearInterval(wait); setStatus('offline'); } });
  }, 500);
}
function statusMenu(on){ const m=$('#statusMenu'); if(m) m.classList.toggle('on', on); }
$('#statusBtn').onclick=e=>{ e.stopPropagation();
  const m=$('#statusMenu'); statusMenu(!m.classList.contains('on')); };
document.addEventListener('click',e=>{ if(!e.target.closest('.statuswrap')) statusMenu(false); });

// ---- online data: fetched once and kept, with an explicit way to update ----
let ONLINE={};
function olWhen(){
  const el=$('#olWhen'); if(!el) return;
  const ts=ONLINE.profile||ONLINE.forced;
  if(!ts){ el.textContent=''; return; }
  const days=Math.floor((Date.now()-new Date(ts))/86400000);
  el.textContent='Last updated '+(days<1?'today':days===1?'yesterday':days+' days ago')+'.';
}
$('#olBtn').onclick=()=>{
  const b=$('#olBtn'), was=b.textContent;
  b.disabled=true; b.textContent='Updating…';
  modal('dataModal',false); CSRTerm.start();
  fetch('/api/refresh-online',{method:'POST'}).then(r=>r.json()).then(res=>{
    if(res.ok){ CSRTerm.finish(()=>location.reload()); }
    else{ b.disabled=false; b.textContent=was; CSRTerm.fail(res.error||'update failed'); }
  }).catch(()=>{ b.disabled=false; b.textContent=was;
    CSRTerm.fail('lost contact with the CSR app — it may have been closed'); });
};

// ---- data / backup modal: export + import the session archive ----
$('#dataBtn').onclick=()=>{ $('#dmMsg').textContent=''; olWhen(); modal('dataModal',true); };
$('#dmClose').onclick=()=>modal('dataModal',false);
$('#dmExport').onclick=()=>{ window.location='/api/export'; $('#dmMsg').innerHTML='<span style="color:var(--cyan)">Downloading your archive…</span>'; };
// The three card titles carried emoji: ⬇ and ⬆ rendered as plain glyphs in the text
// colour, but 📁 came out as a full-colour emoji — one yellow icon in a row of white
// ones. Swap all three for the HUD set so they match each other and the rest of the UI.
document.querySelectorAll('.dct[data-ic]').forEach(e=>{
  if(ICON[e.dataset.ic]) e.insertAdjacentHTML('afterbegin', icon(e.dataset.ic));
});
$('#dmImportBtn').onclick=()=>$('#dmFile').click();
$('#dmFile').onchange=e=>{
  const files=[...e.target.files]; e.target.value=''; if(!files.length) return;
  modal('dataModal',false); CSRTerm.start();
  // read every chosen backup, combine their sessions into one payload, import once
  Promise.all(files.map(f=>f.text())).then(texts=>{
    let sessions=[], bad=0;
    texts.forEach(t=>{ try{ const d=JSON.parse(t); if(d&&Array.isArray(d.sessions)) sessions=sessions.concat(d.sessions); else bad++; }catch(_){ bad++; } });
    if(!sessions.length){ CSRTerm.fail(bad?'those files aren’t CSR backups':'nothing to import'); return; }
    return fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sessions})})
      .then(r=>r.json()).then(res=>{
        if(res.ok){ CSRTerm.finish(()=>location.reload()); }
        else{ CSRTerm.fail('import failed: '+(res.error||'bad file')); }
      });
  }).catch(()=>{ CSRTerm.fail('is the CSR app still running?'); });
};
// Escape hatch for the native dialog. It shares runImportFolder() with the picker, so
// both routes go through exactly the same import — only the way the path is obtained
// differs. Kept collapsed so it doesn't compete with the button for attention.
$('#dmPasteToggle').onclick=()=>{
  const row=$('#dmPasteRow'); row.classList.toggle('on');
  if(row.classList.contains('on')) $('#dmPastePath').focus();
};
$('#dmPasteGo').onclick=()=>{
  const v=$('#dmPastePath').value.trim().replace(/^["']|["']$/g,'');
  if(!v) return;
  modal('dataModal',false); CSRTerm.start(); runImportFolder(v);
};
$('#dmPastePath').addEventListener('keydown',e=>{ if(e.key==='Enter') $('#dmPasteGo').click(); });
// Shared by the native picker and the paste box.
function runImportFolder(path){
  return fetch('/api/import-logs',{method:'POST',headers:{'Content-Type':'application/json'},
                                   body:JSON.stringify({path})})
    .then(r=>r.json()).then(r2=>{
      if(r2.ok){ CSRTerm.finish(()=>location.reload()); }
      else{ CSRTerm.fail(r2.error||'could not read that folder'); }
    })
    .catch(()=>CSRTerm.fail('lost contact with the CSR app — it may have been closed'));
}
// Import a copied Game.log FOLDER. The folder is picked natively and read by the
// app off disk — a log folder is hundreds of MB, far too much to upload through
// the browser, and this also saves installing CSR on the other PC just to Export.
$('#dmLogsBtn').onclick=()=>{
  const btn=$('#dmLogsBtn'), was=btn.textContent;
  btn.disabled=true; btn.textContent='Waiting for folder…';
  // The dialog frequently opens BEHIND the browser and no amount of Win32 coaxing has
  // fixed it (see pick_folder_dialog). So say so plainly the moment it's opened, and
  // put the paste box in reach in the same breath rather than leaving it to be found.
  $('#dmHint').classList.add('on'); $('#dmPasteRow').classList.add('on');
  // Step 1 — native folder dialog. The terminal stays closed while it's open;
  // showing a progress readout over an unanswered dialog looked like a hang.
  fetch('/api/pick-import-folder',{method:'POST'}).then(r=>r.json()).then(res=>{
    btn.disabled=false; btn.textContent=was; $('#dmHint').classList.remove('on');
    // A failed picker used to look exactly like Cancel — the button just reset and
    // nothing happened, with no way to tell the dialog had never opened.
    if(!res.ok && res.error){
      modal('dataModal',false); CSRTerm.fail(res.error); return;
    }
    if(!res.ok) return;                       // cancelled: stay in the panel
    // Step 2 — folder chosen, NOW show the terminal and do the work
    modal('dataModal',false); CSRTerm.start();
    return runImportFolder(res.path);
  }).catch(()=>{
    btn.disabled=false; btn.textContent=was; $('#dmHint').classList.remove('on');
    modal('dataModal',false);
    CSRTerm.fail('lost contact with the CSR app — it may have been closed');
  });
};

// about / legal modal
const CSR_VER='__CSR_VERSION__', CSR_MAIL='__CSR_CONTACT__';
$('#aboutBtn').onclick=()=>modal('aboutModal',true);
$('#aboutClose').onclick=()=>modal('aboutModal',false);
$('#bugBtn').onclick=()=>{
  const body='What happened:\n\n\nWhat I expected:\n\n\nSteps to reproduce:\n\n\n'+
    '— — —\nCSR v'+CSR_VER+'\nData generated: '+DATA.generated+'\nBrowser: '+navigator.userAgent;
  location.href='mailto:'+CSR_MAIL+'?subject='+encodeURIComponent('CSR bug report (v'+CSR_VER+')')+
    '&body='+encodeURIComponent(body);
};
// click backdrop to dismiss any modal
document.querySelectorAll('.modal-back').forEach(b=>b.onclick=e=>{ if(e.target===b) b.classList.remove('on'); });

// footer: total logs across channels, or this channel's count when multiple exist
function updateGen(){
  const multi=(DATA.channels||[]).length>1;
  const n = multi ? ((DATA.ch[currentChannel]||{}).log_count||0) : DATA.log_count;
  const tag = multi ? `${fmt(n)} ${CHAN_LABEL[currentChannel]||currentChannel} logs` : `${fmt(n)} logs`;
  $('#gen').innerHTML = `generated ${DATA.generated}<br>${tag} · v__CSR_VERSION__`;
}
$('#who').textContent = currentAccount;
renderChannels(); renderAccts(); renderPatches(); render(); updateGen();
// Show the coach mark once the page has actually settled — on a first run the
// dashboard arrives via a reload straight out of the loading terminal, and firing
// too early meant it appeared mid-paint. Only ever shown to multi-account users
// who haven't answered it yet.
if(document.readyState==='complete') setTimeout(maybeShowHint,1200);
else window.addEventListener('load',()=>setTimeout(maybeShowHint,1200));
</script>
<!--__TERMINAL__-->
</body>
</html>
"""


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #

ONBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CSR · Setup</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 90 110'><polygon points='45,4 86,58 68,58 45,30 22,58 4,58' fill='%235bd1e6'/><polygon points='45,44 70,78 52,78 45,68 38,78 20,78' fill='%23e7edf2' opacity='0.85'/><line x1='45' y1='90' x2='45' y2='104' stroke='%235bd1e6' stroke-width='3' stroke-linecap='round'/></svg>">
<style>
/*__FONTS__*/
  :root{--bg:#070b11;--panel:#111a26;--panel2:#0d1622;--line:#1d2c3d;--txt:#cddcec;--muted:#7089a1;--dim:#43566b;
    --cyan:#5bd1e6;--red:#ff5468;
    --font-display:'Chakra Petch','Segoe UI',system-ui,sans-serif;
    --font-mono:'Share Tech Mono',ui-monospace,Consolas,monospace;}
  *{box-sizing:border-box}html,body{margin:0}
  body{font-family:var(--font-display);color:var(--txt);min-height:100vh;display:flex;
    align-items:center;justify-content:center;padding:24px;background:
      radial-gradient(1000px 500px at 85% -5%,#14283a 0,rgba(0,0,0,0) 55%),
      radial-gradient(900px 500px at 0% 0%,#1a1330 0,rgba(0,0,0,0) 50%),var(--bg);}
  .box{max-width:560px;width:100%;background:linear-gradient(180deg,var(--panel),var(--panel2));
    border:1px solid var(--line);border-radius:16px;padding:44px 40px 32px;text-align:center}
  .mark{width:60px;height:73px;margin:0 auto 18px;display:block;filter:drop-shadow(0 0 10px rgba(91,209,230,.4))}
  .mark .c1{fill:var(--cyan)}.mark .c2{fill:var(--txt);opacity:.85}
  .mark .c3{stroke:var(--cyan);stroke-width:3;stroke-linecap:round}
  h1{font-size:26px;letter-spacing:7px;margin:0 0 5px;font-weight:700}
  .sub{font-family:var(--font-mono);font-size:11px;letter-spacing:3px;color:var(--muted);
    text-transform:uppercase;margin-bottom:24px}
  p{color:var(--muted);font-size:14px;line-height:1.65;margin:0 auto 26px;max-width:450px}
  p b{color:var(--txt);font-weight:600}
  .btn{font-family:var(--font-display);font-weight:700;letter-spacing:1px;font-size:15px;cursor:pointer;
    color:#06121f;background:linear-gradient(90deg,var(--cyan),#8fe6f2);border:0;border-radius:11px;
    padding:13px 26px;transition:.14s}
  .btn:hover{filter:brightness(1.08)}.btn:disabled{opacity:.5;cursor:default;filter:none}
  .orline{font-family:var(--font-mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--dim);margin:18px 0 10px;position:relative}
  .pathrow{display:flex;gap:8px;max-width:440px;margin:0 auto}
  .pathrow input{flex:1;min-width:0;font-family:var(--font-mono);font-size:12px;color:var(--txt);
    background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:10px 12px}
  .pathrow input:focus{outline:none;border-color:var(--cyan)}
  .btn2{font-family:var(--font-display);font-weight:700;font-size:13px;cursor:pointer;color:var(--txt);
    background:transparent;border:1px solid var(--line);border-radius:9px;padding:0 16px;transition:.13s}
  .btn2:hover{border-color:var(--cyan);color:var(--cyan)}
  .spin{width:26px;height:26px;border-radius:50%;border:3px solid var(--line);border-top-color:var(--cyan);
    animation:sp .8s linear infinite;margin:16px auto 0;display:none}
  @keyframes sp{to{transform:rotate(360deg)}}
  .msg{font-family:var(--font-mono);font-size:12px;margin-top:16px;min-height:16px;letter-spacing:.03em}
  .msg.err{color:var(--red)}.msg.ok{color:var(--cyan)}
  .hint{font-family:var(--font-mono);font-size:10.5px;color:var(--dim);margin-top:24px;line-height:1.7;
    border-top:1px solid var(--line);padding-top:16px}
</style></head><body>
<div class="box">
  <svg class="mark" viewBox="0 0 90 110" fill="none">
    <polygon class="c1" points="45,4 86,58 68,58 45,30 22,58 4,58"/>
    <polygon class="c2" points="45,44 70,78 52,78 45,68 38,78 20,78"/>
    <line class="c3" x1="45" y1="90" x2="45" y2="104"/>
  </svg>
  <h1>CSR</h1>
  <div class="sub">Citizen Service Record</div>
  <p>Welcome, Citizen. To build your career dashboard, point CSR at your
     <b>StarCitizen</b> folder — the one that holds <b>LIVE</b> (and <b>PTU</b> /
     <b>TECH-PREVIEW</b> if you run them). CSR finds each channel automatically.</p>
  <button class="btn" id="pick">Select StarCitizen folder…</button>
  <div class="orline">or paste the path</div>
  <div class="pathrow">
    <input id="pathIn" type="text" spellcheck="false"
           placeholder="C:\Program Files\Roberts Space Industries\StarCitizen">
    <button class="btn2" id="usePath">Use</button>
  </div>
  <div class="spin" id="spin"></div>
  <div class="msg" id="msg"></div>
  <div class="hint">Usually C:\Program Files\Roberts Space Industries\StarCitizen &nbsp;(a LIVE folder works too)<br>
     Your logs never leave your PC · CSR v__CSR_VERSION__<br>
     Fan-made — not affiliated with CIG / RSI · <a style="color:var(--muted)" href="mailto:__CSR_CONTACT__">__CSR_CONTACT__</a></div>
</div>
<script>
const $=s=>document.querySelector(s);
const msg=$('#msg'), spin=$('#spin');
function busy(on){ $('#pick').disabled=on; $('#usePath').disabled=on; spin.style.display=on?'block':'none'; }
async function scanAndGo(){                       // folder is set on the server; run the scan
  CSRTerm.start();
  try{
    const j=await (await fetch('/api/refresh',{method:'POST'})).json();
    if(j.ok){ CSRTerm.finish(()=>location.reload()); }
    else{ CSRTerm.fail('scan failed — check the CSR window'); }
  }catch(e){ CSRTerm.fail('is the CSR window still open?'); }
}
$('#pick').onclick=async()=>{
  msg.className='msg'; msg.textContent='Opening folder picker…';
  try{
    const j=await (await fetch('/api/pick-folder',{method:'POST'})).json();
    if(j.cancelled){ msg.textContent=''; return; }
    if(!j.ok){ msg.className='msg err'; msg.textContent=(j.error||'Could not read that folder.')+' You can paste the path below instead.'; return; }
    scanAndGo();
  }catch(e){ msg.className='msg err'; msg.textContent='Picker unavailable — paste your LIVE folder path below instead.'; }
};
$('#usePath').onclick=async()=>{
  const path=$('#pathIn').value.trim();
  if(!path){ $('#pathIn').focus(); return; }
  msg.className='msg'; msg.textContent='Checking folder…';
  try{
    const j=await (await fetch('/api/set-folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})})).json();
    if(!j.ok){ msg.className='msg err'; msg.textContent=j.error||'That folder doesn’t look right.'; return; }
    scanAndGo();
  }catch(e){ msg.className='msg err'; msg.textContent='Setup failed — is the CSR window still open?'; }
};
$('#pathIn').addEventListener('keydown',e=>{ if(e.key==='Enter') $('#usePath').click(); });
</script>
<!--__TERMINAL__-->
</body></html>"""


def ONBOARD_HTML():
    return (ONBOARD_TEMPLATE
            .replace("/*__FONTS__*/", sc_names.fetch_google_fonts_embed(cache_file("sc_fonts_embed.css")))
            .replace("<!--__TERMINAL__-->", TERMINAL_HTML)
            .replace("__CSR_VERSION__", CSR_VERSION)
            .replace("__CSR_CONTACT__", CSR_CONTACT))


_RESOLVERS_LOADED = False


def load_resolvers():
    """Load ship + item name indexes once (from app-data caches)."""
    global _RESOLVERS_LOADED
    if _RESOLVERS_LOADED:
        return
    SHIPS.cache_path = cache_file("sc_ship_matrix.json")
    ITEMS.cache_path = cache_file("sc_item_names.json")
    if SHIPS.load():
        cstep(f"ship registry synced — {len(SHIPS.by_name):,} hulls on file")
    else:
        cwarn("ship registry offline — falling back to raw hull codes")
    if ITEMS.load():
        sc_names.ITEMS = ITEMS
        cstep(f"munitions database synced — {len(ITEMS.idx):,} entries on file")
    else:
        cwarn("munitions database offline — using curated arms table")
    BPS.cache_path = cache_file("sc_blueprints.json")
    if BPS.load():
        cstep(f"crafting catalogue synced — {len(BPS.rows):,} blueprints ({BPS.version or 'unknown build'})")
    else:
        cwarn("crafting catalogue offline — blueprints shown without the missing list")
    _RESOLVERS_LOADED = True


def generate(out_path, explicit_logs=None, live_path=None, quick=False):
    """Scan logs, merge with the persistent archive, build the dashboard HTML.
    The archive is the source of truth, so history survives log deletion and any
    imported sessions from other PCs are included. Returns data or None.

    quick=True re-reads only logs that are new or changed since the last scan
    (rotated backups never change), which is the normal refresh. A full scan
    re-parses everything and is what you want after a CSR update."""
    set_progress(0.02, "linking to fleet registry")
    load_resolvers()
    load_img_cache()
    channels = resolve_channels(live_path, explicit_logs)
    archived = load_archive()
    scanned, dropped = ([], 0)
    if channels:
        cstep("locating black-box archives — " + ", ".join(
            f"{_paint(lab, _WHT)} ({len(f) + (1 if l else 0)})" for lab, f, l in channels))
        scanned, dropped = scan_channels(channels,
                                         reuse=_reuse_map(archived) if quick else None)

    sessions = merge_sessions(scanned, archived)
    if not sessions:
        return None
    new_count = len(sessions) - len(archived)
    if archived:
        csub(f"archive: {len(archived):,} on file "
             f"{('· +' + format(new_count, ',') + ' new') if new_count > 0 else '· up to date'}")
    if dropped:
        csub(f"purged {dropped:,} duplicate records (cross-channel copies)")
    save_archive(sessions)                 # persist the union so history is never lost

    set_progress(0.88, "compiling service record")
    # a Full re-scan re-fetches your RSI dossier too; Quick refresh uses the
    # cached copy (refreshed on its own once a day) so it stays fast
    data = build_from_sessions(sessions, fresh_profile=not quick)
    if not data:
        return None
    set_progress(0.96, "embedding hull & armament imagery")
    save_img_cache()
    html_out = render_html(data)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    set_progress(1.0, "complete")
    dc = data["ch"][data["default_channel"]]
    prim = dc["acct"][dc["primary"]]
    cok(f"service record compiled — {data['log_count']:,} sessions across "
        f"{len(data['channels'])} channel(s)")
    csub(f"{data['default_channel']} · {dc['primary']}: {prim['career']['hours']} h over "
         f"{prim['career']['sessions']} sessions, {len(prim['order'])} patches · "
         f"channels: {', '.join(data['channels'])}")
    csub(f"dossier written {_g('→', '->')} {out_path}")
    return data


# --------------------------------------------------------------------------- #
#  Local-app mode: tiny stdlib HTTP server + native folder picker
# --------------------------------------------------------------------------- #

def pick_folder_dialog():
    """Open the native Windows 'select folder' dialog via Windows PowerShell's WinForms
    FolderBrowserDialog — so we don't have to bundle Tcl/Tk (~3-4 MB) just for a picker.

    Returns the chosen path, None if cancelled, or False if the picker itself failed.

    KNOWN LIMITATION: the dialog often opens BEHIND the browser. Three approaches were
    tried and all three failed on a real machine; they're recorded so the next person
    (or the next me) doesn't spend the afternoon re-deriving them:

      * `$owner.TopMost = $true` — TopMost sets z-order between windows; it does not
        make the shell's Browse-For-Folder dialog (a separate #32770 window) topmost.
      * AllowSetForegroundWindow() + SetForegroundWindow() — Windows only honours these
        for a process that ALREADY holds the foreground. CSR's console is hidden and the
        browser owns the foreground, so the grant is refused and the call is a no-op.
      * SetWindowPos(HWND_TOPMOST) from a WinForms timer inside ShowDialog's modal pump.
        This needs no foreground rights and the timer does fire, but the dialog still
        came up behind the browser.

    The root problem is that CSR's foreground-activation rights are already spent: the
    click happens in the BROWSER, so the browser is what Windows considers to have
    earned the right to raise a window — not the background HTTP handler reacting to it.
    Rather than escalate (a visible always-on-top shim, forced input-thread attachment),
    the UI now states plainly that the dialog may be behind, and offers a paste-the-path
    box that doesn't depend on window focus at all. Fix the picker if a clean way turns
    up, but the import path is no longer blocked on it.

    The SetWindowPos timer is kept: it is harmless, and on setups where the browser is
    not fullscreen it is what makes the dialog visible at all.
    """
    init = r"C:\Program Files\Roberts Space Industries\StarCitizen\StarCitizen"
    if not os.path.isdir(init):
        init = r"C:\Program Files\Roberts Space Industries\StarCitizen"
    # Written to a temp .ps1 and run with -File: this outgrew -Command, where every
    # embedded quote needs escaping twice and a typo fails silently at runtime.
    script = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -Namespace CSR -Name Win -MemberDefinition @'
  [DllImport("user32.dll", SetLastError=true)]
  public static extern IntPtr FindWindowEx(IntPtr p, IntPtr c, string cls, string title);
  [DllImport("user32.dll")]
  public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")]
  public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y,
                                         int cx, int cy, uint flags);
  [DllImport("user32.dll")]
  public static extern bool IsWindowVisible(IntPtr h);
'@

$owner = New-Object System.Windows.Forms.Form
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.StartPosition = 'Manual'
$owner.Location = New-Object System.Drawing.Point(-4000,-4000)
$owner.Size = New-Object System.Drawing.Size(1,1)
$owner.TopMost = $true
$owner.Show()

# Lift the shell dialog above every other window once it exists. HWND_TOPMOST = -1;
# SWP_NOMOVE|SWP_NOSIZE|SWP_SHOWWINDOW = 0x0043. This does not need foreground rights.
$me = [System.Diagnostics.Process]::GetCurrentProcess().Id
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 120
$timer.Add_Tick({
  $h = [IntPtr]::Zero
  while ($true) {
    $h = [CSR.Win]::FindWindowEx([IntPtr]::Zero, $h, '#32770', $null)
    if ($h -eq [IntPtr]::Zero) { break }
    $pid_out = 0
    [void][CSR.Win]::GetWindowThreadProcessId($h, [ref]$pid_out)
    if ($pid_out -eq $me -and [CSR.Win]::IsWindowVisible($h)) {
      [void][CSR.Win]::SetWindowPos($h, [IntPtr](-1), 0, 0, 0, 0, 0x0043)
      $timer.Stop()
      break
    }
  }
})
$timer.Start()

$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
$dlg.Description = 'Select your StarCitizen folder (contains LIVE, PTU, ...)'
$dlg.ShowNewFolderButton = $false
$seed = '__INIT__'
if (Test-Path $seed) { $dlg.SelectedPath = $seed }
$result = $dlg.ShowDialog($owner)
$timer.Stop()
$owner.Close()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
  [Console]::Out.Write($dlg.SelectedPath)
}
""".replace("__INIT__", init.replace("'", "''"))

    tmp = None
    try:
        import subprocess
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".ps1", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(script)
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
             "-WindowStyle", "Hidden", "-File", tmp],
            capture_output=True, text=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            # PowerShell itself failed (missing, blocked, WinForms unavailable). This
            # used to be indistinguishable from "user pressed Cancel", so the button
            # silently reset and nobody could tell the picker was broken.
            cerr(f"folder picker failed: {(r.stderr or '').strip()[:200]}")
            return False
        return (r.stdout or "").strip() or None      # None = cancelled
    except Exception as e:
        cerr(f"folder picker unavailable: {e}")
        return False                                  # False = broken, not cancelled
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def serve(out_path, port=7878, explicit_logs=None, open_browser=True,
          hide_console=True):
    import http.server
    import threading
    import queue

    cfg = load_config()
    state = {"html": None, "configured": False, "generated": None,
             "log_count": 0, "live_path": cfg.get("live_path"),
             "explicit": explicit_logs, "busy": False, "sessions": len(load_archive()),
             "has_work": False, "seen": time.time()}
    gen_lock = threading.Lock()
    pick_req, pick_res = queue.Queue(), queue.Queue()

    def _power_down(restart):
        """Stop CSR from the page. On restart, relaunch with --no-browser so the tab
        you're already looking at is reused instead of a duplicate opening."""
        time.sleep(0.35)                      # let the HTTP reply flush
        try:
            srv.shutdown()
            srv.server_close()                # release the port before the new process
        except Exception:
            pass
        if restart:
            clog(f"{_paint(_g('⟳', '~'), _CY)} restarting…", _CY)
            # Popen, NOT os.execv: on Windows execv doesn't quote its arguments, so an
            # install path containing spaces ("G:\\My Drive\\…") is split and the
            # relaunch dies with "can't open file". Popen quotes properly.
            args = [a for a in sys.argv[1:] if a != "--no-browser"] + ["--no-browser"]
            cmd = [sys.executable] + ([] if is_frozen() else [os.path.abspath(sys.argv[0])]) + args
            try:
                import subprocess as _sp
                time.sleep(0.4)               # let the socket fully close
                _sp.Popen(cmd, cwd=os.getcwd())
                os._exit(0)
            except Exception as e:
                cerr(f"restart failed: {e}")
        tui_disable()
        clog(f"{_paint(_g('◼', '#'), _AMB)} terminal powered down. fly safe, Citizen.", _AMB)
        os._exit(0)

    def ready_banner():
        """Re-print how to stop CSR once the scan output has finished, then get out of
        the way — CSR is driven from the browser, so the console is only clutter.

        The banner is printed before scanning starts, so on a first run the progress
        output pushes it off screen — leaving no visible hint of how to quit."""
        print()
        cok(f"ready — {_paint(url, _CY)}")
        csub("press Ctrl+C to power down · or use the status light in the page")
        print()

    def watchdog():
        """If the console is hidden and no page has polled for a while, nothing is
        watching CSR at all — a browser tab was closed and the app would otherwise
        linger invisibly with no way to reach it short of Task Manager. Quit cleanly.
        Only ever applies while hidden; with the console visible, Ctrl+C is right there."""
        GRACE = 900                          # 15 min — long enough for a background tab
        while True:
            time.sleep(30)
            if not _CONSOLE_HIDDEN:
                continue
            if time.time() - state.get("seen", 0) > GRACE:
                show_console(True)
                clog(f"{_paint(_g('◼', '#'), _AMB)} no dashboard open for 15 minutes — "
                     f"powering down.", _AMB)
                os._exit(0)

    def do_generate(quick=False):
        with gen_lock:
            state["busy"] = True
            try:
                data = generate(out_path, state["explicit"], state["live_path"],
                                quick=quick)
            except Exception as e:
                cerr(f"refresh error: {e}")
                data = None
            finally:
                state["busy"] = False
            if not data:
                state["configured"] = False
                return False
            state["html"] = open(out_path, encoding="utf-8").read()
            state["configured"] = True
            state["generated"] = data["generated"]
            state["log_count"] = data["log_count"]
            state["sessions"] = data["log_count"]
            return True

    csr_banner()
    # scanning runs in the BACKGROUND so the browser can open onto the animated
    # loading terminal immediately, instead of waiting for the scan to finish.
    state["has_work"] = bool(resolve_channels(state["live_path"], state["explicit"]) or state["sessions"])
    if not state["has_work"]:
        cwarn("no flight recorder linked yet — the browser will walk you through setup.")

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            b = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(b)
            except Exception:
                pass

        def do_GET(self):
            p = self.path.split("?")[0]
            if p in ("/", "/index.html", "/CSR.html", "/sc_stats.html"):
                if state["configured"] and state["html"]:
                    self._send(200, state["html"], "text/html; charset=utf-8")
                elif state["busy"] or state["has_work"]:
                    self._send(200, BOOT_HTML(), "text/html; charset=utf-8")   # scanning → terminal
                else:
                    self._send(200, ONBOARD_HTML(), "text/html; charset=utf-8")
            elif p == "/api/status":
                state["seen"] = time.time()    # heartbeat — see the watchdog in serve()
                self._send(200, json.dumps({
                    "configured": state["configured"], "live_path": state["live_path"],
                    "generated": state["generated"], "log_count": state["log_count"],
                    "sessions": state["sessions"], "busy": state["busy"],
                    "online": online_meta(),
                    "console_hidden": _CONSOLE_HIDDEN,
                    "console_supported": _console_hwnd() is not None,
                    "progress": _PROGRESS,
                    # live crash/disconnect watcher: unseen events + whether a game
                    # session is currently writing (the page polls faster while it is)
                    "events": watch_events(),
                    "session_live": _WATCH["live"],
                    "watch": watch_status()}))
            elif p == "/api/export":
                # hand over the accumulated archive as a downloadable backup
                try:
                    raw = open(archive_path(), "rb").read()
                except Exception:
                    save_archive([])
                    raw = open(archive_path(), "rb").read()
                stamp = datetime.now().strftime("%Y-%m-%d")
                self._send(200, raw, "application/json",
                           extra={"Content-Disposition": f'attachment; filename="CSR-archive-{stamp}.json"'})
            else:
                self._send(404, "{}")

        def _accept_folder(self, path):
            """Validate the picked folder + save it; scanning happens on a follow-up
            /api/refresh so the page can show a spinner. Accepts either your
            StarCitizen folder (which holds LIVE / PTU / …) or a single channel
            folder. Sends the JSON reply."""
            path = (path or "").strip().strip('"')
            if not path:
                self._send(200, json.dumps({"ok": False, "error": "No folder path given."}))
                return
            if not _is_sc_folder(path):
                self._send(200, json.dumps({"ok": False,
                    "error": "That doesn't look right — pick your StarCitizen folder (the "
                             "one containing LIVE / PTU), or a LIVE folder directly."}))
                return
            state["live_path"] = path
            save_config({"live_path": path})
            self._send(200, json.dumps({"ok": True, "path": path}))

        def _body(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(n).decode("utf-8", "replace") if n else ""

        def do_POST(self):
            p = self.path.split("?")[0]
            if p == "/api/refresh":
                # ?full=1 re-parses every log; default reads only new/changed ones
                full = "full=1" in (self.path.split("?", 1)[1] if "?" in self.path else "")
                ok = do_generate(quick=not full)
                self._send(200 if ok else 500, json.dumps({
                    "ok": ok, "generated": state["generated"],
                    "log_count": state["log_count"]}))
            elif p == "/api/pick-folder":
                pick_req.put(True)
                path = pick_res.get()              # main thread runs the dialog
                if path is False:                  # the picker itself failed
                    self._send(200, json.dumps({"ok": False, "error":
                        "the folder picker could not open - see the CSR console"}))
                    return
                if not path:                       # user pressed Cancel
                    self._send(200, json.dumps({"ok": False, "cancelled": True}))
                    return
                self._accept_folder(path)
            elif p == "/api/set-folder":           # paste-the-path fallback
                try:
                    path = json.loads(self._body() or "{}").get("path", "")
                except Exception:
                    path = ""
                self._accept_folder(path)
            elif p == "/api/import":               # merge another PC's archive
                body = self._body()
                if not body.strip():
                    self._send(200, json.dumps({"ok": False, "error": "empty file"}))
                    return
                try:
                    added, total = import_archive_file(body)
                except Exception as e:
                    self._send(200, json.dumps({"ok": False, "error": str(e)}))
                    return
                do_generate()                      # rebuild the dashboard from the merged archive
                self._send(200, json.dumps({"ok": True, "added": added, "total": total,
                                            "log_count": state["log_count"]}))
            elif p == "/api/console":
                try:
                    want = bool(json.loads(self._body() or "{}").get("show"))
                except Exception:
                    want = True
                ok = show_console(want)
                self._send(200, json.dumps({"ok": ok, "hidden": _CONSOLE_HIDDEN,
                                            "visible": console_visible(),
                                            "supported": _console_hwnd() is not None}))
            elif p == "/api/shutdown":
                self._send(200, json.dumps({"ok": True}))
                # reply first, then bring the process down from a side thread so the
                # browser actually receives the response
                threading.Thread(target=_power_down, args=(False,), daemon=True).start()
            elif p == "/api/restart":
                self._send(200, json.dumps({"ok": True, "port": port}))
                threading.Thread(target=_power_down, args=(True,), daemon=True).start()
            elif p == "/api/refresh-online":
                # re-pull everything that comes from the network, then rebuild the
                # page. quick=True: logs haven't changed, only the online data has.
                try:
                    retried = refresh_online_data()
                except Exception as e:
                    self._send(200, json.dumps({"ok": False, "error": str(e)}))
                    return
                ok = do_generate(quick=True)
                self._send(200 if ok else 500,
                           json.dumps({"ok": ok, "retried": retried}))
            elif p == "/api/pick-import-folder":
                # Picking is its own request so the browser can wait for the native
                # dialog BEFORE opening the loading terminal — otherwise the terminal
                # sat there animating over a modal dialog the user hadn't answered yet.
                pick_req.put(True)
                path = pick_res.get()              # main thread runs the native dialog
                if path is False:                  # the picker itself failed
                    self._send(200, json.dumps({"ok": False, "error":
                        "the folder picker could not open - see the CSR console"}))
                    return
                if not path:                       # user pressed Cancel
                    self._send(200, json.dumps({"ok": False, "cancelled": True}))
                    return
                self._send(200, json.dumps({"ok": True, "path": path.strip().strip('"')}))
            elif p == "/api/import-logs":          # merge a copied folder of Game.logs
                try:
                    path = json.loads(self._body() or "{}").get("path", "")
                except Exception:
                    path = ""
                if not path:
                    self._send(200, json.dumps({"ok": False, "error": "no folder given"}))
                    return
                try:
                    added, total, scanned = import_log_folder(path.strip().strip('"'))
                except Exception as e:
                    self._send(200, json.dumps({"ok": False, "error": str(e)}))
                    return
                # merging changes the archive, so the page has to be rebuilt from it.
                # quick=True: the imported logs are already in the archive, and this
                # PC's own logs haven't changed, so there's nothing to re-read.
                do_generate(quick=True)
                self._send(200, json.dumps({"ok": True, "added": added, "total": total,
                                            "scanned": scanned,
                                            "log_count": state["log_count"]}))
            elif p == "/api/settings-apply":
                # Writes to the game's own settings file, so it is deliberately narrow:
                # only keys CSR understands, only for a known channel, always backed up,
                # and never while the game is running.
                try:
                    req = json.loads(self._body() or "{}")
                except Exception:
                    req = {}
                chan = req.get("channel")
                changes = req.get("changes") or {}
                allowed = {"AutoDetect", "SysSpec"}
                changes = {k: v for k, v in changes.items()
                           if k in allowed and str(v).lstrip("-").isdigit()}
                if not changes:
                    self._send(200, json.dumps({"ok": False, "error": "nothing valid to apply"}))
                    return
                ok, msg, applied = write_settings(_CHANNEL_DIRS.get(chan), changes)
                if ok and applied:
                    _SETTINGS_CACHE.pop(chan, None)
                    do_generate(quick=True)     # refresh the page's settings snapshot
                self._send(200, json.dumps({"ok": ok, "msg": msg, "applied": applied}))
            elif p == "/api/test-event":
                try:
                    kind = (json.loads(self._body() or "{}") or {}).get("kind", "crash")
                except Exception:
                    kind = "crash"
                n = fire_test_event(kind)
                self._send(200, json.dumps(
                    {"ok": n is not None, "raised": n or 0, "kind": kind}))
            elif p == "/api/events-ack":
                try:
                    upto = json.loads(self._body() or "{}").get("id")
                except Exception:
                    upto = None
                ack_events(upto)
                self._send(200, json.dumps({"ok": True}))
            elif p == "/api/settings-revert":
                try:
                    req = json.loads(self._body() or "{}")
                except Exception:
                    req = {}
                chan = req.get("channel")
                which = "original" if req.get("which") == "original" else "prev"
                ok, msg = restore_settings(_CHANNEL_DIRS.get(chan), which)
                if ok:
                    _SETTINGS_CACHE.pop(chan, None)
                    do_generate(quick=True)
                self._send(200, json.dumps({"ok": ok, "msg": msg, "which": which}))
            else:
                self._send(404, "{}")

        def log_message(self, *a):
            pass

    srv = None
    for p in range(port, port + 12):
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", p), H)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        cerr("could not bind a local port.")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}/"
    print()
    cok(f"terminal online — {_paint(url, _CY)}")
    csub("press Ctrl+C to power down · or use the status light in the page")
    print()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    start_watcher()      # tail Game.log for crashes / disconnects while you play
    # Hide the console straight away rather than after the scan: the browser's
    # flight-recorder terminal is already showing that progress, so leaving this
    # window up means two things reporting the same thing. Everything printed while
    # hidden is still in the scrollback when you bring it back.
    if hide_console and _console_hwnd():
        csub("hiding this window — bring it back from the status light in the page")
        print()
        time.sleep(1.1)                       # long enough to read the URL first
        show_console(False)
    # from here the console is a fixed frame rather than a scrolling log
    chans = " · ".join(lab for lab, _f, _l in
                       (resolve_channels(state["live_path"], state["explicit"]) or []))
    tui_enable(url, chans)
    # open the browser NOW so it lands on the loading terminal while we scan
    # skipped on a restart — the tab that asked for it is still open and polling
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    if state["has_work"]:
        # Startup is a QUICK pass: unchanged log files are reused from the archive, so
        # relaunching CSR is near-instant instead of re-reading every log again. New and
        # modified logs are still read, and an archive written by an older parser is
        # rejected wholesale by _reuse_map — so a CSR update still re-parses everything
        # exactly once, automatically, without anyone having to ask for a full re-scan.
        threading.Thread(
            target=lambda: (do_generate(quick=True), ready_banner()), daemon=True).start()
    else:
        ready_banner()
    try:
        while True:
            try:
                pick_req.get(timeout=0.3)
            except queue.Empty:
                continue
            pick_res.put(pick_folder_dialog())
    except KeyboardInterrupt:
        tui_disable()
        print()
        clog(f"{_paint(_g('◼', '#'), _AMB)} terminal powered down. fly safe, Citizen.", _AMB)
        srv.shutdown()


def main():
    ap = argparse.ArgumentParser(description="CSR — Citizen Service Record · Star Citizen career dashboard")
    ap.add_argument("--logs", help="path to a LIVE\\logbackups folder (forces a single LIVE channel)")
    ap.add_argument("--live", help="your StarCitizen folder (holds LIVE/PTU/…), or a single channel folder")
    ap.add_argument("--out", default=None, help="output HTML path")
    ap.add_argument("--serve", action="store_true",
                    help="run as a local app: in-page Refresh + first-run onboarding")
    ap.add_argument("--port", type=int, default=7878)
    ap.add_argument("--open", action="store_true", help="open the result in a browser (one-shot mode)")
    ap.add_argument("--quick", action="store_true",
                    help="re-read only new/changed logs (default is a full re-scan)")
    ap.add_argument("--no-browser", action="store_true",
                    help="serve without opening a browser tab (used when restarting)")
    ap.add_argument("--console", action="store_true",
                    help="keep the console window open (it is hidden once CSR is ready)")
    args = ap.parse_args()

    out_path = args.out or os.path.join(app_dir(), "CSR.html")

    # The packaged exe launches the app experience by default.
    if args.serve or (is_frozen() and not args.open and not args.out):
        serve(out_path, args.port, explicit_logs=args.logs,
              open_browser=not args.no_browser, hide_console=not args.console)
        return

    csr_banner()
    live_path = args.live or load_config().get("live_path")
    data = generate(out_path, explicit_logs=args.logs, live_path=live_path,
                    quick=args.quick)
    if not data:
        cerr("no flight recorder found. Pass --live \"...\\StarCitizen\", "
             "or run with --serve to link your StarCitizen folder.")
        sys.exit(1)
    if args.open:
        webbrowser.open("file:///" + out_path.replace("\\", "/"))


if __name__ == "__main__":
    main()
