#!/usr/bin/env python3
"""Assemble the CSR download bundle: the two exes + end-user-friendly docs.

Converts the repo's Markdown docs to clean plain-text .txt (Windows users can
double-click them into Notepad; no raw ## / ** clutter), copies the built exes,
and zips it all. The repo keeps the .md files (they render on GitHub); only the
download bundle ships .txt. `Start Analyzer.bat` is intentionally not included.

Run AFTER build_exe.bat:  python make_release.py
"""
import os
import re
import shutil
import tempfile
import zipfile

VERSION = "1.4.0"
EXES = ["dist/CSR.exe"]
DOCS = {"README.md": "README.txt", "PRIVACY.md": "PRIVACY.txt",
        "DISCLAIMER.md": "DISCLAIMER.txt", "LICENSE": "LICENSE.txt",
        "CHANGELOG.md": "CHANGELOG.txt"}


def md2txt(md):
    md = md.replace("\r\n", "\n")
    md = re.sub(r"\*\*(.+?)\*\*", r"\1", md, flags=re.DOTALL)     # bold (may span lines)
    # italics — after bold, so **x** is already gone and can't be half-matched. The docs
    # are hard-wrapped, so an emphasised phrase often straddles a line break; the match
    # allows single newlines but never a blank line, so a stray "*" in prose can at worst
    # eat to the end of its own paragraph rather than the rest of the file. Refusing to
    # start or end on a space keeps "*.log" and "3 * 4" intact.
    md = re.sub(r"\*(?!\s)((?:[^*\n]|\n(?!\s*\n))+?)(?<!\s)\*", r"\1", md)
    md = re.sub(r"`([^`]+)`", r"\1", md)                          # inline code
    md = re.sub(r"\[([^\]]+)\]\((mailto:)?([^)]+)\)",
                lambda m: m.group(1) if m.group(1) == m.group(3) else f"{m.group(1)} ({m.group(3)})", md)
    out, in_code = [], False
    for ln in md.split("\n"):
        if ln.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            out.append(ln)
            continue
        if re.match(r"^!\[.*\]\(.*\)\s*$", ln):                   # image/badge
            continue
        if re.match(r"^\s*\|?[\s:\-\|]+\|?\s*$", ln) and "-" in ln and "|" in ln:
            continue                                             # table separator
        if ln.strip().startswith("|"):                           # table row
            ln = " — ".join(c.strip() for c in ln.strip().strip("|").split("|") if c.strip())
        h = re.match(r"^(#{1,6})\s+(.*)", ln)
        if h:
            t = h.group(2).strip()
            out += (["", t.upper(), "-" * min(len(t), 60)] if len(h.group(1)) <= 2 else ["", t])
            continue
        if re.match(r"^\s*([-*_])\1{2,}\s*$", ln):               # horizontal rule
            out.append("-" * 60)
            continue
        ln = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", ln)          # italic (line-scoped)
        out.append(ln)
    txt = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"
    return txt.replace("\n", "\r\n")                             # CRLF for Notepad


def main():
    for e in EXES:
        if not os.path.isfile(e):
            raise SystemExit(f"missing {e} — run build_exe.bat first")
    # stage in a temp dir (avoids Google-Drive folder-lock issues); the zip is
    # the deliverable. Also drop an unzipped copy into ./release if writable.
    stage = tempfile.mkdtemp(prefix="csr-rel-")
    names = []
    for e in EXES:
        shutil.copy2(e, os.path.join(stage, os.path.basename(e)))
        names.append(os.path.basename(e))
    for src, dst in DOCS.items():
        if os.path.isfile(src):
            open(os.path.join(stage, dst), "w", encoding="utf-8").write(
                md2txt(open(src, encoding="utf-8").read()))
            names.append(dst)
    names.sort()
    zpath = f"CSR-v{VERSION}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in names:
            z.write(os.path.join(stage, f), f)
    # best-effort unzipped copy for convenience (skip silently if the dir is locked)
    try:
        shutil.rmtree("release", ignore_errors=True)
        os.makedirs("release", exist_ok=True)
        for f in names:
            shutil.copy2(os.path.join(stage, f), os.path.join("release", f))
    except OSError:
        pass
    shutil.rmtree(stage, ignore_errors=True)
    print(f"wrote {zpath}  ({', '.join(names)})")


if __name__ == "__main__":
    main()
