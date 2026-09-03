import sys
import json
import re
from pathlib import Path

tag = sys.argv[1].lstrip("v") if len(sys.argv) > 1 and sys.argv[1] else "4.0.0"

conf_path = Path("smart-system/src-tauri/tauri.conf.json")
if conf_path.exists():
    data = json.loads(conf_path.read_text(encoding="utf-8"))
    data["version"] = tag
    conf_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

cargo_path = Path("smart-system/src-tauri/Cargo.toml")
if cargo_path.exists():
    content = cargo_path.read_text(encoding="utf-8")
    content = re.sub(r'(^\[package\][\s\S]*?^version\s*=\s*)"[^"]+"', rf'\g<1>"{tag}"', content, flags=re.MULTILINE)
    cargo_path.write_text(content, encoding="utf-8")

print(f"Synchronized smart-system version to {tag}")
