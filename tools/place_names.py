#!/usr/bin/env python3
"""Build sc_place_names.json from the game's English global.ini.

Location codes in Game.log (RequestLocationInventory) are object names. For two big
families the localisation keys ARE those codes, so the game's own name can be looked
up directly:

    Pyro3_Outpost_col_m_frm_indy_001   = Shepherd's Rest
    Stanton3a_ASD_Delving_Facility_002 = Onyx Facility S3A2   (the log says _Delve_)

Everything else is named by rules in sc_stats.place_name(). Run after a patch:

    python tools/place_names.py path/to/global.ini

Developer tool, not part of the app.
"""
import json
import os
import re
import sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sc_place_names.json")
OUTPOST = re.compile(r"^(?:Stanton|Pyro|Nyx)\d[a-z]?_Outpost_col_\w+$", re.I)
ONYX = re.compile(r"^(Stanton\d[a-z]?)_ASD_Delving_Facility_(\d+)$", re.I)


def build(ini):
    out = {}
    with open(ini, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            if "=" not in line:
                continue
            key, val = line.rstrip("\r\n").split("=", 1)
            key, val = key.split(",")[0].strip(), val.strip()
            if not val or len(val) > 60 or key.lower().endswith(("_desc", "_clinic")):
                continue
            if OUTPOST.match(key):
                out[key.lower()] = val
            m = ONYX.match(key)
            if m:
                out[f"{m.group(1)}_asd_delve_facility_{m.group(2)}".lower()] = val
    return dict(sorted(out.items()))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    names = build(sys.argv[1])
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(names, fh, ensure_ascii=False, indent=0)
    print(f"wrote {OUT}: {len(names)} places")
