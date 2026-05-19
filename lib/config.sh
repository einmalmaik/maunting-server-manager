#!/bin/bash

DEFAULT_CONFIG=$(cat <<'EOF'
# Conan Exiles Enhanced Dedicated Server / Conan Exiles Enhanced Dedicated Server
appid=443030
workshop_appid=440900
server_binary="ConanSandbox/Binaries/Linux/ConanSandboxServer"
server_config_dir="ConanSandbox/Saved/Config/LinuxServer"
save_db_file="ConanSandbox/Saved/game_0.db"

# Default interface language / Standardsprache fuer die Benutzeroberflaeche
language="en"

# Automatic restart mode / Automatischer Neustartmodus
# Valid values / Gueltige Werte: off, times, interval
autorestart_mode="off"

# Automatic daily restart times in 24-hour format (space-separated, mode=times)
# Automatische taegliche Neustartzeiten im 24-Stunden-Format (leerzeichengetrennt, modus=times)
autorestart_times=""

# Automatic restart interval in hours (mode=interval)
# Automatisches Neustartintervall in Stunden (modus=interval)
# Allowed values / Erlaubte Werte: 1, 2, 3, 4, 6, 8, 12, 24
autorestart_interval_hours=""

# Game, query and RCON ports / Spiel-, Query- und RCON-Ports
port=7777
queryport=27015
rconport=25575
rcon_host="127.0.0.1"

# IMPORTANT PARAMETERS / WICHTIGE PARAMETER
steamlogin=anonymous
steampassword=
servername="Conan Exiles Enhanced Server"
serverpassword=""
adminpassword=CHANGEME
maxplayers=40
rcon_enabled="false"
rcon_password=""
# optional - just remove the # to enable / optional - # entfernen zum Aktivieren
#extra_args="-MULTIHOME=0.0.0.0 -MULTIHOMEHTTP=0.0.0.0"

# Discord notifications / Discord-Benachrichtigungen
discord_webhook_url=""

# Conan Exiles mods from Steam Workshop
# Edit workshop.cfg and add one mod number per line.
# workshop.cfg bearbeiten und pro Zeile eine Mod-ID eintragen.
# The manager writes ConanSandbox/Mods/modlist.txt in this order.
# Der Manager schreibt ConanSandbox/Mods/modlist.txt in dieser Reihenfolge.

# Modify carefully. Values above are written to Engine.ini, Game.ini and ServerSettings.ini.
# Vorsichtig anpassen. Die Werte oben werden nach Engine.ini, Game.ini und ServerSettings.ini geschrieben.
conanparameter="-log -Port=${port} -QueryPort=${queryport} -RconPort=${rconport} ${extra_args}"
EOF
)
fn_safe_chmod_config() {
    chmod 600 "$CONFIG_FILE" 2>/dev/null || true
}

fn_append_missing_config_defaults() {
    local appended="0"

    if ! grep -q '^language=' "$CONFIG_FILE"; then
        {
            printf '\n# Default interface language / Standardsprache fuer die Benutzeroberflaeche\n'
            printf 'language="en"\n'
        } >> "$CONFIG_FILE"
        appended="1"
    fi

    if ! grep -q '^autorestart_mode=' "$CONFIG_FILE"; then
        {
            printf '\n# Automatic restart mode / Automatischer Neustartmodus\n'
            printf '# Valid values / Gueltige Werte: off, times, interval\n'
            printf 'autorestart_mode="off"\n'
        } >> "$CONFIG_FILE"
        appended="1"
    fi

    if ! grep -q '^autorestart_times=' "$CONFIG_FILE"; then
        {
            printf '\n# Automatic daily restart times in 24-hour format (space-separated, mode=times)\n'
            printf '# Automatische taegliche Neustartzeiten im 24-Stunden-Format (leerzeichengetrennt, modus=times)\n'
            printf 'autorestart_times=""\n'
        } >> "$CONFIG_FILE"
        appended="1"
    fi

    if ! grep -q '^autorestart_interval_hours=' "$CONFIG_FILE"; then
        {
            printf '\n# Automatic restart interval in hours (mode=interval)\n'
            printf '# Automatisches Neustartintervall in Stunden (modus=interval)\n'
            printf '# Allowed values / Erlaubte Werte: 1, 2, 3, 4, 6, 8, 12, 24\n'
            printf 'autorestart_interval_hours=""\n'
        } >> "$CONFIG_FILE"
        appended="1"
    fi

    if ! grep -q '^steampassword=' "$CONFIG_FILE"; then
        {
            printf '\n# Steam password for logins (required if not anonymous)\n'
            printf '# Steam-Passwort fuer Logins (erforderlich, falls nicht anonymous)\n'
            printf 'steampassword=CHANGEME\n'
        } >> "$CONFIG_FILE"
        appended="1"
    fi

    if ! grep -q '^workshop_appid=' "$CONFIG_FILE"; then
        {
            printf '\n# Conan Exiles Workshop App ID / Conan Exiles Workshop-App-ID\n'
            printf 'workshop_appid=440900\n'
        } >> "$CONFIG_FILE"
        appended="1"
    fi

    if ! grep -q '^server_binary=' "$CONFIG_FILE"; then
        {
            printf '\n# Native Linux server binary / Native Linux-Server-Binary\n'
            printf 'server_binary="ConanSandbox/Binaries/Linux/ConanSandboxServer"\n'
        } >> "$CONFIG_FILE"
        appended="1"
    fi

    if ! grep -q '^save_db_file=' "$CONFIG_FILE"; then
        {
            printf '\n# Enhanced save database / Enhanced-Save-Datenbank\n'
            printf 'save_db_file="ConanSandbox/Saved/game_0.db"\n'
        } >> "$CONFIG_FILE"
        appended="1"
    fi

    if [ "$appended" = "1" ]; then
        fn_safe_chmod_config
    fi
}

fn_ensure_config_exists() {
    if [ -z "${SERVER_DIR:-}" ]; then
        fn_log "$red" "error" "server_dir_not_set"
        return 1
    fi
    if [ ! -f "$CONFIG_FILE" ]; then
        if ! fn_is_quiet_request; then
            fn_log "$yellow" "warning" "config_missing" "$CONFIG_FILE"
        fi
        printf '%s\n' "$DEFAULT_CONFIG" > "$CONFIG_FILE"
        fn_safe_chmod_config
        CONFIG_WAS_CREATED="1"
        if ! fn_is_quiet_request; then
            fn_log "$green" "success" "config_created" "$CONFIG_FILE"
            fn_log "$red" "important" "config_edit_required" "$CONFIG_FILE"
        fi
    fi
}

fn_load_config() {
    local had_autorestart_mode="0"

    if grep -q '^autorestart_mode=' "$CONFIG_FILE"; then
        had_autorestart_mode="1"
    fi

    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
    fn_append_missing_config_defaults
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"

    language="${language:-en}"
    appid="${appid:-443030}"
    workshop_appid="${workshop_appid:-440900}"
    server_binary="${server_binary:-ConanSandbox/Binaries/Linux/ConanSandboxServer}"
    server_config_dir="${server_config_dir:-ConanSandbox/Saved/Config/LinuxServer}"
    save_db_file="${save_db_file:-ConanSandbox/Saved/game_0.db}"
    port="${port:-7777}"
    queryport="${queryport:-27015}"
    rconport="${rconport:-25575}"
    conanparameter="${conanparameter:-"-log -Port=${port} -QueryPort=${queryport} -RconPort=${rconport} ${extra_args:-}"}"
    autorestart_mode="${autorestart_mode:-off}"
    autorestart_times="${autorestart_times:-}"
    autorestart_interval_hours="${autorestart_interval_hours:-}"

    case "$language" in
        en|de) CURRENT_LANGUAGE="$language" ;;
        *)
            CURRENT_LANGUAGE="en"
            fn_log "$yellow" "warning" "invalid_config_value" "language"
            ;;
    esac

    if [ "$had_autorestart_mode" != "1" ] && [ -n "$autorestart_times" ]; then
        autorestart_mode="times"
        fn_write_config_string "autorestart_mode" "$autorestart_mode"
    fi

    case "$autorestart_mode" in
        off|times|interval) ;;
        *)
            autorestart_mode="off"
            fn_log "$yellow" "warning" "invalid_config_value" "autorestart_mode"
            fn_write_config_string "autorestart_mode" "$autorestart_mode"
            ;;
    esac

    case "$autorestart_mode" in
        times)
            if [ -z "$autorestart_times" ]; then
                autorestart_mode="off"
                fn_write_config_string "autorestart_mode" "$autorestart_mode"
            fi
            if [ -n "$autorestart_interval_hours" ]; then
                autorestart_interval_hours=""
                fn_write_config_string "autorestart_interval_hours" ""
            fi
            ;;
        interval)
            if ! fn_is_valid_interval_hours "$autorestart_interval_hours"; then
                autorestart_mode="off"
                autorestart_interval_hours=""
                fn_log "$yellow" "warning" "invalid_config_value" "autorestart_interval_hours"
                fn_write_config_string "autorestart_mode" "$autorestart_mode"
                fn_write_config_string "autorestart_interval_hours" ""
            fi
            if [ -n "$autorestart_times" ]; then
                autorestart_times=""
                fn_write_config_string "autorestart_times" ""
            fi
            ;;
    esac

    fn_safe_chmod_config
    if ! fn_is_quiet_request; then
        fn_log "$green" "success" "config_found"
        fn_log "$green" "finished" "config_loaded"
    fi
}

fn_init_server_paths() {
    if [ -z "${SERVER_DIR:-}" ]; then
        fn_log "$red" "error" "server_dir_not_set"
        return 1
    fi
    SERVERFILES="${SERVER_DIR}/serverfiles"
    STEAMCMD_DIR="${SERVER_DIR}/steamcmd"
    BACKUP_DIR="${SERVER_DIR}/backup"
    SERVERPROFILE="${SERVER_DIR}/serverprofile"
    WORKSHOP_CFG="${SERVER_DIR}/workshop.cfg"
    TIMESTAMP_FILE="${SERVER_DIR}/mod_timestamps.json"
    LOCKFILE="${SERVER_DIR}/.conanlockfile"
    LOCKUPDATE_FILE="${SERVER_DIR}/.conanlockupdate"
    STARTUP_LOG="${SERVER_DIR}/server_startup.log"
    AUTORESTART_CRON_LOG="${SERVER_DIR}/autorestart_cron.log"
    WORKSHOP_AUTOUPDATE_LOG="${SERVER_DIR}/workshop_autoupdate.log"
    WORKSHOPFOLDER="${SERVER_DIR}/serverfiles/steamapps/workshop/content/${workshop_appid:-440900}"
    CONAN_MODS_DIR="${SERVER_DIR}/serverfiles/ConanSandbox/Mods"
    CONAN_MODLIST="${CONAN_MODS_DIR}/modlist.txt"
    TMUX_SESSION="$(whoami)-conan-${SERVER_NAME:-default}"

    export SERVERFILES STEAMCMD_DIR BACKUP_DIR SERVERPROFILE
    export WORKSHOP_CFG TIMESTAMP_FILE LOCKFILE LOCKUPDATE_FILE STARTUP_LOG
    export AUTORESTART_CRON_LOG WORKSHOP_AUTOUPDATE_LOG WORKSHOPFOLDER CONAN_MODS_DIR CONAN_MODLIST TMUX_SESSION
}

fn_migrate_to_multiserver() {
    local target_name="${1:-default}"
    local runtime_home="${HOME}"
    # Reject path traversal and invalid characters before constructing any path
    if [[ "$target_name" =~ [/\\] ]] || [[ "$target_name" == .* ]] || [[ ! "$target_name" =~ ^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$ ]]; then
        fn_log "$red" "error" "server_name_invalid" "$target_name"
        return 1
    fi
    if command -v fn_panel_get_runtime_home >/dev/null 2>&1; then
        runtime_home="$(fn_panel_get_runtime_home 2>/dev/null || printf '%s' "${HOME}")"
    fi
    local default_dir="${runtime_home}/servers/${target_name}"

    # Only migrate if old flat layout exists AND (for auto-migration) new-style servers/ dir does not yet exist
    if [ ! -d "${runtime_home}/serverfiles" ] && [ ! -d "${runtime_home}/steamcmd" ]; then
        return 0
    fi
    if [ "$target_name" = "default" ] && [ -d "${runtime_home}/servers" ]; then
        return 0
    fi

    if ! mkdir -p "${default_dir}" 2>/dev/null; then
        fn_log "$red" "error" "server_dir_create_failed" "${default_dir}"
        return 1
    fi

    # Pre-flight: verify write access to HOME (needed to remove source dirs) and target
    if [ ! -w "${runtime_home}" ] || [ ! -w "${default_dir}" ]; then
        fn_log "$red" "error" "migrate_no_permission" "${default_dir}"
        return 1
    fi

    # Pre-flight: disk space check (relevant when source and target are on different filesystems)
    local _req_kb=0 _dir_size _avail_kb
    for _chk in serverfiles steamcmd backup serverprofile; do
        [ -d "${runtime_home}/${_chk}" ] || continue
        _dir_size="$(du -sk "${runtime_home}/${_chk}" 2>/dev/null | cut -f1)"
        _req_kb=$(( _req_kb + ${_dir_size:-0} ))
    done
    _avail_kb="$(df -k "${default_dir}" 2>/dev/null | awk 'NR==2 {print $4}')"
    if [ -n "${_avail_kb}" ] && [ "${_avail_kb:-0}" -lt "${_req_kb}" ] 2>/dev/null; then
        fn_log "$red" "error" "migrate_insufficient_space"
        return 1
    fi
    unset _req_kb _dir_size _avail_kb _chk

    for d in serverfiles steamcmd backup serverprofile; do
        if [ -d "${runtime_home}/${d}" ]; then
            if ! mv -- "${runtime_home}/${d}" "${default_dir}/${d}"; then
                fn_log "$red" "error" "migration_mv_failed" "${d}"
                fn_log "$red" "error" "migration_incomplete"
                return 1
            fi
        fi
    done
    for f in workshop.cfg mod_timestamps.json .dayzlockfile .dayzlockupdate .conanlockfile .conanlockupdate; do
        # Skip lock files — they are runtime artifacts that will be recreated;
        # moving them while a server process might be active causes race conditions.
        case "$f" in
            .dayzlockfile|.dayzlockupdate|.conanlockfile|.conanlockupdate)
                [ -f "${runtime_home}/${f}" ] && rm -f -- "${runtime_home}/${f}"
                continue ;;
        esac
        if [ -f "${runtime_home}/${f}" ]; then
            if ! mv -- "${runtime_home}/${f}" "${default_dir}/${f}"; then
                fn_log "$red" "error" "migration_mv_failed" "${f}"
                fn_log "$red" "error" "migration_incomplete"
                return 1
            fi
        fi
    done

    # Copy existing config into the target server dir so it keeps its settings
    if [ -n "${SCRIPT_DIR:-}" ] && [ -f "${SCRIPT_DIR}/config.ini" ] && [ ! -f "${default_dir}/config.ini" ]; then
        if cp -- "${SCRIPT_DIR}/config.ini" "${default_dir}/config.ini"; then
            chmod 600 "${default_dir}/config.ini"
        else
            fn_log "$yellow" "warning" "config_copy_failed" "${default_dir}/config.ini"
        fi
    fi

    fn_log "$green" "success" "migrated_to_multiserver" "$target_name"
}

fn_write_config_string() {
    local key="$1"
    local value="$2"
    local escaped_value="$value"

    escaped_value="${escaped_value//\\/\\\\}"
    escaped_value="${escaped_value//&/\\&}"
    escaped_value="${escaped_value//|/\\|}"

    # Always normalize to LF - idempotent and cheap (no-op on LF files)
    # This fixes issues when config files are edited via web panel on Windows
    sed -i 's/\r$//' "$CONFIG_FILE" 2>/dev/null || true

    if grep -q "^${key}=" "$CONFIG_FILE"; then
        sed -i "s|^${key}=.*|${key}=\"${escaped_value}\"|" "$CONFIG_FILE"
    else
        printf '\n%s="%s"\n' "$key" "$value" >> "$CONFIG_FILE"
    fi

    fn_safe_chmod_config
}
