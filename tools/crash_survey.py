#!/usr/bin/env python3
"""Survey the crashes in a set of Game.logs: how many carry the crash-context fields,
and which [Error] tag was the top one in the seconds before each crash.

    python tools/crash_survey.py                  -> logs from csr_config.json's live_path
    python tools/crash_survey.py "D:\...\LIVE"     -> a channel folder (logbackups + Game.log)
    python tools/crash_survey.py "D:\...\logbackups"  -> a bare folder of *.log
    python tools/crash_survey.py -v               -> also one line per crash (file, tags)

Only logs containing the crash handler's marker are parsed, with the same scan_log the
app uses, so the numbers match what the Stability tab shows. Read-only.

Used to decide which last_err tags earn an ERR_PHRASE sentence in sc_stats.py: a tag
that tops several crashes, and whose log lines say plainly what was going on.

Developer tool, not part of the app.
"""
import glob
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import sc_stats  # noqa: E402


def log_files(folder):
    """Every log under a channel folder (logbackups + Game.log), else the *.log in it."""
    files, live = sc_stats._channel_files(folder)
    if live:
        files = files + [live]
    return files or glob.glob(os.path.join(folder, "*.log"))


def has_crash(path):
    mark = sc_stats.CRASH_MARK.encode()
    try:
        with open(path, "rb") as f:
            return mark in f.read()
    except OSError:
        return False


def main(argv):
    verbose = "-v" in argv
    args = [a for a in argv if a != "-v"]
    folder = args[0] if args else None
    if not folder:
        cfg = os.path.join(ROOT, "csr_config.json")
        try:
            folder = json.load(open(cfg, encoding="utf-8")).get("live_path")
        except (OSError, ValueError):
            folder = None
    if not folder or not os.path.isdir(folder):
        sys.exit("no logs folder: pass one, or set live_path in csr_config.json")

    files = log_files(folder)
    crashes = []
    for p in sorted(files):
        if not has_crash(p):
            continue
        c = sc_stats.scan_log(p).get("crash")
        if c:
            crashes.append((p, c))

    n = len(crashes)
    print(f"{folder}")
    print(f"{len(files)} logs scanned, {n} crashes")
    if not n:
        return
    for field in ("phase", "where", "ship_cls", "last_err"):
        print(f"  {field:<9} {sum(1 for _, c in crashes if c.get(field)):>4} of {n}")
    phases = Counter(c.get("phase") or "-" for _, c in crashes)
    print("  by phase  " + ", ".join(f"{ph} {k}" for ph, k in phases.most_common()))

    tops = Counter()
    for _, c in crashes:
        errs = c.get("last_err") or []
        if errs:
            tops[errs[0]["tag"]] += 1
    print(f"\nlast_err tag, by crashes it tops ({sum(tops.values())} crashes have one)")
    for tag, k in tops.most_common():
        print(f"  {k:>4}  {tag}")

    if verbose:
        print()
        for p, c in crashes:
            errs = "; ".join(f"{e['tag']} x{e['n']}" + (f" cls={e['cls']}" if e.get("cls") else "")
                             + (f" to={e['to']}" if e.get("to") else "")
                             for e in c.get("last_err") or [])
            print(f"{os.path.basename(p)}  {c.get('phase') or '-'}  {c.get('where') or '-'}  {errs or '-'}")


if __name__ == "__main__":
    main(sys.argv[1:])
