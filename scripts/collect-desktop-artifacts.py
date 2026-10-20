"""Sammelt den Windows-Installer und das Velopack-Updatepaket des Smart Systems.

Velopack ist der einzige Windows-Installer **und** Update-Weg. Damit bestehende
v5.5.1-Installationen (die noch `tauri-plugin-updater` nutzen) automatisch auf
Velopack migriert werden, wird `MauntingSmartSystem-Setup.exe` zusätzlich mit
`tauri signer` signiert und in `latest.json` unter `platforms.windows-x86_64`
referenziert. Ab der ersten Velopack-Version nutzt der Client dann direkt
`releases.mss.json` samt `.sig`.

* `MauntingSmartSystem-Setup.exe` (+ `.sig`) — Velopacks eigener Installer,
* `releases.mss.json` samt `.sig` — der signierte Update-Feed,
* `MauntingSmartSystem-<Version>-full.nupkg` — das Paket dazu,
* `latest.json` — Versionsauskunft für **Android** (`updater_core::android`)
  sowie einmalige Migrationsbrücke (`platforms.windows-x86_64`) für v5.5.1.

Aufruf: python scripts/collect-desktop-artifacts.py <tag>
"""

import datetime
import json
import shutil
import sys
from pathlib import Path

REPO = "einmalmaik/maunting-server-manager"
INSTALLER = "MauntingSmartSystem-Setup.exe"
desktop_dir = Path("desktop-release")
desktop_dir.mkdir(parents=True, exist_ok=True)
tag = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "v4.0.0"

release_dir = Path("smart-system/src-tauri/target/release")
velopack_dir = Path("smart-system-velopack")
clean_version = tag.lstrip("v")

# 1. Kein zweiter Installer: ein Tauri-NSIS-Paket daneben wäre wieder eine
#    Installation, die Velopacks UpdateManager nicht aktualisieren kann.
nsis = sorted(release_dir.glob("bundle/nsis/*.exe"))
if nsis:
    sys.exit(
        "Tauri-NSIS-Installer gefunden "
        f"({[p.name for p in nsis]}): Velopack ist der einzige Windows-Installer. "
        "In tauri.conf.json muss bundle.targets nur \"app\" nennen."
    )

# 2. Das signierte Velopack-Feedpaar samt mindestens einem Updatepaket und der
#    signierten Velopack-Setup.exe ist Pflicht.
feed = velopack_dir / "releases.mss.json"
feed_signature = velopack_dir / "releases.mss.json.sig"
packages = sorted(velopack_dir.glob("MauntingSmartSystem-*-full.nupkg"))
# vpk 1.2.161 nennt sie `<PackId>-<Kanal>-Setup.exe` (z. B.
# `MauntingSmartSystem-mss-Setup.exe`), aeltere Fassungen `*-win-Setup.exe`.
setup_installer = sorted(velopack_dir.glob("*-Setup.exe"))
for required in (feed, feed_signature):
    if not required.is_file():
        sys.exit(f"Signierter Velopack-Feed fehlt: {required}")
if not packages:
    sys.exit(f"Velopack-Vollpaket fehlt: {velopack_dir}/MauntingSmartSystem-*-full.nupkg")
if len(setup_installer) != 1:
    sys.exit(f"Genau ein Velopack-Installer erwartet, gefunden: {[p.name for p in setup_installer]}")

setup_signature = setup_installer[0].with_name(f"{setup_installer[0].name}.sig")
if not setup_signature.is_file():
    sys.exit(f"Signatur für Velopack-Setup fehlt: {setup_signature}")
sig_content = setup_signature.read_text(encoding="utf-8").strip()
if not sig_content:
    sys.exit(f"Signatur für Velopack-Setup ist leer: {setup_signature}")

# 3. Feed, Paket und Installer übernehmen; der Installer bekommt den festen
#    Namen, den Release und Doku nennen.
for f in velopack_dir.glob("*"):
    if f.is_file() and f not in (setup_installer[0], setup_signature):
        shutil.copy2(f, desktop_dir / f.name)
        print(f"Copied Velopack artifact: {f.name}")

shutil.copy2(setup_installer[0], desktop_dir / INSTALLER)
shutil.copy2(setup_signature, desktop_dir / f"{INSTALLER}.sig")
print(f"Copied Velopack installer: {setup_installer[0].name} -> {INSTALLER} (+ .sig)")

# 4. Das Hauptprogramm liegt zusätzlich einzeln im Release (Support, Prüfsumme).
main_exe = release_dir / "MauntingSmartSystem.exe"
if main_exe.exists():
    shutil.copy2(main_exe, desktop_dir / "MauntingSmartSystem.exe")
    print("Copied main executable: MauntingSmartSystem.exe")

# 5. `latest.json` versorgt Android (`version`, `notes`) und migriert bestehende
#    v5.5.1-Windows-Installationen über `platforms.windows-x86_64` auf die
#    Velopack-Setup.exe.
manifest = {
    "version": clean_version,
    "notes": f"MSS Update {tag}",
    "pub_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "platforms": {
        "windows-x86_64": {
            "signature": sig_content,
            "url": f"https://github.com/{REPO}/releases/download/{tag}/{INSTALLER}",
        }
    },
}
latest_json_path = desktop_dir / "latest.json"
latest_json_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

written = json.loads(latest_json_path.read_text(encoding="utf-8"))
if written.get("version") != clean_version:
    sys.exit("latest.json enthält eine ungültige MSS-Version.")
if written.get("platforms", {}).get("windows-x86_64", {}).get("signature") != sig_content:
    sys.exit("latest.json enthält nicht die Signatur des Velopack-Installers.")
print(f"Wrote Android + legacy migration manifest latest.json (version: {clean_version})")

print("\n--- Inhalt von desktop-release: ---")
for item in sorted(desktop_dir.iterdir()):
    print(f"  {item.name} ({item.stat().st_size} bytes)")
