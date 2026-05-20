import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List
import logging
from sqlalchemy.orm import Session
from .shell import get_server_dir, invoke_core_action
from .server_layout import read_config_ini

logger = logging.getLogger(__name__)

# Regular expressions to parse INI lines
_INI_SECTION_RE = re.compile(r"^\[(.*)\]$")
_INI_KEY_RE = re.compile(r"^([^=;#\s]+)\s*=\s*(.*)$")

def parse_ini_file(path: Path) -> Dict[str, Dict[str, str]]:
    """Parses an INI file into a nested dict of section -> key -> value."""
    result: Dict[str, Dict[str, str]] = {}
    if not path.is_file():
        return result
    
    current_section = ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith(";") or line.startswith("#"):
                continue
            section_match = _INI_SECTION_RE.match(line)
            if section_match:
                current_section = section_match.group(1).strip()
                if current_section not in result:
                    result[current_section] = {}
                continue
            
            key_match = _INI_KEY_RE.match(line)
            if key_match:
                key, val = key_match.groups()
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] == '"':
                    val = val[1:-1]
                if current_section not in result:
                    result[current_section] = {}
                result[current_section][key] = val
    except Exception as e:
        logger.error(f"Error parsing INI file {path}: {e}")
    return result

def write_ini_file(path: Path, data: Dict[str, Dict[str, str]]):
    """Writes a nested dict of section -> key -> value back to an INI file."""
    lines = []
    for section, keys in data.items():
        lines.append(f"[{section}]")
        for key, val in keys.items():
            lines.append(f"{key}={val}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")

def scan_pterodactyl_volumes(root_path: str) -> List[Dict[str, Any]]:
    root = Path(root_path)
    candidates = []
    if not root.is_dir():
        logger.warning(f"Pterodactyl volumes directory not found: {root_path}")
        return candidates

    try:
        # Loop through all subdirectories
        for p in root.iterdir():
            if not p.is_dir():
                continue
            
            # Check for game.db or game_0.db at standard Conan locations
            db_candidates = [
                p / "ConanSandbox" / "Saved" / "game.db",
                p / "ConanSandbox" / "Saved" / "game_0.db",
                p / "serverfiles" / "ConanSandbox" / "Saved" / "game.db",
                p / "serverfiles" / "ConanSandbox" / "Saved" / "game_0.db",
            ]
            
            found_db = None
            for db in db_candidates:
                if db.is_file():
                    found_db = db
                    break
                    
            if not found_db:
                continue

            # We found a candidate! Let's get metadata.
            stat = found_db.stat()
            db_size = stat.st_size
            db_modified = stat.st_mtime

            # Attempt to locate config files relative to the found DB
            saved_dir = found_db.parent
            config_dir = saved_dir / "Config" / "LinuxServer"
            if not config_dir.is_dir():
                # Fallback to check if it's WindowsServer or another folder
                config_dir = saved_dir / "Config" / "WindowsServer"
            
            server_settings_path = config_dir / "ServerSettings.ini"
            
            # Read metadata from ServerSettings.ini
            server_name = p.name  # Fallback to directory name
            max_players = 40
            admin_password = "CHANGEME"
            
            if server_settings_path.is_file():
                ini_data = parse_ini_file(server_settings_path)
                # Parse server name
                server_name_val = None
                for section in ini_data.values():
                    if "ServerName" in section:
                        server_name_val = section["ServerName"]
                        break
                if server_name_val:
                    server_name = server_name_val
                
                # Max players
                for section in ini_data.values():
                    if "MaxPlayers" in section:
                        try:
                            max_players = int(section["MaxPlayers"])
                        except ValueError:
                            pass
                        break
                
                # Admin password
                for section in ini_data.values():
                    if "AdminPassword" in section:
                        admin_password = section["AdminPassword"]
                        break

            # Parse mods from modlist.txt
            mods_count = 0
            modlist_candidates = [
                found_db.parent.parent / "Mods" / "modlist.txt",
                found_db.parent.parent.parent / "ConanSandbox" / "Mods" / "modlist.txt",
            ]
            
            found_modlist = None
            for ml in modlist_candidates:
                if ml.is_file():
                    found_modlist = ml
                    break
                    
            if found_modlist:
                try:
                    lines = found_modlist.read_text(encoding="utf-8", errors="replace").splitlines()
                    for line in lines:
                        if line.strip():
                            mods_count += 1
                except Exception as e:
                    logger.error(f"Error reading modlist {found_modlist}: {e}")

            candidates.append({
                "pterodactyl_path": p.as_posix(),
                "volume_name": p.name,
                "server_name": server_name,
                "db_size": db_size,
                "db_modified": db_modified,
                "mods_count": mods_count,
                "max_players": max_players,
                "admin_password": admin_password
            })
    except Exception as e:
        logger.error(f"Error scanning Pterodactyl volumes: {e}")

    return candidates

def migrate_pterodactyl_server(
    pterodactyl_path: str,
    target_server_name: str,
    create_target: bool,
    db_session: Session
) -> dict:
    ptero_dir = Path(pterodactyl_path)
    if not ptero_dir.is_dir():
        raise FileNotFoundError(f"Pterodactyl directory not found: {pterodactyl_path}")

    # Resolve database file
    db_candidates = [
        ptero_dir / "ConanSandbox" / "Saved" / "game.db",
        ptero_dir / "ConanSandbox" / "Saved" / "game_0.db",
        ptero_dir / "serverfiles" / "ConanSandbox" / "Saved" / "game.db",
        ptero_dir / "serverfiles" / "ConanSandbox" / "Saved" / "game_0.db",
    ]
    found_db = None
    for db in db_candidates:
        if db.is_file():
            found_db = db
            break
            
    if not found_db:
        raise FileNotFoundError("Game database file (game.db / game_0.db) not found in candidate directory.")

    # Create target server
    if create_target:
        invoke_core_action("server", "create", target_server_name)
    
    target_dir = get_server_dir(target_server_name)
    if not target_dir.is_dir():
        raise FileNotFoundError(f"Target server directory was not created: {target_dir}")

    # Copy SQLite database
    target_saved_dir = target_dir / "serverfiles" / "ConanSandbox" / "Saved"
    target_saved_dir.mkdir(parents=True, exist_ok=True)
    
    target_db_path = target_saved_dir / "game_0.db"
    shutil.copy2(found_db, target_db_path)

    # Resolve configuration files
    saved_dir = found_db.parent
    config_dir = saved_dir / "Config" / "LinuxServer"
    if not config_dir.is_dir():
        config_dir = saved_dir / "Config" / "WindowsServer"

    target_config_dir = target_saved_dir / "Config" / "LinuxServer"
    target_config_dir.mkdir(parents=True, exist_ok=True)

    # Copy and merge ini files if they exist
    for ini_name in ["ServerSettings.ini", "Engine.ini", "Game.ini"]:
        src_ini = config_dir / ini_name
        dest_ini = target_config_dir / ini_name
        if src_ini.is_file():
            shutil.copy2(src_ini, dest_ini)

    # Read and update config.ini
    config_ini_path = target_dir / "config.ini"
    
    server_name = target_server_name
    admin_password = "CHANGEME"
    max_players = 40
    
    server_settings_path = config_dir / "ServerSettings.ini"
    if server_settings_path.is_file():
        ini_data = parse_ini_file(server_settings_path)
        for section in ini_data.values():
            if "ServerName" in section:
                server_name = section["ServerName"]
            if "AdminPassword" in section:
                admin_password = section["AdminPassword"]
            if "MaxPlayers" in section:
                max_players = section["MaxPlayers"]

    # Write key configurations into config.ini
    if config_ini_path.is_file():
        lines = config_ini_path.read_text(encoding="utf-8", errors="replace").splitlines()
        new_lines = []
        for line in lines:
            if line.startswith("servername="):
                new_lines.append(f'servername="{server_name}"')
            elif line.startswith("adminpassword="):
                new_lines.append(f'adminpassword="{admin_password}"')
            elif line.startswith("maxplayers="):
                new_lines.append(f'maxplayers={max_players}')
            else:
                new_lines.append(line)
        config_ini_path.write_text("\n".join(new_lines), encoding="utf-8")

    # Migrate mods list
    modlist_candidates = [
        found_db.parent.parent / "Mods" / "modlist.txt",
        found_db.parent.parent.parent / "ConanSandbox" / "Mods" / "modlist.txt",
    ]
    found_modlist = None
    for ml in modlist_candidates:
        if ml.is_file():
            found_modlist = ml
            break

    if found_modlist:
        target_mods_dir = target_dir / "serverfiles" / "ConanSandbox" / "Mods"
        target_mods_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found_modlist, target_mods_dir / "modlist.txt")
        
        # Extract mod IDs (digits) from the modlist paths
        mod_ids = []
        try:
            content = found_modlist.read_text(encoding="utf-8", errors="replace")
            mod_ids = re.findall(r'\b\d{8,12}\b', content)
        except Exception as e:
            logger.error(f"Error parsing mods for workshop.cfg: {e}")

        if mod_ids:
            mod_ids = list(dict.fromkeys(mod_ids))
            workshop_cfg_path = target_dir / "workshop.cfg"
            workshop_cfg_lines = [
                "# Workshop mods configuration",
                "autoupdate_enabled=false",
                "autoupdate_interval_hours=2",
                "autoupdate_interval_minutes=",
                f"workshop_mod_ids=({' '.join(mod_ids)})"
            ]
            workshop_cfg_path.write_text("\n".join(workshop_cfg_lines), encoding="utf-8")

    return {
        "ok": True,
        "name": target_server_name,
        "target_dir": target_dir.as_posix()
    }
