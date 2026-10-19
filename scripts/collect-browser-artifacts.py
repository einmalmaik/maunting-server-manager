"""Sammelt den Windows-Installer des Maunting Secure Browsers für das Release.

Aufruf: python scripts/collect-browser-artifacts.py <tag>

Tauri legt mit `createUpdaterArtifacts` neben den NSIS-Installer dessen
Signatur (`.exe.sig`). Beide kommen unter festem Namen nach `browser-release/`,
dazu `latest-msb.json`, das der Updater des Browsers liest
(`browser/src-tauri/tauri.conf.json`). `latest.json` gehört dem Smart System.
Fehlt etwas, bricht das Skript ab, statt ein Release ohne Update zu bauen.
"""

import datetime
import json
import shutil
import sys
from pathlib import Path

REPO = "einmalmaik/maunting-server-manager"
INSTALLER = "MauntingPontis-Setup.exe"
MAIN_EXE = "MauntingPontis.exe"
MANIFEST = "latest-msb.json"


def main() -> None:
    tag = sys.argv[1]
    release_dir = Path("browser/src-tauri/target/release")
    nsis = release_dir / "bundle/nsis"
    ziel = Path("browser-release")
    ziel.mkdir(parents=True, exist_ok=True)

    installer = sorted(nsis.glob("*-setup.exe"))
    if len(installer) != 1:
        sys.exit(f"Genau ein NSIS-Installer erwartet, gefunden: {[p.name for p in installer]}")
    signatur = installer[0].with_name(installer[0].name + ".sig")
    if not signatur.is_file():
        sys.exit(f"Signatur fehlt: {signatur} (TAURI_SIGNING_PRIVATE_KEY gesetzt?)")

    shutil.copy2(installer[0], ziel / INSTALLER)
    shutil.copy2(signatur, ziel / f"{INSTALLER}.sig")

    main_binary = release_dir / MAIN_EXE
    if main_binary.exists():
        shutil.copy2(main_binary, ziel / MAIN_EXE)
        print(f"Hauptprogramm kopiert: {MAIN_EXE}")

    # Velopack-Artefakte einsammeln (*.nupkg, releases.*.json, Setup.exe)
    velopack_dir = Path("browser-velopack")
    if velopack_dir.exists():
        for f in velopack_dir.glob("*"):
            if f.is_file():
                shutil.copy2(f, ziel / f.name)
                print(f"Copied Velopack artifact: {f.name}")

    manifest = {
        "version": tag.lstrip("v"),
        "notes": f"Maunting Pontis {tag}",
        "pub_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platforms": {
            "windows-x86_64": {
                "signature": signatur.read_text(encoding="utf-8").strip(),
                "url": f"https://github.com/{REPO}/releases/download/{tag}/{INSTALLER}",
            }
        },
    }
    (ziel / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{INSTALLER}, {INSTALLER}.sig und {MANIFEST} für {tag} in {ziel}/")


if __name__ == "__main__":
    main()
