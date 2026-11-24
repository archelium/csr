#!/usr/bin/env python3
"""
sc_names  —  turn Star Citizen backend class IDs into real in-fiction names.

* Ships   -> resolved against the official RSI Ship Matrix (authoritative name,
             manufacturer and artwork).  Cached to sc_ship_matrix.json.
* Weapons -> manufacturer codes expanded to full names + a curated table of
             confirmed model names (e.g. klwe_rifle_energy_01 -> Klaus & Werner
             Gallant).  Unknown models fall back to "<Manufacturer> <Type>" so a
             name is always reasonable and never fabricated.
"""

import datetime
import json
import os
import re
import unicodedata
import ssl
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE
_UA = {"User-Agent": "Mozilla/5.0 SCStats"}

# --------------------------------------------------------------------------- #
#  Manufacturers
# --------------------------------------------------------------------------- #

WEAPON_MAN = {
    "klwe": "Klaus & Werner", "behr": "Behring", "ksar": "Kastak Arms",
    "gemi": "Gemini", "apar": "Apocalypse Arms", "grin": "Greycat",
    "crlf": "CureLife", "volt": "Volt", "lbco": "Lightning Bolt Co.",
    "gmni": "Gemini", "ndsl": "Klaus & Werner", "amrs": "Amon & Reese Co.",
    "hdgw": "Hedeby Gunworks", "utfl": "UltiFlex", "sasu": "Sakura Sun", "vlk": "Behring",
    "glsn": "Grey's Market",
}
# Grey's Market guns carry no maker in their class (none_...). Their wiki pages give the
# maker code GLSN, which is Grey's Market. The Deadrig is listed as unknown everywhere.
NONE_MAKER = {
    "none_rifle_multi_01": "glsn",          # Killshot Rifle
    "none_lmg_ballistic_01": "glsn",        # Pulverizer LMG
    "none_smg_energy_01": "glsn",           # Ripper SMG
    "none_pistol_ballistic_01": "glsn",     # Tripledown Pistol
    "none_special_ballistic_01": "glsn",    # Boomtube Rocket Launcher
}


def weapon_maker_code(cls):
    """The maker code of a weapon class ('behr', 'glsn'), '' when unknown."""
    c = (cls or "").lower()
    tok = c.split("_")[0]
    if tok == "none":
        for base, code in NONE_MAKER.items():
            if c == base or c.startswith(base + "_"):
                return code
        return ""
    return tok if tok in WEAPON_MAN else ""


# Confirmed weapon/tool model names (base class -> model). Only entries we're
# confident about; everything else uses the manufacturer + type fallback.
WEAPON_MODELS = {
    "klwe_rifle_energy_01": "Gallant",
    "volt_rifle_energy_01": "Parallax",
    "klwe_smg_energy_01": "Sawbuck",
    "klwe_pistol_energy_01": "Arclight",
    "klwe_sniper_energy_01": "Arrowhead",
    "behr_rifle_ballistic_01": "P4-AR",
    "behr_rifle_ballistic_02": "P8-AR",
    "behr_smg_ballistic_01": "P8-SC",
    "behr_lmg_ballistic_01": "FS-9 LMG",
    "behr_pistol_ballistic_01": "S-38",
    "behr_gren_frag_01": "Frag Grenade",
    "behr_binoculars_01": "Binoculars",
    "ksar_rifle_ballistic_01": "Karna",
    "gemi_pistol_ballistic_01": "LH86",
    "gemi_shotgun_ballistic_01": "R97 Shotgun",
    "gemi_pistol_energy_01": "S71",
    "apar_shotgun_ballistic_01": "Devastator",
    "apar_railgun_electron_01": "Scourge Railgun",
    "grin_multitool_01": "Pyro Multi-Tool",
    "grin_tractor_01": "Tractor Beam Attachment",
    "crlf_medgun_01": "MedGun (ParaMed)",
    "crlf_consumable_healing_01": "Hemozal Injector",
    "hdgw_rifle_ballistic_01": "Arlington Rifle",   # Hedeby Gunworks lever-action rifle (4.10)
    # not in the SC-Wiki item index yet, named from their starcitizen.tools pages
    "hdgw_optics_tsco_x4_s2": "Myers (4x Telescopic)",   # Hedeby scope for the Arlington
    "ksar_lmg_energy_01": "Verdict LMG",                 # Kastak Arms energy LMG
}

# Type words used to build a readable fallback ("<Man> Ballistic Sniper")
TYPE_WORDS = {
    "rifle": "Rifle", "smg": "SMG", "lmg": "LMG", "pistol": "Pistol",
    "shotgun": "Shotgun", "sniper": "Sniper", "gren": "Grenade",
    "grenade": "Grenade", "launcher": "Launcher", "railgun": "Railgun",
    "binoculars": "Binoculars", "multitool": "Multi-Tool", "tractor": "Tractor Beam",
    "medgun": "MedGun", "ballistic": "Ballistic", "energy": "Energy",
    "scattergun": "Scattergun",
}

# color / edition tokens to drop so variants collapse to one weapon
_VARIANT = re.compile(
    r"_(black|blue|white|red|green|grey|gray|orange|gold|tan|purple|yellow|pink|"
    r"tint|civilian|collector|lux|luxe|msn|rwd|reward|default|indust|s42|iae\d*|"
    r"firerats\d*|store\d*|spc|gilded|silver|brown|sand|digi|urban|olive|desert|"
    r"nature|frost|citizencon\d*|cc\d*|holiday|festive|mystic|glow)\w*", re.I)


def strip_variant(cls):
    """Collapse a weapon/tool class to its base model (drop colours/editions)."""
    prev = None
    out = cls
    while out != prev:
        prev = out
        out = _VARIANT.sub("", out)
    return out.strip("_")


def wiki_title_for_display(display_name):
    """Reverse a resolved gun name back to its wiki page title (model), or None."""
    for cls, model in WEAPON_MODELS.items():
        man = WEAPON_MAN.get(cls.split("_")[0], "")
        full = f"{man} {model}".strip() if man else model
        if full == display_name:
            return model
    return None


# Module-level authoritative item-name index (loaded by the caller).
ITEMS = None


def _skin_strip(name):
    """Drop the quoted skin nickname: 'P8-AR "Blackguard" Rifle' -> 'P8-AR Rifle'.
    Single quotes too (Deadrig 'Noxious' Shotgun), only as a whole word pair."""
    name = re.sub(r'\s*"[^"]*"\s*', " ", name or "")
    name = re.sub(r"(^|\s)'[^']+'(?=\s|$)", " ", name)
    return re.sub(r"\s+", " ", name).strip()


# The game's own item names (the API's "name" is its localisation string) with CIG's
# typos fixed to the starcitizen.tools page. Applied over every load of the index.
NAME_FIX = {
    "qrt_specialist_heavy_helmet_01_01_12": "Antium Helmet Jet",          # game: "Antium Core Jet"
    "qrt_specialist_heavy_arms_01_01_13": "Antium Arms Maroon",           # game: "Antium Helmet Jet"
    "rrs_combat_heavy_backpack_04_02_01": "Morozov-CH Backpack Gold Horizon",
    "fta_medium_helmet_01_01_13": "Ambrose Helmet Onyx",                  # game: "Strata …"
    "fta_medium_helmet_01_01_15": "Ambrose Helmet Calico",
    "fta_medium_helmet_01_01_18": "Ambrose Helmet Piston",
    "vgl_armor_light_legs_01_01_15": "TrueDef-Pro Legs Tan/Silver",
    "rsi_explorer_armor_light_helmet_01_01_01": "Venture Helmet",
    "klwe_ubarrel_flsh_s1": "Brightspot Flashlight",                      # game: "… Purple"
    "sasu_pistol_toy_01": "WowBlast Desperado Toy Pistol",                # base reads "… Red"
}

# personal_weapon.class from the API, in one spelling ("Electron" and "Energy (Electron)")
_AMMO_FIX = {"Electron": "Energy (Electron)", "Laser": "Energy (Laser)", "Plasma": "Energy (Plasma)"}


def _real_name(cn, name):
    """False for the API's placeholder names: the class id itself, "@loc_key", PLACEHOLDER."""
    return bool(name) and name.lower() != cn and not name.startswith("@")         and "placeholder" not in name.lower()


def weapon_display(cls):
    """Return (display_name, kind) for a held-item class id, kind ∈ {'gun','tool',None}.

    Names come from the authoritative SC-Wiki item index (game data) when
    available — already manufacturer-free and skin-free.  Falls back to the
    curated table, then to a clean generic, so a name is always reasonable."""
    base = strip_variant(cls)
    key = base.lower()

    name = None
    if ITEMS is not None:
        name = ITEMS.resolve(key)

    if not name:
        model = WEAPON_MODELS.get(key)
        man = WEAPON_MAN.get(key.split("_")[0], "")
        if model:
            name = model
        else:
            toks = key.split("_")[1:]
            material = [TYPE_WORDS[t] for t in toks if t in ("ballistic", "energy")]
            wtype = [TYPE_WORDS[t] for t in toks
                     if t in TYPE_WORDS and t not in ("ballistic", "energy")]
            ordered = []
            for p in material + wtype:
                if p not in ordered:
                    ordered.append(p)
            typ = " ".join(ordered) if ordered else " ".join(
                t.title() for t in toks if not t.isdigit())
            name = f"{man} {typ}".strip() if man else (typ or base.replace("_", " ").title())

    disp = _skin_strip(name)
    return disp, weapon_kind(cls, disp)


class ItemIndex:
    """Maps game item class ids -> display names via the SC-Wiki item API."""

    API = "https://api.star-citizen.wiki/api/v2/items?limit=200&page="

    def __init__(self, cache=None):
        self.cache_path = cache or os.path.join(SCRIPT_DIR, "sc_item_names.json")
        self.idx = {}
        self.guns = {}          # class -> [type, ammo class, image url] for personal weapons
        self.imgs = {}          # class -> catalogue image url, every item that has one
        self.meta = {}          # class -> [class, grade, maker] for ship, FPS and wearable items
        self._by_name = None
        self.fetched = None
        self.ok = False

    def load(self, allow_fetch=True, force=False):
        if os.path.isfile(self.cache_path):
            try:
                with open(self.cache_path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                # {"fetched", "names", "guns", "imgs"} since 1.10; a bare name map before
                if isinstance(raw, dict) and isinstance(raw.get("names"), dict):
                    self.idx, self.guns, self.fetched = raw["names"], raw.get("guns") or {}, raw.get("fetched")
                    self.imgs = raw.get("imgs") or {}
                    self.meta = raw.get("meta") or {}
                else:
                    self.idx, self.guns, self.fetched = raw or {}, {}, None
            except Exception:
                self.idx = {}
        if allow_fetch and (not self.idx or force):
            self._fetch()
        self.ok = bool(self.idx)
        if self.ok:
            self.idx.update(NAME_FIX)
        return self.ok

    def _fetch(self):
        names, guns, pics, meta = {}, {}, {}, {}
        try:
            page, last = 1, 1
            while page <= last:
                req = urllib.request.Request(self.API + str(page), headers=_UA)
                d = json.loads(urllib.request.urlopen(req, timeout=40, context=_SSL).read())
                for i in d.get("data", []):
                    cn = (i.get("class_name") or "").lower()
                    if not cn or not _real_name(cn, i.get("name")):
                        continue
                    names.setdefault(cn, i.get("name"))
                    imgs = [im.get("thumbnail_url") or im.get("original_url")
                            for im in (i.get("images") or [])]
                    imgs = [u for u in imgs if u]
                    # a quoted skin name in the file ("Gallant "Conviction Camo"…") is a
                    # skin's picture: the card shows the base gun
                    img = next((u for u in imgs if "%22" not in u), imgs[0] if imgs else None)
                    if img and (cn not in pics or "%22" in pics[cn]):
                        pics[cn] = img
                    # what the Blueprints board sorts components by: Military S2 grade A, Aegis
                    if (i.get("classification") or "").startswith(("Ship.", "FPS.", "Char_")):
                        mk = (i.get("manufacturer") or {}).get("name") or ""
                        m = [i.get("class") or "", i.get("grade") or "", "" if mk.lower() == "unknown" else mk]
                        if any(m):
                            meta.setdefault(cn, m)
                    pw = i.get("personal_weapon") or {}
                    if pw.get("type"):
                        had = (guns.get(cn) or [None, None, None])[2]
                        if cn not in guns or not had or ("%22" in had and img and "%22" not in img):
                            ammo = pw.get("class") or ""
                            guns[cn] = [pw.get("type"), _AMMO_FIX.get(ammo, ammo), img or had]
                last = d.get("meta", {}).get("last_page", 1)
                page += 1
        except Exception as e:
            print(f"[sc_names] item index fetch failed: {e}")
            return
        # items the API has dropped keep their last known name (old logs still carry them)
        names = {**{k: v for k, v in self.idx.items() if _real_name(k, v)}, **names}
        self.idx, self.guns, self.imgs, self.meta, self._by_name = names, guns, pics, meta, None
        self.fetched = datetime.date.today().isoformat()
        with open(self.cache_path, "w", encoding="utf-8") as fh:
            json.dump({"fetched": self.fetched, "names": names, "guns": guns, "imgs": pics, "meta": meta}, fh)

    def image_for(self, name):
        """Catalogue image url for an item by the name CSR shows (skin names stripped),
        else None. The base item's picture wins over a skin's."""
        if self._by_name is None:
            by = {}
            for cn in sorted(self.imgs, key=len):          # base classes are the shortest
                n = self.idx.get(cn)
                if n:
                    by.setdefault(n, cn)
                    by.setdefault(_skin_strip(n), cn)
            self._by_name = by
        cn = self._by_name.get(name)
        return self.imgs.get(cn) if cn else None

    def gun(self, cls):
        """[type, ammo class, image url] for a personal weapon, from its base class first
        (a card groups every skin under the base gun), then its own, else None."""
        c = (cls or "").lower()
        return self.guns.get(strip_variant(c)) or self.guns.get(c)

    def resolve(self, base_key):
        if base_key in self.idx:
            return _skin_strip(self.idx[base_key])
        # base only exists as skinned variants -> take the shortest, strip skin
        variants = [v for k, v in self.idx.items() if k.startswith(base_key + "_")]
        if variants:
            return _skin_strip(min(variants, key=len))
        return None


class XparrowIcons:
    """Which game classes have a picture on xparrow-lite.pages.dev (a Star Citizen fan
    site): icons for ship components and ship guns, FPS weapons and attachments (two
    lists they publish), and armour and clothing thumbnails (no list, found by asking
    for each class CSR has no picture of). CSR keeps the class names only. The page
    loads the picture from their site for an item CSR has no picture of."""

    BASE = "https://xparrow-lite.pages.dev/"
    LISTS = {"s": "icons/ship/index.json", "f": "icons/index.json"}

    def __init__(self, cache=None):
        self.cache_path = cache or os.path.join(SCRIPT_DIR, "sc_xparrow.json")
        self.ver = {}                 # "s"/"f" -> their cache-busting version
        self.ids = {}                 # lowercased class -> (kind, id as they spell it)
        self.ok = False
        self.fetched = None

    def _apply(self, d):
        self.ver = {k: str(d.get(k + "_v") or "") for k in self.LISTS}
        self.ids = {}
        for k in ("t", "f", "s"):     # icons win over thumbnails, ship over FPS
            for i in d.get(k) or []:
                self.ids[str(i).lower()] = (k, str(i))
        self.thumbs = list(d.get("t") or [])
        self.fetched = d.get("fetched")
        self.ok = bool(self.ids)

    def load(self, force=False):
        if not force and os.path.isfile(self.cache_path):
            try:
                self._apply(json.load(open(self.cache_path, encoding="utf-8")))
                if self.ok:
                    return True
            except Exception:
                pass
        try:
            keep = list(getattr(self, "thumbs", []))
            if not keep and os.path.isfile(self.cache_path):      # thumbnails found on earlier runs
                try:
                    keep = json.load(open(self.cache_path, encoding="utf-8")).get("t") or []
                except Exception:
                    keep = []
            d = {"fetched": datetime.date.today().isoformat(), "t": keep}
            for k, path in self.LISTS.items():
                req = urllib.request.Request(self.BASE + path, headers=_UA)
                j = json.loads(urllib.request.urlopen(req, timeout=20, context=_SSL).read())
                ids = j.get("ids") or []
                d[k] = sorted(ids.keys() if isinstance(ids, dict) else ids)
                d[k + "_v"] = j.get("v") or ""
            if d["s"] or d["f"]:
                json.dump(d, open(self.cache_path, "w", encoding="utf-8"))
                self._apply(d)
        except Exception:
            pass
        return self.ok

    def probe_thumbs(self, classes, delay=0.5):
        """Ask for an armour/clothing thumbnail for each class not already known, one at a
        time, gently. Keeps the ones that exist. Returns how many were found."""
        import time
        known = set(self.ids)
        found = []
        for c in dict.fromkeys(c for c in classes if c and c.lower() not in known):
            try:
                req = urllib.request.Request(f"{self.BASE}thumbs/sc/{c}.webp", headers=_UA, method="HEAD")
                with urllib.request.urlopen(req, timeout=15, context=_SSL) as r:
                    if r.status == 200 and "image" in (r.headers.get("Content-Type") or ""):
                        found.append(c)
            except Exception:
                pass
            time.sleep(delay)
        if found:
            try:
                d = json.load(open(self.cache_path, encoding="utf-8"))
            except Exception:
                d = {}
            d["t"] = sorted(set(d.get("t") or []) | set(found))
            json.dump(d, open(self.cache_path, "w", encoding="utf-8"))
            self._apply(d)
        return len(found)

    def art(self, cls):
        """["x", id, kind] for the page, None when they have no icon for this class."""
        hit = self.ids.get((cls or "").lower())
        return ["x", hit[1], hit[0]] if hit else None

    def payload(self):
        return {"base": self.BASE, "v": self.ver} if self.ok else None


class BlueprintIndex:
    """Every crafting blueprint in the game, from the SC-Wiki API's extraction of the
    game data (/api/v2/blueprints — 1,606 rows in 4.10). Kept by the OUTPUT item's class
    id, so an owned blueprint matches whatever a language pack has renamed the item to.

    The cache holds only what the page needs (key, name, class, default-availability,
    number of unlocking missions, category id) — ~150 KB rather than the ~1.5 MB the
    API returns with every ingredient list attached."""

    API = "https://api.star-citizen.wiki/api/v2/blueprints?limit=200&page="

    def __init__(self, cache=None):
        self.cache_path = cache or os.path.join(SCRIPT_DIR, "sc_blueprints.json")
        self.rows = []
        self.version = None
        self.ok = False
        self.fetched = None

    def load(self, allow_fetch=True, refresh_stale=False, force=False):
        fetched = None
        if os.path.isfile(self.cache_path):
            try:
                d = json.load(open(self.cache_path, encoding="utf-8"))
                self.rows, self.version, fetched = d.get("rows") or [], d.get("version"), d.get("fetched")
            except Exception:
                self.rows = []
        self.fetched = fetched
        if allow_fetch and (not self.rows or force or (refresh_stale and cache_is_stale(fetched))):
            before = self.rows
            self._fetch()                       # keeps the cached rows if the API is down
            if self.rows is not before:
                self.fetched = datetime.date.today().isoformat()
        self.ok = bool(self.rows)
        return self.ok

    def _fetch(self):
        try:
            rows, page, last, ver = [], 1, 1, None
            while page <= last:
                req = urllib.request.Request(self.API + str(page), headers=_UA)
                d = json.loads(urllib.request.urlopen(req, timeout=40, context=_SSL).read())
                for b in d.get("data", []):
                    name = b.get("output_name")
                    cls = (b.get("output_class") or "").lower()
                    # three rows have no name, and CIG's own test/placeholder entries are
                    # in the data too — none of those is a blueprint anyone can be missing
                    if not name or not cls or re.search(r"placeholder|test #", name, re.I):
                        continue
                    ver = ver or b.get("game_version")
                    rows.append({"k": b.get("key"), "n": name.replace("\xa0", " ").strip(),
                                 "c": cls, "d": bool(b.get("is_available_by_default")),
                                 "m": int(b.get("unlocking_missions_count") or 0),
                                 "g": (b.get("category_uuid") or "")[:8],
                                 "_l": b.get("link")})
                last = d.get("meta", {}).get("last_page", 1)
                page += 1
            # The list endpoint never includes the unlocking missions; only the detail
            # does. ~700 blueprints carry missions, one call each (~0.13 s), so they are
            # fetched eight at a time. A row keeps "u": [[debug name, chance, title]].
            self._fill_unlocks([r for r in rows if r["m"] and r.get("_l")])
            for r in rows:
                r.pop("_l", None)
            if rows:
                self.rows, self.version = rows, ver
                json.dump({"version": ver, "fetched": datetime.date.today().isoformat(), "rows": rows},
                          open(self.cache_path, "w", encoding="utf-8"))
        except Exception as e:
            print(f"[sc_names] blueprint catalogue fetch failed: {e}")

    @staticmethod
    def _fill_unlocks(rows):
        import concurrent.futures

        def one(r):
            try:
                req = urllib.request.Request(r["_l"] + "?include=unlocking_missions", headers=_UA)
                d = json.loads(urllib.request.urlopen(req, timeout=40, context=_SSL).read()).get("data", {})
                r["u"] = [[m.get("debug_name") or "", round(float(m.get("chance") or 0), 3), m.get("title") or ""]
                          for m in (d.get("unlocking_missions") or []) if m.get("debug_name")]
            except Exception:
                r["u"] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(one, rows))


class MissionIndex:
    """Every mission the SC-Wiki API lists (/api/v2/missions, ~1,800 rows), kept by its
    debug name — the same string the client log writes as the contract class. Holds what
    a blueprint planner needs: faction, the standing a mission needs and its reputation
    threshold, the reputation a completion gives, and each faction's rank ladder.

    Rows: [debug, title, faction, rank index, standing name, standing threshold,
           reputation amount, [[faction, amount] …], has blueprints?, [systems], url]."""

    API = "https://api.star-citizen.wiki/api/v2/missions?limit=200&page="
    FACTIONS = "https://api.star-citizen.wiki/api/v2/factions?limit=200&page="

    def __init__(self, cache=None):
        self.cache_path = cache or os.path.join(SCRIPT_DIR, "sc_missions.json")
        self.rows = []
        self.factions = {}
        self.version = None
        self.ok = False
        self.fetched = None

    def load(self, allow_fetch=True, refresh_stale=False, force=False):
        fetched = None
        if os.path.isfile(self.cache_path):
            try:
                d = json.load(open(self.cache_path, encoding="utf-8"))
                self.rows, self.factions = d.get("rows") or [], d.get("factions") or {}
                self.version, fetched = d.get("version"), d.get("fetched")
            except Exception:
                self.rows = []
        self.fetched = fetched
        if allow_fetch and (not self.rows or force or (refresh_stale and cache_is_stale(fetched))):
            before = self.rows
            self._fetch()
            if self.rows is not before:
                self.fetched = datetime.date.today().isoformat()
        self.ok = bool(self.rows)
        return self.ok

    def _fetch(self):
        try:
            rows, page, last, ver, flinks = [], 1, 1, None, {}
            while page <= last:
                req = urllib.request.Request(self.API + str(page), headers=_UA)
                d = json.loads(urllib.request.urlopen(req, timeout=40, context=_SSL).read())
                for m in d.get("data", []):
                    dbg = m.get("debug_name")
                    if not dbg:
                        continue
                    ver = ver or m.get("game_version")
                    fac = (m.get("faction") or {}).get("name") or ""
                    if fac and (m.get("faction") or {}).get("link"):
                        flinks[fac] = m["faction"]["link"]
                    ms = m.get("min_standing") or {}
                    gains = [[g.get("faction") or "", int(g.get("amount") or 0)]
                             for g in (m.get("reputation_gained") or []) if g.get("amount")]
                    rows.append([dbg, (m.get("title") or "").strip(), fac, m.get("rank_index"),
                                 ms.get("name") or "", int(ms.get("min_reputation") or 0),
                                 int(m.get("reputation_amount") or 0), gains,
                                 bool(m.get("has_blueprints")),
                                 [x.get("name") if isinstance(x, dict) else str(x) for x in (m.get("star_systems") or [])],
                                 m.get("web_url") or ""])
                last = d.get("meta", {}).get("last_page", 1)
                page += 1
            factions = self._ladders(flinks)
            if rows:
                self.rows, self.factions, self.version = rows, factions, ver
                json.dump({"version": ver, "fetched": datetime.date.today().isoformat(),
                           "rows": rows, "factions": factions},
                          open(self.cache_path, "w", encoding="utf-8"))
        except Exception as e:
            print(f"[sc_names] mission index fetch failed: {e}")

    @staticmethod
    def _ladders(flinks):
        """faction name -> {"c": ceiling, "l": [[rank name, min reputation] …]}"""
        import concurrent.futures
        out = {}

        def one(item):
            name, link = item
            try:
                req = urllib.request.Request(link, headers=_UA)
                d = json.loads(urllib.request.urlopen(req, timeout=40, context=_SSL).read()).get("data", {})
                lad = d.get("reputation_ladder") or {}
                steps = [[st.get("display_name") or st.get("name") or "", int(st.get("min_reputation") or 0)]
                         for st in (lad.get("standings") or [])
                         if st.get("display_name") and "PLACEHOLDER" not in st["display_name"]]
                if steps:
                    out[name] = {"c": int(lad.get("reputation_ceiling") or 0), "l": sorted(steps, key=lambda x: x[1])}
            except Exception:
                pass
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(one, list(flinks.items())))
        return out


TOOL_MARKERS = ("multitool", "tractor", "medgun", "scanner", "flashlight",
                "mining", "salvage", "utility", "cutter", "binocular", "beacon")
GUN_MARKERS = ("rifle", "pistol", "shotgun", "_smg", "smg_", "_lmg", "lmg_",
               "sniper", "gatling", "launcher", "revolver", "railgun",
               "scattergun", "ballistic", "energy")
# Things that live on a weapon port but aren't a gun or tool we want to rank.
# NB: is_tool() defaults any non-gun to "tool", so armour/clothing MUST be caught
# here or a flight suit shows up under "Tools & utility".
NOISE_MARKERS = ("harvestable", "prota", "debris", "_ore", "gem", "rmc", "scu",
                 "cargo", "commodity", "helmet", "backpack", "armor",
                 "_core_", "gadget_light", "fuse_", "carryable", "serverblade",
                 "harddrive", "hardrive", "_mag", "magazine", "battery", "ammo",
                 "clip", "food", "drink", "bottle", "ration", "bowl", "berries",
                 "_box", "keycard", "consumable", "medpen", "substitute", "canister",
                 "mount", "gimbal", "missile", "torpedo", "bomb_", "flare", "chaff",
                 "_gren",
                 # armour & clothing (worn, not held) — suit covers flight/under/spacesuit
                 "suit", "shirt", "pants", "jacket", "boots", "gloves", "shoes",
                 "vest", "coat", "dress", "skirt", "mask", "_hat", "hat_", "sock",
                 "glasses", "clothing", "hood", "beanie", "scarf", "belt")


def is_noise(cls):
    # NB: a "none_" prefix is NOT junk — those are real unbranded weapons
    # (none_rifle_multi_01 = Killshot Rifle, none_lmg_ballistic_01 = Pulverizer LMG).
    n = cls.lower()
    return any(t in n for t in NOISE_MARKERS)


# --- weapon classification (for the showcase labels) ----------------------- #
_GUN_TYPE_RULES = (
    ("sniper", "Sniper Rifle"), ("_lmg", "LMG"), ("lmg_", "LMG"),
    ("_smg", "SMG"), ("smg_", "SMG"), ("shotgun", "Shotgun"),
    ("pistol", "Pistol"), ("revolver", "Revolver"), ("railgun", "Railgun"),
    ("launcher", "Launcher"), ("rifle", "Assault Rifle"),
)


def gun_class_labels(cls, name=""):
    """Return (type, ammo) e.g. ('Assault Rifle','Energy (Laser)') for a gun class id.
    The wiki's personal_weapon data when the index has it, else a guess from the class."""
    g = ITEMS.gun(cls) if ITEMS is not None and hasattr(ITEMS, "gun") else None
    if g and g[0]:
        return g[0], g[1] or ""
    n = (cls + " " + (name or "")).lower()
    gtype = ""
    for key, label in _GUN_TYPE_RULES:
        if key in n:
            gtype = label
            break
    if not gtype:
        gtype = "Weapon"
    # "_multi" weapons fire both ballistic and energy (e.g. Killshot) -> Hybrid
    if "_multi" in n or "multi_" in n:
        ammo = "Hybrid"
    elif "energy" in n or "laser" in n or "plasma" in n:
        ammo = "Energy"
    elif "ballistic" in n:
        ammo = "Ballistic"
    else:
        ammo = ""
    return gtype, ammo


def is_tool(cls):
    n = cls.lower()
    if any(t in n for t in TOOL_MARKERS):
        return True
    if any(g in n for g in GUN_MARKERS):
        return False
    return True


# worn-armour body slots — matched on the CLASS id only, so a weapon whose maker is
# "…Arms" (Kastak Arms, Carrion Arms) in its display NAME is never mistaken for arm armour.
_ARMOR_CLASS = ("_legs", "_arms", "_leg_", "_arm_", "_torso", "_core_", "_helmet",
                "backpack", "undersuit", "flightsuit", "spacesuit", "hardsuit",
                "_armor", "_armour", "chestrig")
# Attachments, cosmetics, props & melee. These are neither a rankable gun nor a utility
# tool, so they drop out of the loadout entirely. Matched on class OR resolved name,
# because some only reveal themselves in the wiki name (e.g. "Size 3 fixed mount").
# A class with no gun/tool word falls through to None anyway; this list exists to catch
# the ones that DO carry a gun-ish word ("laser pointer", "Chairman's Club").
_NONWEAPON_HARD = ("pointer", "telescop", "fixed mount", "mobiglas", "casing",
                   "literature", "book", "_mug", "coffee", "melee", " club",
                   "ubarrel", "underbarrel", "_optics_", "optics_holo", "barrel_supp",
                   "_sight", "reflex", "magnifier", "suppressor", "silencer",
                   "compensator", "bipod", "_grip", "gimbal", "rail_attach", "gadget")
# Modules that clip onto a tool, plus their consumable refills — not tools in their own
# right, so they never belong in the loadout ranking (OreBit Mining Attachment,
# Cambio-Lite SRT Canister, ParaMed Refill, mining gadgets…). Matched on the resolved
# NAME first (how the wiki labels them) with class-id patterns as an offline safety net.
_ACCESSORY_NAME = ("attachment", "canister", "refill", "cartridge", "vial", " gadget")
_ACCESSORY_CLASS = ("_resource_", "mining_gadget", "_vial", "_gadget_")


def weapon_kind(cls, name=""):
    """Classify a held item as 'gun', 'tool', or None (drop).

    Fixes the old "anything non-gun is a tool" default that leaked armour pieces,
    weapon attachments (scopes, mounts, laser pointers), mobiGlas and cosmetics into
    the Tools list. Armour is decided from the class id; everything else can match the
    resolved display name too, since some attachments only reveal themselves there
    (e.g. a class that the wiki names "Size 3 fixed mount")."""
    c = (cls or "").lower()
    both = (c + " " + (name or "")).lower()
    if c == "default" or (name or "").strip().lower() == "default":
        return None
    if any(t in c for t in _ARMOR_CLASS):
        return None
    # tool modules / consumables — checked before the tool test, since their class ids
    # legitimately contain tool words ("grin_multitool_01_mining" is the OreBit ATTACHMENT)
    if any(t in (name or "").lower() for t in _ACCESSORY_NAME) or \
            any(t in c for t in _ACCESSORY_CLASS):
        return None
    # A real gun keeps its identity even when the class carries a factory attachment
    # suffix (e.g. volt_smg_energy_01_tint01_optic_001 IS the Volt Energy SMG), so the
    # gun/tool test runs BEFORE the attachment filter. A bare attachment has no gun
    # word in its class and still falls through to None below.
    if any(t in c for t in TOOL_MARKERS) or "extinguisher" in both:
        return "tool"
    if any(g in c for g in GUN_MARKERS) and not any(t in both for t in _NONWEAPON_HARD):
        return "gun"
    return None


# --------------------------------------------------------------------------- #
#  Ships — resolve against the RSI Ship Matrix
# --------------------------------------------------------------------------- #

SHIP_VARIANT = re.compile(
    r"_(collector|indust|industrial|best_in_show|bis|pirate|emerald|harbinger|"
    r"stella|fortuna|solstice|auspicious|firebird|blue|red|platinum|citizencon"
    r"\d*|cc\d*|s42|edition|paint|test|template|modifiers?|military|stealth)\w*", re.I)


# Variants the Ship Matrix doesn't list, mapped to the hull they're built on: by game
# class prefix, and by crew-channel name.
SHIP_ALIAS_CLS = {"ANVL_Hornet_F7_Mk2_Collector": "F7C-M Super Hornet Mk II",  # Wikelo F7
                  "ANVL_Lightning_F8C": "F8C Lightning"}   # PYAM Exec and Executive Edition too
SHIP_ALIAS_NAME = {"f7 hornet mk wikelo": "F7C-M Super Hornet Mk II"}

# Hulls flown in game that the RSI matrix does not list yet, keyed by the exact game class.
# Used only while the loaded matrix has no ship of that name, so a later matrix wins with no
# code change. Source: api.star-citizen.wiki /vehicles (maker); starcitizen.tools (Hyūn name,
# career, role: its infobox disagrees with the API, the page wins).
# size, url and images stay empty: the wiki gives a numeric size class and no art.
SHIP_EXTRA = {
    "DRAK_Marauder": {"name": "Marauder", "man": "Drake Interplanetary", "code": "DRAK",
                      "url": "", "size": "", "type": "Combat", "focus": "Medium Fighter",
                      "img_small": "", "img_large": ""},
    "GAMA_Hyun": {"name": "Hyūn", "man": "Gatac Manufacture", "code": "GAMA",
                  "url": "", "size": "", "type": "Industrial", "focus": "Mining / Salvage",
                  "img_small": "", "img_large": ""},
}

_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}
_MK_RE = re.compile(r"\bmk\.?\s*(iv|iii|ii|i|v)\b", re.I)


def _fold(x):
    # "Hyūn" -> "Hyun": crew-channel names carry no accents
    return unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode()


def _norm(x):
    x = _fold(x)
    # normalise "Mk II" / "Mk2" to a common form so game classes (arabic) match
    # the RSI matrix (roman): "F7C-M Super Hornet Mk II" <-> "Hornet_F7CM_Mk2".
    x = _MK_RE.sub(lambda m: "mk" + _ROMAN[m.group(1).lower()], x.lower())
    return re.sub(r"[^a-z0-9]", "", x)


# A cache that is never refreshed goes quietly wrong: the ship matrix copied into the
# 4.10 release was 83 days old by the time 4.10.2 added the Constellation Mk V
# Centaurus, and the fuzzy matcher filed the new hull under the nearest old name.
# Caches that carry a "fetched" date are STALE after this many days. Staleness is
# only reported at boot (an update is the player's call — the blueprint catalogue
# takes about two minutes); load(refresh_stale=True) or load(force=True) re-pulls,
# and the old copy stays in use if the fetch fails.
CACHE_MAX_DAYS = 14


def cache_is_stale(fetched):
    """True when `fetched` (ISO date or None) is missing or older than CACHE_MAX_DAYS."""
    if not fetched:
        return True
    try:
        return (datetime.date.today() - datetime.date.fromisoformat(fetched)).days > CACHE_MAX_DAYS
    except (TypeError, ValueError):
        return True


class ShipIndex:
    """Loads the RSI Ship Matrix and maps game class ids -> ship records."""

    MATRIX_URL = "https://robertsspaceindustries.com/ship-matrix/index"

    def __init__(self, cache=None):
        self.cache_path = cache or os.path.join(SCRIPT_DIR, "sc_ship_matrix.json")
        self.by_code = {}
        self.by_name = {}
        self.extra = {}         # SHIP_EXTRA entries in use
        self._resolved = {}     # class -> record cache
        self.ok = False
        self.fetched = None

    def load(self, allow_fetch=True, refresh_stale=False, force=False):
        data, fetched = None, None
        if os.path.isfile(self.cache_path):
            try:
                with open(self.cache_path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                # {"fetched": date, "ships": [...]} since parser 17; a bare list before
                data, fetched = (raw.get("ships"), raw.get("fetched")) if isinstance(raw, dict) else (raw, None)
            except Exception:
                data = None
        self.fetched = fetched
        if allow_fetch and (data is None or force or (refresh_stale and cache_is_stale(fetched))):
            new = self._fetch()
            if new:
                data, self.fetched = new, datetime.date.today().isoformat()
        if not data:
            return False
        self.by_code, self.by_name, self._resolved = {}, {}, {}
        for s in data:
            s["name"] = (s.get("name") or "").strip()   # RSI data has stray spaces
            self.by_code.setdefault(s["code"], []).append(s)
            self.by_name[s["name"]] = s
        # hulls the matrix lacks; exact class only, so suffixed classes never match them
        listed = {_norm(n) for n in self.by_name}
        self.extra = {c: dict(r) for c, r in SHIP_EXTRA.items()
                      if _norm(r["name"]) not in listed
                      and _norm(r["man"] + r["name"]) not in listed}
        for r in self.extra.values():
            self.by_name[r["name"]] = r
        self.ok = True
        return True

    def record_by_name(self, name):
        return self.by_name.get(name)

    def _fetch(self):
        try:
            req = urllib.request.Request(self.MATRIX_URL, headers=_UA)
            raw = urllib.request.urlopen(req, timeout=30, context=_SSL).read()
            ships = json.loads(raw)["data"]
            out = []
            for s in ships:
                man = s.get("manufacturer") or {}
                img = {}
                for m in (s.get("media") or []):
                    img = m.get("images") or {}
                    if img:
                        break
                url = s.get("url") or ""
                if url and url.startswith("/"):
                    url = "https://robertsspaceindustries.com" + url
                out.append({
                    "name": s.get("name"), "man": man.get("name"),
                    "code": man.get("code"), "url": url,
                    "size": s.get("size"), "type": s.get("type"),
                    "focus": s.get("focus"),
                    "img_small": (img.get("product_thumb_medium_and_small")
                                  or img.get("store_hub_small") or img.get("store_small")),
                    "img_large": img.get("store_large") or img.get("store_hub_large"),
                })
            json.dump({"fetched": datetime.date.today().isoformat(), "ships": out},
                      open(self.cache_path, "w", encoding="utf-8"))
            return out
        except Exception as e:
            print(f"[sc_names] ship matrix fetch failed: {e}")
            return None

    @staticmethod
    def _best(cands, tokens, strict=False):
        if not cands or not tokens:
            return None
        target = _norm("".join(tokens))
        best, bestscore = None, -1
        for c in cands:
            nn = _norm(c["name"] or "")
            score = sum(1 for t in tokens if _norm(t) and _norm(t) in nn)
            if target and target in nn:
                score += 2
            score -= abs(len(nn) - len(target)) * 0.02
            if score > bestscore:
                bestscore, best = score, c
        if best is None or bestscore <= 0:
            return None
        # cross-manufacturer fallback must actually contain the model name,
        # so junk like "Default" never latches onto a random ship
        if strict and not (target and target in _norm(best["name"] or "")):
            return None
        return best

    def match(self, cls):
        if cls in self._resolved:
            return self._resolved[cls]
        alias = next((n for pre, n in SHIP_ALIAS_CLS.items() if cls.startswith(pre)), None)
        if alias in self.by_name:
            self._resolved[cls] = self.by_name[alias]
            return self.by_name[alias]
        base = SHIP_VARIANT.sub("", cls)
        parts = base.split("_")
        code, tokens = parts[0], [p for p in parts[1:] if p]
        rec = self._best(self.by_code.get(code, []), tokens)
        if rec is None:
            # game manufacturer code differs from the matrix (Fury MISC->MRAI,
            # Nox XIAN->XNAA, Stinger VNCL->ESPR): match the model across all ships
            rec = self._best(list(self.by_name.values()), tokens, strict=True)
        if rec is None:
            rec = self.extra.get(base)      # hull the matrix doesn't list
        self._resolved[cls] = rec
        return rec

    def match_display(self, name):
        """Record for a ship named the way the game shows it in a crew channel
        ('Anvil C8R Pisces Rescue', 'RSI Ursa Medivac'): the longest matrix name found
        in it as whole words, so 'Ursa Medivac' wins over 'Ursa'."""
        key = "@" + name
        if key in self._resolved:
            return self._resolved[key]
        low, best = " " + re.sub(r"\s+", " ", _fold(name).lower()) + " ", None
        alias = next((n for ph, n in SHIP_ALIAS_NAME.items() if ph in low), None)
        if alias in self.by_name:
            self._resolved[key] = self.by_name[alias]
            return self.by_name[alias]
        for n, rec in self.by_name.items():
            if n and f" {_fold(n).lower()} " in low and (best is None or len(n) > len(best["name"])):
                best = rec
        if best is None:
            # variants the matrix doesn't list ('Origin 600i', 'Drake Dragonfly Star
            # Kitten'): among ships whose model word is in the name, most shared words
            # wins, then the shortest name (the base hull, as the class route picks)
            words = set(low.split())
            makers = {w.lower() for r in self.by_name.values()
                      for w in ((r.get("man") or "").split()[:1] + [r.get("code") or ""]) if w}
            cands = [r for n, r in self.by_name.items()
                     if n and n.split()[0].lower() in words - makers]
            best = min(cands, default=None, key=lambda r: (
                -len(words & set(r["name"].lower().split())), len(r["name"])))
        self._resolved[key] = best
        return best

    def display(self, cls):
        rec = self.match(cls)
        if rec:
            return rec["name"], rec["man"], rec
        # fallback: manufacturer code -> name if known, else prettified
        code = cls.split("_")[0]
        man = next((c[0]["man"] for c in [self.by_code.get(code)] if c), None)
        pretty = cls.replace("_", " ")
        return pretty, man or "", None


# --------------------------------------------------------------------------- #
#  Image download -> data URI (for embedding, fully self-contained page)
# --------------------------------------------------------------------------- #

def fetch_data_uri(url, timeout=25, max_bytes=900_000):
    if not url:
        return None
    try:
        import base64
        req = urllib.request.Request(url, headers=_UA)
        r = urllib.request.urlopen(req, timeout=timeout, context=_SSL)
        raw = r.read()
        if len(raw) > max_bytes:        # keep the embedded page reasonable
            return None
        # trust the server's real MIME (handles svg+xml, webp, png, jpeg) — guessing
        # from the URL extension mislabels SVG/WebP thumbnails and breaks the image.
        ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not ct.startswith("image/"):
            low = url.lower()
            ct = ("image/svg+xml" if low.endswith(".svg")
                  else "image/jpeg" if low.endswith((".jpg", ".jpeg"))
                  else "image/webp" if low.endswith(".webp") else "image/png")
        return f"data:{ct};base64," + base64.b64encode(raw).decode("ascii")
    except Exception:
        return None


def fetch_profile(handle):
    """Scrape the public RSI citizen dossier for career-page context."""
    if not handle:
        return None
    try:
        url = "https://robertsspaceindustries.com/citizens/" + urllib.parse.quote(handle)
        req = urllib.request.Request(url, headers=_UA)
        h = urllib.request.urlopen(req, timeout=25, context=_SSL).read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[sc_names] profile fetch failed: {e}")
        return None

    def one(pat, flags=0):
        m = re.search(pat, h, flags)
        return m.group(1).strip() if m else None

    p = {
        "record": one(r'UEE Citizen Record</span>\s*<strong class="value">([^<]+)'),
        "handle": one(r'Handle name</span>\s*<strong class="value">([^<]+)') or handle,
        "enlisted": one(r'Enlisted</span>\s*<strong class="value">([^<]+)'),
        "fluency": one(r'Fluency</span>\s*<strong class="value">\s*([^<]+?)\s*</strong>'),
        "location": one(r'Location</span>\s*<strong class="value">\s*([^<]+?)\s*</strong>'),
        "rank": one(r'<span class="value">([^<]+)</span>'),
        "org_name": None, "org_sid": None, "org_rank": None,
    }
    m = re.search(r'-\s*([^|]+?)\s*\|\s*([A-Z0-9]+)\s*\(([^)]+)\)\s*-\s*Roberts Space Industries', h)
    if m:
        p["org_name"], p["org_sid"], p["org_rank"] = m.group(1).strip(), m.group(2), m.group(3)
    av = one(r'<div class="thumb">\s*<img src="([^"]+)"')
    if av and av.startswith("/"):
        av = "https://robertsspaceindustries.com" + av
    p["avatar"] = fetch_data_uri(av) if av else None
    return p


def fetch_orgs(handle):
    """Scrape the citizen Organizations tab: main + affiliate orgs (public)."""
    if not handle:
        return []
    try:
        url = "https://robertsspaceindustries.com/citizens/%s/organizations" % urllib.parse.quote(handle)
        h = urllib.request.urlopen(urllib.request.Request(url, headers=_UA),
                                   timeout=25, context=_SSL).read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[sc_names] orgs fetch failed: {e}")
        return []
    orgs = []
    for b in re.split(r'<div class="box-content org ', h)[1:]:
        vis = re.search(r"visibility-(\w)", b)
        v = vis.group(1) if vis else "V"
        sid = re.search(r"/orgs/([A-Z0-9]+)", b)
        members = re.search(r"([\d,]+)\s*members", b)
        vals = re.findall(r'class="value">([^<]+)</', b)
        kind = "main" if b.startswith("main") else "affiliate"
        rank = ""
        # main org: vals = [name, sid, rank, 'Organization']; rank at idx 2
        if kind == "main" and len(vals) >= 3:
            rank = vals[2]
        if v != "V" or not sid:
            orgs.append({"sid": "REDACTED", "name": "Redacted", "kind": kind,
                         "members": None, "rank": "", "logo": None})
            continue
        orgs.append({"sid": sid.group(1), "name": None, "kind": kind,
                     "members": (members.group(1) if members else None),
                     "rank": rank, "logo": None})
    # enrich names + logos from each org page
    for o in orgs:
        if o["sid"] in ("REDACTED", None):
            continue
        try:
            oh = urllib.request.urlopen(urllib.request.Request(
                "https://robertsspaceindustries.com/orgs/" + o["sid"], headers=_UA),
                timeout=20, context=_SSL).read().decode("utf-8", "replace")
            m = re.search(r"<title>\s*([^<\[]+?)\s*\[", oh)
            if m:
                o["name"] = m.group(1).strip()
            lg = re.search(r'<img src="(/media/[^"]+/logo/[^"]+)"', oh)
            if lg:
                o["logo"] = fetch_data_uri("https://robertsspaceindustries.com" + lg.group(1))
        except Exception:
            pass
        if not o["name"]:
            o["name"] = o["sid"]
    return orgs


def fetch_google_fonts_embed(cache=None):
    """Return a CSS block of @font-face rules with the woff2 files inlined as
    data URIs, so the dashboard's HUD typography (Chakra Petch + Share Tech Mono)
    stays fully self-contained.  Cached; only the 'latin' subset is kept (small)."""
    cache = cache or os.path.join(SCRIPT_DIR, "sc_fonts_embed.css")
    if os.path.isfile(cache):
        try:
            return open(cache, encoding="utf-8").read()
        except Exception:
            pass
    url = ("https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;500;700"
           "&family=Share+Tech+Mono&display=swap")
    # a modern browser UA makes Google serve woff2 (not ttf)
    ua = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")}
    try:
        import base64
        css = urllib.request.urlopen(urllib.request.Request(url, headers=ua),
                                     timeout=30, context=_SSL).read().decode("utf-8")
        out = []
        for subset, face in re.findall(r"/\*\s*([\w-]+)\s*\*/\s*(@font-face\s*\{[^}]*\})", css):
            if subset != "latin":                 # latin only keeps the payload tiny
                continue
            m = re.search(r"url\((https://[^)]+\.woff2)\)", face)
            if not m:
                continue
            raw = urllib.request.urlopen(urllib.request.Request(m.group(1), headers=ua),
                                         timeout=30, context=_SSL).read()
            uri = "data:font/woff2;base64," + base64.b64encode(raw).decode("ascii")
            out.append(face.replace(m.group(1), uri))
        css_out = "\n".join(out)
        if css_out:
            open(cache, "w", encoding="utf-8").write(css_out)
        return css_out
    except Exception as e:
        print(f"[sc_names] font embed failed: {e}")
        return ""


def wiki_weapon_image(page_title):
    """Best-effort weapon image from the SC community wiki (RSI has none)."""
    if not page_title:
        return None
    try:
        import urllib.parse
        api = ("https://starcitizen.tools/api.php?action=query&format=json"
               "&prop=pageimages&piprop=thumbnail&pithumbsize=640&redirects=1&titles="
               + urllib.parse.quote(page_title))
        req = urllib.request.Request(api, headers=_UA)
        d = json.loads(urllib.request.urlopen(req, timeout=20, context=_SSL).read())
        pages = d.get("query", {}).get("pages", {})
        for pg in pages.values():
            src = (pg.get("thumbnail") or {}).get("source")
            if src:
                return src
    except Exception:
        pass
    return None


# starcitizen.tools file names say what a picture shows. A card wants the gun on its
# own: the standard skin's shop display, a flat lay, a plain background. Not someone
# aiming it, a roadmap teaser or a WIP render.
_IMG_BAD = re.compile(
    r"\b(aiming|inspecting|shooting|ads|sneak|roadmap|wip|concept|newsletter|dev\d*|"
    r"equipped|bolt from|hanging|advert\w*|citcon|rewards|shot|player|close ?up|vfx|"
    r"in rifle mode|annotated|monthly report|placeholder\w*|fun military|header|fps|"
    r"blurred|various angles|showcase|sub-flair|screenshot|min|keyshot|examine|table|"
    r"mag|ammo box|detail)\b")
# Guns whose pages name no product shot, and whose other files show someone holding
# the gun. Pinned to a wiki file by hand.
GUN_IMG_FILE = {
    "Parallax Energy Assault Rifle": "Volt Rifle Energy 01 UEE01.jpg",
    "Quartz Energy SMG": "Volt SMG Energy 01 UEE01.jpg",
    "Animus Missile Launcher": "APAR-Animus.jpg",
}
# shop displays are framed tight on the receiver: a flat lay shows the whole gun
_IMG_GOOD = (("flat", 4), ("lying", 4), ("laying", 4), ("background", 3), ("cutout", 2),
             ("cut out", 2), ("on crate", 2), ("gun crate", 2), ("standard skin", 2),
             ("shop display", 1), ("render", 1), ("on grey", 2), ("on dark", 2))


def gun_image_score(fname):
    """Rank a wiki file name as a gun card picture. Below zero means never use it."""
    f = " " + re.sub(r"[_]+", " ", (fname or "").lower()) + " "
    if _IMG_BAD.search(f) or f.strip().endswith(".svg"):
        return -1
    s = sum(w for k, w in _IMG_GOOD if k in f)
    if '"' in f or "%22" in f:          # a named skin's picture
        s -= 2
    return s


def wiki_gun_image(page_title, size=640, min_score=1):
    """Thumbnail URL of the best product shot on a gun's starcitizen.tools page, or
    None when nothing scores min_score. A file named after the gun breaks ties. One call."""
    if not page_title:
        return None
    try:
        import urllib.parse
        api = ("https://starcitizen.tools/api.php?action=query&format=json&redirects=1"
               "&generator=images&gimlimit=500&prop=imageinfo&iiprop=url|size"
               "&iiurlwidth=" + str(size) + "&titles=" + urllib.parse.quote(page_title))
        req = urllib.request.Request(api, headers=_UA)
        d = json.loads(urllib.request.urlopen(req, timeout=20, context=_SSL).read())
        model = (page_title.split() or [""])[0].lower()
        best, best_s = None, -1
        for pg in sorted((d.get("query") or {}).get("pages", {}).values(),
                         key=lambda p: p.get("title", "")):
            ii = (pg.get("imageinfo") or [{}])[0]
            if not ii.get("thumburl") or (ii.get("width") or 0) < 600 \
                    or (ii.get("width") or 0) < (ii.get("height") or 0):
                continue                       # tiny or portrait: never a good card
            fname = pg.get("title", "")[5:]
            s = gun_image_score(fname)
            if s >= 0 and model and model in fname.lower():
                s += 0.5
            if s > best_s:
                best, best_s = ii["thumburl"], s
        return best if best_s >= min_score else None
    except Exception:
        return None


def wiki_file_logo(filename, size=320):
    """Rendered PNG thumbnail URL of a SPECIFIC wiki File: — used to pin a maker
    to a chosen logo variant, and more robust than embedding a raw SVG (some SVGs,
    e.g. Volt's, render as a black block when inlined but render fine as a PNG)."""
    if not filename:
        return None
    try:
        import urllib.parse
        api = ("https://starcitizen.tools/api.php?action=query&format=json"
               "&prop=imageinfo&iiprop=url&iiurlwidth=" + str(size) +
               "&titles=" + urllib.parse.quote("File:" + filename))
        req = urllib.request.Request(api, headers=_UA)
        d = json.loads(urllib.request.urlopen(req, timeout=20, context=_SSL).read())
        for pg in d.get("query", {}).get("pages", {}).values():
            if "missing" in pg:
                return None
            ii = (pg.get("imageinfo") or [{}])[0]
            return ii.get("thumburl") or ii.get("url")
    except Exception:
        pass
    return None


def wiki_manufacturer_logo(name, size=200):
    """The manufacturer's logo (its wiki page's lead image), as a thumbnail URL.
    The SC community wiki keeps standardised `Sc-logo-*.svg` logos for nearly every
    manufacturer, rendered to a crisp PNG at the requested size."""
    if not name:
        return None
    try:
        import urllib.parse
        api = ("https://starcitizen.tools/api.php?action=query&format=json"
               "&prop=pageimages&piprop=thumbnail&pithumbsize=" + str(size) +
               "&redirects=1&titles=" + urllib.parse.quote(name))
        req = urllib.request.Request(api, headers=_UA)
        d = json.loads(urllib.request.urlopen(req, timeout=20, context=_SSL).read())
        for pg in d.get("query", {}).get("pages", {}).values():
            if "missing" in pg:
                return None
            src = (pg.get("thumbnail") or {}).get("source")
            if src:
                return src
    except Exception:
        pass
    return None
