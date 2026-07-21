import pathlib
import json

blueprints_dir = pathlib.Path(r"c:\Users\einma\AppData\Local\Singra\workspace\maunting-server-manager\backend\blueprints\native")

for p in blueprints_dir.glob("*.blueprint.json"):
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    health = data.get("health", {})
    if not health:
        continue

    # Build recovery policies
    policies = []
    
    # Process check
    if "process" in health:
        policies.append({
            "match": "process_not_running",
            "action": "restart"
        })
        
    # Application check
    app = health.get("application", {})
    if app:
        app_type = app.get("type")
        if app_type == "source-query":
            policies.append({
                "match": "source_query_failed",
                "action": "restart"
            })
        elif app_type in ("minecraft-status", "minecraft-query"):
            policies.append({
                "match": "minecraft_status_failed",
                "action": "restart"
            })
            policies.append({
                "match": "minecraft_query_failed",
                "action": "restart"
            })
        elif app_type == "tcp":
            policies.append({
                "match": "tcp_connect_failed",
                "action": "restart"
            })
        elif app_type == "http-ping":
            policies.append({
                "match": "http_request_failed",
                "action": "restart"
            })
            policies.append({
                "match": "http_unexpected_status",
                "action": "restart"
            })

    if policies:
        # Create recovery block
        recovery = {
            "policies": policies,
            "attempt_window_seconds": 600,
            "max_attempts": 3,
            "cooldown_seconds": 60
        }
        
        # Position recovery before backup, or at the end
        backup = data.pop("backup", None)
        data["recovery"] = recovery
        if backup is not None:
            data["backup"] = backup
            
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"Updated {p.name}")
