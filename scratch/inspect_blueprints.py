import pathlib
import json

blueprints_dir = pathlib.Path(r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\backend\blueprints\native")
for p in sorted(blueprints_dir.glob("*.blueprint.json")):
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    health = data.get("health", {})
    recovery = data.get("recovery", {})
    print(f"{p.name}:")
    print(f"  Health: {json.dumps(health)}")
    print(f"  Recovery: {json.dumps(recovery)}")
