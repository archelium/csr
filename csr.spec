# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for CSR — Citizen Service Record.
# Build:  python -m PyInstaller csr.spec --noconfirm
# Output: dist/CSR.exe  (one file, console app that launches the local dashboard)
#
# Bundles the game-data caches so the exe works without re-fetching them
# (ship art + RSI profile still need the network at runtime). Outputs, config
# and refreshed caches are written to %LOCALAPPDATA%\CSR at runtime.

import os
import re

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                 StringTable, VarFileInfo, VarStruct, VSVersionInfo)

# Windows version info (Properties -> Details). An exe with no product, company or
# version reads as anonymous to antivirus heuristics. The version comes from
# sc_stats.py so the two can't drift.
VERSION = re.search(r'^CSR_VERSION = "([^"]+)"', open("sc_stats.py", encoding="utf-8").read(), re.M).group(1)
_nums = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_nums, prodvers=_nums, mask=0x3F, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "archelium"),
            StringStruct("FileDescription", "CSR — Citizen Service Record"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "CSR"),
            StringStruct("LegalCopyright", "Copyright (c) 2026 archelium. MIT License."),
            StringStruct("OriginalFilename", "CSR.exe"),
            StringStruct("ProductName", "CSR — Citizen Service Record"),
            StringStruct("ProductVersion", VERSION),
            StringStruct("Comments", "Reads your Star Citizen logs into a career dashboard. "
                                     "Fan-made, not affiliated with CIG."),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

datas = []
for f in ("sc_ship_matrix.json", "sc_item_names.json", "sc_blueprints.json", "sc_missions.json", "sc_place_names.json", "sc_fonts_embed.css"):
    if os.path.isfile(f):
        datas.append((f, "."))

a = Analysis(
    ["sc_stats.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=["sc_names"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # the folder picker now shells out to PowerShell, so Tcl/Tk isn't needed —
    # excluding tkinter drops ~3-4 MB from the exe
    excludes=["matplotlib", "numpy", "PIL", "pandas",
              "tkinter", "_tkinter", "tkinter.filedialog", "Tkinter", "turtle"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CSR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,             # packed exes are a common antivirus flag. Keep it off
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # show the server log window; Ctrl+C stops it
    disable_windowed_traceback=False,
    icon="csr.ico" if os.path.isfile("csr.ico") else None,
    version=version_info,
)
