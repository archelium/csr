# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for CSR — Citizen Service Record.
# Build:  python -m PyInstaller csr.spec --noconfirm
# Output: dist/CSR.exe  (one file, console app that launches the local dashboard)
#
# Bundles the game-data caches so the exe works without re-fetching them
# (ship art + RSI profile still need the network at runtime). Outputs, config
# and refreshed caches are written to %LOCALAPPDATA%\CSR at runtime.

import os

datas = []
for f in ("sc_ship_matrix.json", "sc_item_names.json", "sc_blueprints.json", "sc_fonts_embed.css"):
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # show the server log window; Ctrl+C stops it
    disable_windowed_traceback=False,
    icon="csr.ico" if os.path.isfile("csr.ico") else None,
)
