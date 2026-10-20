"""Sammelt den Windows-Installer und das Velopack-Updatepaket des Browsers.

Velopack ist der einzige Windows-Installer **und** Update-Weg. Damit bestehende
v5.5.1-Installationen (die noch `tauri-plugin-updater` nutzen) automatisch auf
Velopack migriert werden, wird `MauntingPontis-Setup.exe` zusätzlich mit
`tauri signer` signiert und in `latest-msb.json` unter `platforms.windows-x86_64`
referenziert. Ab der ersten Velopack-Version nutzt der Client dann direkt
`releases.pontis.json` samt `.sig`.

* `MauntingPontis-Setup.exe` (+ `.sig`) — Velopacks eigener Installer,
* `releases.pontis.json` samt `.sig` — der signierte Update-Feed,
* `Pontis-<Version>-full.nupkg` — das Paket dazu,
* `latest-msb.json` — Versionsauskunft für **Android** (`updater_core::android`)
  sowie einmalige Migrationsbrücke (`platforms.windows-x86_64`) für v5.5.1.

Aufruf: python scripts/collect-browser-artifacts.py <tag>
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
    clean_version = tag.lstrip("v")
    release_dir = Path("browser/src-tauri/target/release")
    velopack_dir = Path("browser-velopack")
    ziel = Path("browser-release")
    ziel.mkdir(parents=True, exist_ok=True)

    # Kein zweiter Installer: ein Tauri-NSIS-Paket daneben wäre wieder eine
    # Installation, die Velopack nicht aktualisieren kann.
    nsis = release_dir / "bundle/nsis"
    if nsis.is_dir() and sorted(nsis.glob("*.exe")):
        sys.exit(
            f"Tauri-NSIS-Installer gefunden ({[p.name for p in sorted(nsis.glob('*.exe'))]}): "
            "Velopack ist der einzige Windows-Installer. In tauri.conf.json muss "
            "bundle.targets nur \"app\" nennen."
        )

    # Das signierte Pontis-Feedpaar samt mindestens einem Updatepaket und der
    # signierten Velopack-Setup.exe ist Pflicht.
    feed = velopack_dir / "releases.pontis.json"
    feed_signature = velopack_dir / "releases.pontis.json.sig"
    packages = sorted(velopack_dir.glob("Pontis-*-full.nupkg"))
    # vpk 1.2.161 nennt sie `<PackId>-<Kanal>-Setup.exe` (z. B.
    # `Pontis-pontis-Setup.exe`), aeltere Fassungen `*-win-Setup.exe`.
    setup_installer = sorted(velopack_dir.glob("*-Setup.exe"))
    for required in (feed, feed_signature):
        if not required.is_file():
            sys.exit(f"Signierter Velopack-Feed fehlt: {required}")
    if not packages:
        sys.exit(f"Velopack-Vollpaket fehlt: {velopack_dir}/Pontis-*-full.nupkg")
    if len(setup_installer) != 1:
        sys.exit(f"Genau ein Velopack-Installer erwartet, gefunden: {[p.name for p in setup_installer]}")

    setup_signature = setup_installer[0].with_name(f"{setup_installer[0].name}.sig")
    if not setup_signature.is_file():
        sys.exit(f"Signatur für Velopack-Setup fehlt: {setup_signature}")
    sig_content = setup_signature.read_text(encoding="utf-8").strip()
    if not sig_content:
        sys.exit(f"Signatur für Velopack-Setup ist leer: {setup_signature}")

    for f in velopack_dir.glob("*"):
        if f.is_file() and f not in (setup_installer[0], setup_signature):
            shutil.copy2(f, ziel / f.name)
            print(f"Copied Velopack artifact: {f.name}")

    shutil.copy2(setup_installer[0], ziel / INSTALLER)
    shutil.copy2(setup_signature, ziel / f"{INSTALLER}.sig")
    print(f"Copied Velopack installer: {setup_installer[0].name} -> {INSTALLER} (+ .sig)")

    main_binary = release_dir / MAIN_EXE
    if main_binary.exists():
        shutil.copy2(main_binary, ziel / MAIN_EXE)
        print(f"Copied main executable: {MAIN_EXE}")

    # `latest-msb.json` versorgt Android (`version`, `notes`) und migriert
    # bestehende v5.5.1-Windows-Installationen über `platforms.windows-x86_64`
    # auf die Velopack-Setup.exe.
    manifest = {
        "version": clean_version,
        "notes": f"Maunting Pontis {tag}",
        "pub_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "platforms": {
            "windows-x86_64": {
                "signature": sig_content,
                "url": f"https://github.com/{REPO}/releases/download/{tag}/{INSTALLER}",
            }
        },
    }
    (ziel / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    written = json.loads((ziel / MANIFEST).read_text(encoding="utf-8"))
    if written.get("version") != clean_version:
        sys.exit(f"{MANIFEST} enthält eine ungültige Pontis-Version.")
    if written.get("platforms", {}).get("windows-x86_64", {}).get("signature") != sig_content:
        sys.exit(f"{MANIFEST} enthält nicht die Signatur des Velopack-Installers.")
    print(f"{INSTALLER}, signierter Velopack-Feed und {MANIFEST} für {tag} in {ziel}/")


if __name__ == "__main__":
    main()
