#!/usr/bin/env python3
"""Survey own deaths, arrivals and deathloops in a set of Game.logs.

    python tools/deathloop_survey.py                  -> logs from csr_config.json's live_path
    python tools/deathloop_survey.py "D:\\...\\LIVE"     -> a channel folder (logbackups + Game.log)
    python tools/deathloop_survey.py "D:\\...\\logbackups"  -> a bare folder of *.log
    python tools/deathloop_survey.py -v               -> also one line per own death

Only logs containing a corpse list are parsed, with the same scan_log the app uses, and
folded the way the Stability tab folds them, so the loops and quick-death rows printed
are the ones it shows (career scope). Read-only.

Used to check ARRIVED_DEAD_S and the loop / quick-death thresholds in sc_stats.py
against a new build's logs.

Developer tool, not part of the app.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import sc_stats  # noqa: E402
from crash_survey import log_files  # noqa: E402


def has_corpse(path):
    mark = sc_stats.CORPSE_ITEM_MARK.encode()
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
    agg = sc_stats.blank_patch()
    hit = 0
    for p in sorted(files):
        if not has_corpse(p):
            continue
        hit += 1
        sc_stats.fold(agg, sc_stats.scan_log(p))
    deaths = sorted(agg["own_deaths"])
    v = sc_stats._deathloop_summary(deaths)

    print(f"{folder}")
    print(f"{len(files)} logs scanned, {hit} with corpse lists")
    print(f"  own deaths    {len(deaths):>5}   (gear lost counts {len(agg['gl_sizes'])})")
    print(f"  arrived dead  {sum(1 for d in deaths if d[1]):>5}")
    print(f"  deathloops    {v['n'] if v else 0:>5}")
    for lp in (v["recent"] if v else [])[::-1]:
        print(f"    {lp['start']} -> {lp['at'][11:]}  {lp['n']} deaths, {lp['arrived']} arrived, "
              f"{lp['rejoins']} rejoins, gaps {lp['gaps']}, after {lp['after_s']} s")
    print(f"  quick rows    {v['quick_n'] if v else 0:>5}")
    for q in (v["quick"] if v else [])[::-1]:
        print(f"    {q['start']} -> {q['at'][11:]}  {q['n']} deaths")

    if verbose:
        print()
        for t, arrived, src, _end in deaths:
            print(f"{t}  {'ARRIVED' if arrived else '-':<7}  {src}")


if __name__ == "__main__":
    main(sys.argv[1:])
