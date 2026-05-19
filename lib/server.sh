#!/bin/bash

fn_get_server_cfg_path() {
    printf '%s\n' "${SERVERFILES}/${server_config_dir:-ConanSandbox/Saved/Config/LinuxServer}/ServerSettings.ini"
}

fn_parse_mission_template_from_cfg() {
    local server_cfg="$1"

    sed -nE 's/^[[:space:]]*template[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$server_cfg" | head -n 1
}

fn_detect_single_mission_folder() {
    local missions_dir="${SERVERFILES}/mpmissions"
    local -a mission_dirs=()

    [ -d "$missions_dir" ] || return 1

    mapfile -t mission_dirs < <(find "$missions_dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort)
    if [ "${#mission_dirs[@]}" -ne 1 ]; then
        return 1
    fi

    printf '%s\n' "${mission_dirs[0]}"
}

fn_get_mission_folder() {
    local server_cfg
    local missionfolder=""
    server_cfg="$(fn_get_server_cfg_path)"

    if [ ! -f "$server_cfg" ]; then
        fn_log "$red" "error" "server_cfg_missing" "$server_cfg" >&2
        return 1
    fi

    missionfolder="$(fn_parse_mission_template_from_cfg "$server_cfg")"
    if [ -z "$missionfolder" ]; then
        missionfolder="$(fn_detect_single_mission_folder)"
    fi

    if [ -z "$missionfolder" ]; then
        fn_log "$red" "error" "mission_cfg_missing" "$server_cfg" >&2
        return 1
    fi

    printf '%s\n' "$missionfolder"
}

fn_get_mission_folder_from_archive() {
    local mission_archive="$1"
    local missionfolder=""

    if [ ! -f "$mission_archive" ]; then
        return 1
    fi

    missionfolder="$(tar -tf "$mission_archive" 2>/dev/null | head -n 1 | cut -d '/' -f 1)"

    if [ -z "$missionfolder" ]; then
        fn_log "$red" "error" "backup_run_incomplete" "$(basename "$(dirname "$mission_archive")")" >&2
        return 1
    fi

    printf '%s\n' "$missionfolder"
}

fn_status_dayz() {
    if tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
        dayzstatus="1"
    else
        dayzstatus="0"
    fi
}

fn_clear_logs() {
    local profiles_dir="${SERVERFILES}/ConanSandbox/Saved"

    if [ -d "$profiles_dir" ]; then
        find "$profiles_dir" -type f \( -name "*.RPT" -o -name "*.log" -o -name "*.mdmp" \) -delete
        fn_log "$green" "dayz" "cleared_logs"
    fi
}

fn_conan_ini_append_managed_block() {
    local file_path="$1"
    local block_name="$2"
    local content="$3"
    local tmp_file=""

    mkdir -p "$(dirname "$file_path")" || return 1
    touch "$file_path" || return 1
    tmp_file="$(mktemp "${file_path}.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp_file" ] || return 1

    awk -v begin="; BEGIN CONAN PANEL ${block_name}" -v end="; END CONAN PANEL ${block_name}" '
        $0 == begin { skip = 1; next }
        $0 == end { skip = 0; next }
        skip != 1 { print }
    ' "$file_path" > "$tmp_file" || {
        rm -f "$tmp_file"
        return 1
    }

    {
        printf '\n; BEGIN CONAN PANEL %s\n' "$block_name"
        printf '%s\n' "$content"
        printf '; END CONAN PANEL %s\n' "$block_name"
    } >> "$tmp_file"

    mv "$tmp_file" "$file_path"
}

fn_conan_write_managed_config() {
    local config_root="${SERVERFILES}/${server_config_dir:-ConanSandbox/Saved/Config/LinuxServer}"
    local engine_ini="${config_root}/Engine.ini"
    local server_ini="${config_root}/ServerSettings.ini"
    local game_ini="${config_root}/Game.ini"
    local rcon_enabled_normalized="False"
    local engine_block server_block game_block

    mkdir -p "$config_root" "${SERVERFILES}/ConanSandbox/Saved" || return 1

    if [ "${rcon_enabled:-false}" = "true" ] || [ "${rcon_enabled:-false}" = "1" ]; then
        rcon_enabled_normalized="True"
    fi

    engine_block="[URL]
Port=${port:-7777}

[OnlineSubsystem]
ServerName=${servername:-Conan Exiles Enhanced Server}
ServerPassword=${serverpassword:-}

[OnlineSubsystemNull]
GameServerQueryPort=${queryport:-27015}"

    server_block="[ServerSettings]
AdminPassword=${adminpassword:-}
MaxPlayers=${maxplayers:-40}"

    game_block="[RconPlugin]
RconEnabled=${rcon_enabled_normalized}
RconPort=${rconport:-25575}
RconPassword=${rcon_password:-}"

    # Enhanced migration note: stale UE4 build overrides can reject valid UE5 clients.
    sed -i '/^[[:space:]]*bUseBuildIdOverride[[:space:]]*=/d;/^[[:space:]]*BuildIdOverride[[:space:]]*=/d' "$engine_ini" 2>/dev/null || true

    fn_conan_ini_append_managed_block "$engine_ini" "ENGINE" "$engine_block" || return 1
    fn_conan_ini_append_managed_block "$server_ini" "SERVERSETTINGS" "$server_block" || return 1
    fn_conan_ini_append_managed_block "$game_ini" "GAME" "$game_block" || return 1
}

fn_launch_dayz_process() {
    local launch_command
    local binary_path="${SERVERFILES}/${server_binary:-ConanSandbox/Binaries/Linux/ConanSandboxServer}"

    if [ ! -d "${SERVERFILES}" ]; then
        fn_log "$red" "error" "serverfiles_dir_missing" "${SERVERFILES}"
        return 1
    fi
    if [ ! -x "$binary_path" ]; then
        fn_log "$red" "error" "server_binary_missing" "$binary_path"
        return 1
    fi
    if ! fn_conan_write_managed_config; then
        fn_log "$red" "error" "server_config_write_failed"
        return 1
    fi

    fn_log "$green" "dayz" "starting_server"
    sleep 1

    : > "${STARTUP_LOG}" 2>/dev/null || true
    launch_command="cd \"${SERVERFILES}\" && \"./${server_binary:-ConanSandbox/Binaries/Linux/ConanSandboxServer}\" ${conanparameter:-"-log"} 2>&1 | tee -a \"${STARTUP_LOG}\""
    tmux new-session -d -x 220 -y 5000 -s "${TMUX_SESSION}" "$launch_command"
    sleep 2

    if ! tmux has-session -t "${TMUX_SESSION}" 2>/dev/null; then
        fn_log "$red" "error" "server_start_failed" >&2
        fn_log "$yellow" "warning" "server_startup_log_hint" "${STARTUP_LOG}" >&2
        if [ -s "${STARTUP_LOG}" ]; then
            fn_log "$yellow" "warning" "server_startup_log_tail" >&2
            tail -n 20 "${STARTUP_LOG}" >&2
        fi
        return 1
    fi

    date > "${LOCKFILE}"
}

fn_prestart_dayz() {
    if [ "${PANEL_SKIP_PRESTART:-0}" = "1" ]; then
        fn_clear_logs
        return 0
    fi

    fn_backup_dayz
    fn_update_dayz
    fn_workshop_mods
    fn_clear_logs
}

fn_start_dayz() {
    fn_status_dayz

    if [ "${dayzstatus}" = "1" ]; then
        fn_log "$yellow" "dayz" "server_already_running"
        exit 1
    fi

    fn_prestart_dayz
    fn_launch_dayz_process
}

fn_stop_dayz() {
    fn_status_dayz

    if [ "${dayzstatus}" != "1" ]; then
        fn_log "$yellow" "dayz" "server_not_running"
        return 0
    fi

    fn_log_inline "$magenta" "progress" "stopping_server"

    local dayz_pid=""
    local seconds=""

    dayz_pid="$(pgrep -f "ConanSandboxServer" 2>/dev/null | head -n 1)"
    if [ -n "$dayz_pid" ]; then
        kill -SIGTERM "$dayz_pid" 2>/dev/null
    else
        tmux send-keys -t "${TMUX_SESSION}" C-c >/dev/null 2>&1
    fi

    for seconds in {1..30}; do
        fn_status_dayz
        if [ "${dayzstatus}" = "0" ]; then
            printf "\r[ ${green}%s${default} ] " "$(fn_label "ok")"
            printf "$(fn_tr "server_stopped_gracefully")" "$seconds"
            printf '\n'
            fn_remove_file_if_exists "${LOCKFILE}"
            return 0
        fi

        printf "\r[ ${magenta}%s${default} ] " "$(fn_label "progress")"
        printf "$(fn_tr "stopping_server_progress")" "$seconds"
        sleep 1
    done

    printf '\n'
    fn_log "$yellow" "warn" "graceful_stop_timeout"

    dayz_pid="$(pgrep -f "ConanSandboxServer" 2>/dev/null | head -n 1)"
    if [ -n "$dayz_pid" ]; then
        kill -9 "$dayz_pid" 2>/dev/null
        sleep 2
    fi

    tmux kill-session -t "${TMUX_SESSION}" 2>/dev/null
    fn_remove_file_if_exists "${LOCKFILE}"
    fn_log "$green" "ok" "server_force_stopped"
}

fn_restart_dayz() {
    fn_stop_dayz
    sleep 1
    fn_start_dayz
}

fn_monitor_dayz() {
    if [ -f "${LOCKUPDATE_FILE}" ]; then
        fn_log "$yellow" "info" "serverfiles_updating"
        return 0
    fi

    fn_status_dayz

    if [ "${dayzstatus}" = "0" ] && [ -f "${LOCKFILE}" ]; then
        fn_restart_dayz
    elif [ "${dayzstatus}" != "0" ] && [ -f "${LOCKFILE}" ]; then
        fn_log "$lightblue" "info" "server_should_be_online"
    else
        fn_log "$yellow" "info" "use_start_command"
    fi
}

fn_is_yes() {
    case "$1" in
        [YyJj]|[Yy][Ee][Ss]|[Jj][Aa]) return 0 ;;
        *) return 1 ;;
    esac
}

fn_is_no() {
    case "$1" in
        [Nn]|[Nn][Oo]) return 0 ;;
        *) return 1 ;;
    esac
}

fn_console_dayz() {
    fn_status_dayz

    if [ "${dayzstatus}" != "1" ]; then
        fn_log "$yellow" "warning" "console_not_running"
        return 1
    fi

    printf "[ ${yellow}%s${default} ] %s\n\n" "$(fn_label "warning")" "$(fn_tr "console_warning")"

    local answer=""
    while true; do
        read -e -i "$(fn_tr "yes_short")" -p "$(printf "$(fn_tr "console_prompt")" "$(fn_tr "yes_short")")" -r answer
        if fn_is_yes "$answer"; then
            tmux attach -t "${TMUX_SESSION}"
            return 0
        fi
        if fn_is_no "$answer"; then
            return 1
        fi
        printf '%s\n' "$(fn_tr "answer_yes_no")"
    done
}


fn_wipe_dayz() {
    local seconds=""
    local save_db=""
    local was_running="0"

    if [ -z "${SERVERFILES:-}" ] || [ ! -d "${SERVERFILES}" ]; then
        fn_log "$red" "error" "serverfiles_not_set"
        return 1
    fi
    save_db="${SERVERFILES}/${save_db_file:-ConanSandbox/Saved/game_0.db}"

    fn_log "$red" "warning" "wipe_warning"
    for seconds in {9..0}; do
        printf '\r\t'
        printf "$(fn_tr "selected_mission_countdown")" "${save_db}" "${seconds}"
        sleep 1
    done
    printf '\n'

    fn_status_dayz
    if [ "${dayzstatus}" != "0" ]; then
        was_running="1"
        fn_stop_dayz
    fi

    if [ -f "$save_db" ]; then
        rm -f -- "$save_db" "${save_db}-shm" "${save_db}-wal"
    else
        fn_log "$yellow" "warning" "storage_data_missing" "$save_db"
    fi

    fn_log "$yellow" "dayz" "wipe_complete"
    if [ "$was_running" = "1" ]; then
        fn_launch_dayz_process
    fi
}
