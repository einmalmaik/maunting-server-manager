import os
import glob
import json
import datetime
import sys
import shutil
from pathlib import Path

desktop_dir = Path("desktop-release")
desktop_dir.mkdir(parents=True, exist_ok=True)
tag = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else os.environ.get("TAG", "v4.0.0")

release_dir = Path("smart-system/src-tauri/target/release")

# 1. Installer und Haupt-Executable einsammeln
installers = sorted(release_dir.glob("bundle/nsis/*.exe"))
if len(installers) != 1:
    sys.exit(f"Genau ein NSIS-Installer erwartet, gefunden: {[p.name for p in installers]}")
installer = installers[0]
shutil.copy2(installer, desktop_dir / "MauntingSmartSystem-Setup.exe")
print(f"Copied installer: {installer.name} -> MauntingSmartSystem-Setup.exe")

main_exe = release_dir / "MauntingSmartSystem.exe"
if main_exe.exists():
    shutil.copy2(main_exe, desktop_dir / "MauntingSmartSystem.exe")
    print("Copied main executable: MauntingSmartSystem.exe")

# 2. Updater-Dateien einsammeln (*.sig, *.zip, etc.)
for f in release_dir.rglob("*"):
    if f.is_file() and (f.suffix in [".sig", ".zip"] or f.name.endswith(".nsis.zip")):
        shutil.copy2(f, desktop_dir / f.name)
        print(f"Copied updater file: {f.name}")

setup_signature = installer.with_name(f"{installer.name}.sig")
if not setup_signature.is_file():
    sys.exit(f"Tauri-Setup-Signatur fehlt: {setup_signature}")
shutil.copy2(setup_signature, desktop_dir / "MauntingSmartSystem-Setup.exe.sig")

# 2b. Das signierte MSS-Feedpaar samt mindestens einem Updatepaket ist Pflicht.
velopack_dir = Path("smart-system-velopack")
feed = velopack_dir / "releases.mss.json"
feed_signature = velopack_dir / "releases.mss.json.sig"
packages = sorted(velopack_dir.glob("MauntingSmartSystem-*-full.nupkg"))
for required in (feed, feed_signature):
    if not required.is_file():
        sys.exit(f"Signierter Velopack-Feed fehlt: {required}")
if not packages:
    sys.exit(f"Velopack-Vollpaket fehlt: {velopack_dir}/MauntingSmartSystem-*-full.nupkg")

for f in velopack_dir.glob("*"):
    if f.is_file():
        shutil.copy2(f, desktop_dir / f.name)
        print(f"Copied Velopack artifact: {f.name}")

# 3. Erzeuge den MSS-Updater-Feed immer aus der Signatur genau des Installers.
# Tauri-Signaturen weiterer Dateien (z. B. Velopack-Feed) dürfen nicht dafür
# verwendet werden.
latest_json_path = desktop_dir / "latest.json"
sig_content = setup_signature.read_text(encoding="utf-8").strip()
if not sig_content:
    sys.exit(f"Tauri-Setup-Signatur ist leer: {setup_signature}")
clean_version = tag.lstrip("v")
manifest = {
    "version": clean_version,
    "notes": f"MSS Update {tag}",
    "pub_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "platforms": {
        "windows-x86_64": {
            "signature": sig_content,
            "url": f"https://github.com/einmalmaik/maunting-server-manager/releases/download/{tag}/MauntingSmartSystem-Setup.exe"
        }
    }
}
latest_json_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"Generated manifest latest.json pointing to MauntingSmartSystem-Setup.exe (version: {clean_version})")

# 4. Verifiziere, dass das Manifest ausschließlich die Setup-Signatur verwendet.
written_manifest = json.loads(latest_json_path.read_text(encoding="utf-8"))
if written_manifest.get("platforms", {}).get("windows-x86_64", {}).get("signature") != sig_content:
    sys.exit("latest.json enthält nicht die Signatur des MSS-Installers.")
if written_manifest.get("version") != clean_version:
    sys.exit("latest.json enthält eine ungültige MSS-Version.")

print("\n--- Inhalt von desktop-release: ---")
for item in desktop_dir.iterdir():
    print(f"  {item.name} ({item.stat().st_size} bytes)")
