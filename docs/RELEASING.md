# Releasing CSR

Developer checklist. The download zip only ships the user docs.

## 1. Version

Set the version in `sc_stats.py` (`CSR_VERSION`) and `make_release.py` (`VERSION`), and
date the changelog section. `csr.spec` reads the version from `sc_stats.py` for the exe's
Windows details.

## 2. Build

```
py -3 -m unittest discover -s tests
python -m PyInstaller csr.spec --noconfirm --clean
python make_release.py
```

`make_release.py` writes `CSR-vX.Y.Z.zip`, prints the SHA-256 of `CSR.exe` and the zip with
their VirusTotal links, and saves the hashes to `CSR-vX.Y.Z.sha256.txt`.

Check `dist\CSR.exe` → right-click → **Properties → Details**: product name, version and
copyright should all be filled in.

## 3. Scan before publishing

1. Open the VirusTotal link for `CSR.exe`. If it says the file isn't known, upload
   `dist\CSR.exe` at <https://www.virustotal.com>.
2. Wait for all engines to finish. A clean result is 0 detections.
3. For every engine that flags it, send a false-positive report:
   - **Microsoft Defender:** <https://www.microsoft.com/en-us/wdsi/filesubmission>.
     Pick *Software developer*, then *Incorrectly detected as malware*. Attach the exe
     and link the GitHub release.
   - **Other vendors:** each has its own form. A maintained list is at
     <https://github.com/hankhank10/false-positive-malware-reporting>.
4. Microsoft usually clears a false positive within a few days. Publishing first is fine.
   Rescan after the vendors reply.

## 4. Publish

Push `main`, then create the GitHub release with the zip and paste the SHA-256 lines
from `CSR-vX.Y.Z.sha256.txt` under the notes.

## Why these steps

Python apps packed with PyInstaller get flagged more than most, because malware uses
the same packer. What helps: version details in the exe, no UPX compression, reporting
false positives, and code signing.
