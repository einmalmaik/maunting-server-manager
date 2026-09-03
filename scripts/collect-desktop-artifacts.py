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
for exe in release_dir.glob("bundle/nsis/*.exe"):
    shutil.copy2(exe, desktop_dir / "MauntingSmartSystem-Setup.exe")
    print(f"Copied installer: {exe.name} -> MauntingSmartSystem-Setup.exe")

main_exe = release_dir / "MauntingSmartSystem.exe"
if main_exe.exists():
    shutil.copy2(main_exe, desktop_dir / "MauntingSmartSystem.exe")
    print("Copied main executable: MauntingSmartSystem.exe")

# 2. Updater-Dateien einsammeln (*.sig, *.zip, etc.)
for f in release_dir.rglob("*"):
    if f.is_file() and (f.suffix in [".sig", ".zip"] or f.name.endswith(".nsis.zip")):
        shutil.copy2(f, desktop_dir / f.name)
        print(f"Copied updater file: {f.name}")

# Falls eine .sig für das Setup existiert, auch als MauntingSmartSystem-Setup.exe.sig bereitstellen
for sig in desktop_dir.glob("*.sig"):
    if "setup" in sig.name.lower():
        shutil.copy2(sig, desktop_dir / "MauntingSmartSystem-Setup.exe.sig")
        print(f"Linked signature: {sig.name} -> MauntingSmartSystem-Setup.exe.sig")
        break

# 3. Falls Tauri direkt ein latest.json erzeugt hat, übernehmen
tauri_latest = list(release_dir.rglob("latest.json"))
if tauri_latest:
    shutil.copy2(tauri_latest[0], desktop_dir / "latest.json")
    print(f"Copied Tauri's latest.json from {tauri_latest[0]}")

# 4. latest.json sicherstellen (entweder von Tauri oder generiert)
latest_json_path = desktop_dir / "latest.json"
if not latest_json_path.exists():
    sig_files = list(desktop_dir.glob("*.sig"))
    zip_files = list(desktop_dir.glob("*.zip"))
    
    sig_content = ""
    target_url_file = "MauntingSmartSystem-Setup.exe"
    
    if zip_files:
        target_url_file = zip_files[0].name
        # Finde passende sig
        matching_sigs = [s for s in sig_files if target_url_file in s.name]
        sig_file = matching_sigs[0] if matching_sigs else (sig_files[0] if sig_files else None)
        if sig_file:
            sig_content = sig_file.read_text(encoding="utf-8").strip()
    elif sig_files:
        sig_content = sig_files[0].read_text(encoding="utf-8").strip()
        target_url_file = "MauntingSmartSystem-Setup.exe"

    if sig_content:
        manifest = {
            "version": tag,
            "notes": f"MSS Update {tag}",
            "pub_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "platforms": {
                "windows-x86_64": {
                    "signature": sig_content,
                    "url": f"https://github.com/einmalmaik/maunting-server-manager/releases/download/{tag}/{target_url_file}"
                }
            }
        }
        latest_json_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Generated manifest latest.json pointing to {target_url_file}")
    else:
        print("Warning: No signature found to create latest.json")

print("\n--- Inhalt von desktop-release: ---")
for item in desktop_dir.iterdir():
    print(f"  {item.name} ({item.stat().st_size} bytes)")
