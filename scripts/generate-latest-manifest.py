import os, glob, json, datetime, sys
from pathlib import Path

desktop_dir = Path("desktop-release")
tag = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else os.environ.get("TAG", "v4.0.0")

# 1. Search if Tauri already created latest.json anywhere in release
if not (desktop_dir / "latest.json").exists():
    tauri_latests = list(Path("smart-system/src-tauri/target/release").glob("**/latest.json"))
    if tauri_latests:
        content = tauri_latests[0].read_text(encoding="utf-8")
        (desktop_dir / "latest.json").write_text(content, encoding="utf-8")
        print(f"Copied latest.json from {tauri_latests[0]}")

# 2. If still missing, generate fallback from .sig and .zip
if not (desktop_dir / "latest.json").exists():
    sig_files = list(desktop_dir.glob("*.sig"))
    zip_files = list(desktop_dir.glob("*.zip"))
    if sig_files and zip_files:
        sig = sig_files[0].read_text(encoding="utf-8").strip()
        zip_name = zip_files[0].name
        data = {
            "version": tag,
            "notes": f"MSS Update {tag}",
            "pub_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "platforms": {
                "windows-x86_64": {
                    "signature": sig,
                    "url": f"https://github.com/einmalmaik/maunting-server-manager/releases/download/{tag}/{zip_name}"
                }
            }
        }
        (desktop_dir / "latest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"Fallback latest.json created for {zip_name}")
    else:
        print(f"No .sig or .zip found: sig_files={sig_files}, zip_files={zip_files}")
