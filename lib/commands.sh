#!/bin/bash

fn_current_language_name() {
    case "$1" in
        en) fn_tr "language_name_en" ;;
        de) fn_tr "language_name_de" ;;
        *) printf '%s' "$1" ;;
    esac
}

fn_language_command() {
    local requested_language="${1:-}"

    if [ -z "$requested_language" ]; then
        fn_log "$lightblue" "info" "language_current" "$(fn_current_language_name "$CURRENT_LANGUAGE")" "$CURRENT_LANGUAGE"
        printf '%s\n' "$(fn_tr "language_usage")"
        return 0
    fi

    case "$requested_language" in
        en|de)
            fn_write_config_string "language" "$requested_language"
            CURRENT_LANGUAGE="$requested_language"
            language="$requested_language"
            fn_log "$green" "success" "language_updated" "$(fn_current_language_name "$requested_language")"
            ;;
        *)
            fn_log "$red" "error" "language_invalid" "$requested_language"
            printf '%s\n' "$(fn_tr "language_usage")"
            return 1
            ;;
    esac
}

fn_is_valid_server_name() {
    local name="$1"
    [[ "$name" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]
}

register_command() {
    local display="$1"
    local aliases="$2"
    local handler="$3"
    local syntax="$4"
    local desc_key="$5"
    local mutating="$6"
    local needs_server="$7"
    local required_tools="$8"
    local require_runtime_libs="$9"
    local requires_steamlogin="${10}"
    local index="${#COMMAND_DISPLAY[@]}"
    local alias=""

    COMMAND_DISPLAY+=("$display")
    COMMAND_ALIASES+=("$aliases")
    COMMAND_HANDLER+=("$handler")
    COMMAND_SYNTAX+=("$syntax")
    COMMAND_DESC_KEY+=("$desc_key")
    COMMAND_MUTATING+=("$mutating")
    COMMAND_NEEDS_SERVER+=("$needs_server")
    COMMAND_REQUIRED_TOOLS+=("$required_tools")
    COMMAND_REQUIRE_RUNTIME_LIBS+=("$require_runtime_libs")
    COMMAND_REQUIRES_STEAMLOGIN+=("$requires_steamlogin")
    COMMAND_INDEX_BY_DISPLAY["$display"]="$index"

    IFS=';' read -r -a alias_list <<< "$aliases"
    for alias in "${alias_list[@]}"; do
        COMMAND_INDEX_BY_ALIAS["$alias"]="$index"
    done
}

fn_server_list() {
    local servers_dir="${HOME}/servers"
    local found_any="0"

    fn_log "$lightblue" "info" "server_list_header"
    if [ -d "$servers_dir" ]; then
        while IFS= read -r name; do
            [ -z "$name" ] && continue
            printf '  %s\n' "$name"
            found_any="1"
        done < <(find "$servers_dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort)
    fi

    if [ "$found_any" = "0" ]; then
        printf '  (none)\n'
    fi
}

fn_server_create() {
    local name="${1:-}"

    if [ -z "$name" ] || ! fn_is_valid_server_name "$name"; then
        fn_log "$red" "error" "server_name_invalid"
        return 1
    fi

    local server_dir="${HOME}/servers/${name}"
    if [ -d "$server_dir" ]; then
        fn_log "$yellow" "warning" "server_already_exists" "$name"
        return 1
    fi

    mkdir -p "${server_dir}" || return 1

    # Write the default config.ini so the user can configure credentials immediately
    local config_file="${server_dir}/config.ini"
    if [ ! -f "$config_file" ]; then
        printf '%s\n' "$DEFAULT_CONFIG" > "$config_file"
        chmod 600 "$config_file" 2>/dev/null || true
    fi

    fn_log "$green" "success" "server_created" "$name" "$server_dir"
}

fn_server_source_is_running() {
    local name="$1"
    local tmux_session="$(whoami)-${name}"

    command -v tmux >/dev/null 2>&1 || return 1
    tmux has-session -t "${tmux_session}" 2>/dev/null
}

fn_server_clone_cleanup_runtime_files() {
    local target_dir="$1"
    local -a runtime_files=(
        ".conanlockfile"
        ".conanlockupdate"
        ".conanserver.lock"
        ".conanserver.workshop.lock"
        ".panel_task.json"
        ".panel_task.lock"
        ".panel_task.workshop.json"
        ".panel_task.workshop.lock"
        "panel_action.log"
        "panel_action.workshop.log"
        "server_startup.log"
        "autorestart_cron.log"
        "workshop_autoupdate.log"
    )
    local runtime_file=""

    for runtime_file in "${runtime_files[@]}"; do
        if [ -d "${target_dir}/${runtime_file}" ]; then
            rm -rf -- "${target_dir}/${runtime_file}" 2>/dev/null || return 1
            continue
        fi
        rm -f -- "${target_dir}/${runtime_file}" 2>/dev/null || return 1
    done

    return 0
}

fn_server_clone_rewrite_internal_symlinks() {
    local source_dir="$1"
    local target_dir="$2"
    local link_path=""
    local current_target=""
    local rewritten_target=""

    while IFS= read -r -d '' link_path; do
        current_target="$(readlink "$link_path" 2>/dev/null || true)"
        [ -n "$current_target" ] || continue

        case "$current_target" in
            "${source_dir}"/*)
                rewritten_target="${target_dir}${current_target#"$source_dir"}"
                ln -snf -- "$rewritten_target" "$link_path" || return 1
                ;;
        esac
    done < <(find "$target_dir" -type l -print0 2>/dev/null)

    return 0
}

fn_server_clone_reset_autorestart_config() {
    local target_dir="$1"
    local target_config="${target_dir}/config.ini"

    [ -f "$target_config" ] || return 0

    (
        CONFIG_FILE="$target_config"
        fn_write_config_string "autorestart_mode" "off" &&
        fn_write_config_string "autorestart_times" "" &&
        fn_write_config_string "autorestart_interval_hours" ""
    )
}

fn_server_clone_clear_scheduler_blocks() {
    local server_name="$1"
    local current_crontab=""
    local cleaned_crontab=""
    local temp_file=""

    command -v crontab >/dev/null 2>&1 || return 0

    current_crontab="$(crontab -l 2>/dev/null || true)"
    if ! printf '%s\n' "$current_crontab" | grep -qF "# BEGIN CONANSERVER AUTORESTART ${server_name}" \
        && ! printf '%s\n' "$current_crontab" | grep -qF "# BEGIN CONANSERVER WORKSHOP AUTOUPDATE ${server_name}"; then
        return 0
    fi

    cleaned_crontab="$(fn_get_crontab_without_named_blocks \
        "CONANSERVER AUTORESTART ${server_name}" \
        "CONANSERVER WORKSHOP AUTOUPDATE ${server_name}")"
    temp_file="$(mktemp)"

    if [ -n "$cleaned_crontab" ]; then
        printf '%s\n' "$cleaned_crontab" > "$temp_file"
    fi

    if ! crontab "$temp_file"; then
        rm -f "$temp_file"
        fn_log "$red" "error" "crontab_update_failed"
        return 1
    fi

    rm -f "$temp_file"
    return 0
}

fn_server_clone() {
    local source="${1:-}"
    local target="${2:-}"
    local source_dir=""
    local target_dir=""

    if [ "$#" -ne 2 ]; then
        fn_log "$red" "error" "server_clone_usage"
        return 1
    fi

    if [ -z "$source" ] || ! fn_is_valid_server_name "$source" || [ -z "$target" ] || ! fn_is_valid_server_name "$target" ]; then
        fn_log "$red" "error" "server_name_invalid"
        return 1
    fi

    if [ "$source" = "$target" ]; then
        fn_log "$red" "error" "server_clone_same_name" "$source"
        return 1
    fi

    source_dir="${HOME}/servers/${source}"
    target_dir="${HOME}/servers/${target}"

    if [ ! -d "$source_dir" ]; then
        fn_log "$red" "error" "server_clone_source_not_found" "$source" "$source_dir"
        return 1
    fi

    if [ -d "$target_dir" ]; then
        fn_log "$yellow" "warning" "server_clone_target_exists" "$target" "$target_dir"
        return 1
    fi

    if fn_server_source_is_running "$source"; then
        fn_log "$yellow" "warning" "server_clone_live_warning" "$source"
    fi

    if ! mkdir -p -- "$target_dir"; then
        fn_log "$red" "error" "server_dir_create_failed" "$target_dir"
        return 1
    fi

    if ! cp -a -- "${source_dir}/." "${target_dir}/"; then
        rm -rf -- "$target_dir"
        fn_log "$red" "error" "server_clone_failed" "$source" "$target"
        return 1
    fi

    if ! fn_server_clone_rewrite_internal_symlinks "$source_dir" "$target_dir"; then
        rm -rf -- "$target_dir"
        fn_log "$red" "error" "server_clone_failed" "$source" "$target"
        return 1
    fi

    if ! fn_server_clone_cleanup_runtime_files "$target_dir"; then
        rm -rf -- "$target_dir"
        fn_log "$red" "error" "server_clone_failed" "$source" "$target"
        return 1
    fi

    if ! fn_server_clone_reset_autorestart_config "$target_dir"; then
        rm -rf -- "$target_dir"
        fn_log "$red" "error" "server_clone_failed" "$source" "$target"
        return 1
    fi

    if ! fn_server_clone_clear_scheduler_blocks "$target"; then
        rm -rf -- "$target_dir"
        fn_log "$red" "error" "server_clone_failed" "$source" "$target"
        return 1
    fi

    fn_log "$green" "success" "server_cloned" "$source" "$target" "$target_dir"
}

fn_server_delete() {
    local force="0"
    local name=""
    local _arg=""
    for _arg in "$@"; do
        if [ "$_arg" = "--force" ]; then
            force="1"
        elif [ -z "$name" ]; then
            name="$_arg"
        else
            fn_log "$red" "error" "server_delete_extra_args"
            return 1
        fi
    done

    if [ -z "$name" ] || ! fn_is_valid_server_name "$name"; then
        fn_log "$red" "error" "server_name_invalid"
        return 1
    fi

    local server_dir="${HOME}/servers/${name}"
    if [ ! -d "$server_dir" ]; then
        fn_log "$red" "error" "server_not_found" "$name" "$server_dir"
        return 1
    fi

    if [ "$force" = "0" ]; then
        if [ ! -t 0 ]; then
            fn_log "$red" "error" "server_delete_requires_force"
            return 1
        fi
        fn_log "$yellow" "warning" "server_delete_warning" "$name"
        printf 'Type the server name to confirm deletion: '
        local confirm=""
        read -r -t 60 confirm || { fn_log "$red" "error" "server_delete_timeout"; return 1; }
        if [ "$confirm" != "$name" ]; then
            fn_log "$yellow" "info" "server_delete_cancelled"
            return 1
        fi
    fi

    # Stop the server if running before deleting files
    (
        export SERVER_NAME="$name"
        export SERVER_DIR="$server_dir"
        fn_init_server_paths
        fn_stop_dayz >/dev/null 2>&1 || true
    )

    if rm -rf -- "$server_dir"; then
        if [ -d "$server_dir" ]; then
            fn_log "$red" "error" "server_delete_failed" "$name"
            return 1
        fi
        fn_log "$green" "success" "server_deleted" "$name"
    else
        fn_log "$red" "error" "server_delete_failed" "$name"
        return 1
    fi
}

fn_server_command() {
    local subcommand="${1:-}"
    shift || true

    case "$subcommand" in
        list|liste) fn_server_list ;;
        create|erstellen) fn_server_create "${@}" ;;
        clone|klonen) fn_server_clone "${@}" ;;
        delete|loeschen) fn_server_delete "${@}" ;;
        *)
            printf 'Usage: server list | server create <name> | server clone <source> <target> | server delete [--force] <name>\n'
            return 1
            ;;
    esac
}

fn_migrate_command() {
    local name="${1:-default}"

    if ! fn_is_valid_server_name "$name"; then
        fn_log "$red" "error" "server_name_invalid"
        return 1
    fi

    if [ ! -d "${HOME}/serverfiles" ] && [ ! -d "${HOME}/steamcmd" ]; then
        fn_log "$yellow" "info" "migrate_no_legacy"
        return 0
    fi

    fn_migrate_to_multiserver "$name"
}

fn_register_commands() {
    register_command "start" "st;start;starten" "fn_start_dayz" "start" "cmd_desc_start" "1" "1" "tmux curl jq wget" "1" "1"
    register_command "stop" "sp;stop;stopp" "fn_stop_dayz" "stop" "cmd_desc_stop" "1" "1" "tmux" "0" "0"
    register_command "restart" "r;restart;neustart" "fn_restart_dayz" "restart" "cmd_desc_restart" "1" "1" "tmux curl jq wget" "1" "1"
    register_command "monitor" "m;monitor;ueberwachen" "fn_monitor_dayz" "monitor" "cmd_desc_monitor" "1" "1" "tmux curl jq wget" "1" "1"
    register_command "console" "c;console;konsole" "fn_console_dayz" "console" "cmd_desc_console" "0" "1" "tmux" "0" "0"
    register_command "install" "i;install;installieren" "fn_install_dayz" "install" "cmd_desc_install" "1" "0" "curl" "0" "1"
    register_command "update" "u;update;aktualisieren" "fn_update_dayz" "update" "cmd_desc_update" "1" "1" "curl jq wget" "0" "1"
    register_command "validate" "v;validate;pruefen" "fn_validate_dayz" "validate" "cmd_desc_validate" "1" "1" "curl jq wget" "0" "1"
    register_command "workshop" "ws;workshop;mods" "fn_workshop_command" "workshop" "cmd_desc_workshop" "1" "1" "curl jq wget" "0" "1"
    register_command "backup" "b;backup;sicherung" "fn_backup_command" "backup" "cmd_desc_backup" "1" "1" "" "0" "0"
    register_command "wipe" "wi;wipe;reset" "fn_wipe_dayz" "wipe" "cmd_desc_wipe" "1" "1" "" "0" "0"
    register_command "panel" "panel;webpanel" "fn_panel_command" "panel install|repair|status|bridge" "cmd_desc_panel" "0" "0" "" "0" "0"
    register_command "help" "h;help;hilfe" "fn_help_command" "help" "cmd_desc_help" "0" "0" "" "0" "0"
    register_command "language" "language;lang;sprache" "fn_language_command" "language [en|de]" "cmd_desc_language" "1" "0" "" "0" "0"
    register_command "autorestart" "autorestart;ar;autoneustart" "fn_autorestart_command" "autorestart set times HH:MM... | set interval <hours> | list | clear" "cmd_desc_autorestart" "0" "0" "" "0" "0"
    register_command "server" "server;srv" "fn_server_command" "server list | server create <name> | server clone <source> <target> | server delete [--force] <name>" "cmd_desc_server" "0" "0" "" "0" "0"
    register_command "migrate" "migrate;migrieren" "fn_migrate_command" "migrate [name]" "cmd_desc_migrate" "1" "0" "" "0" "0"
}


fn_command_effective_mutating() {
    local command_name="$1"
    local base_mutating="$2"
    local first_arg="${3:-}"

    if [ "$command_name" = "backup" ] && { [ "$first_arg" = "list" ] || [ "$first_arg" = "liste" ] || [ "$first_arg" = "anzeigen" ]; }; then
        printf '0\n'
        return
    fi

    if [ "$command_name" = "panel" ] && { [ "$first_arg" = "install" ] || [ "$first_arg" = "repair" ]; }; then
        printf '1\n'
        return
    fi

    if [ "$command_name" = "server" ] && { [ "$first_arg" = "create" ] || [ "$first_arg" = "erstellen" ] || [ "$first_arg" = "clone" ] || [ "$first_arg" = "klonen" ] || [ "$first_arg" = "delete" ] || [ "$first_arg" = "loeschen" ]; }; then
        printf '1\n'
        return
    fi

    printf '%s\n' "$base_mutating"
}


fn_command_lock_scope() {
    local command_name="$1"

    case "$command_name" in
        workshop)
            printf 'workshop\n'
            ;;
        *)
            printf 'default\n'
            ;;
    esac
}


fn_command_effective_needs_server() {
    local command_name="$1"
    local base_needs_server="$2"
    local first_arg="${3:-}"

    if [ "$command_name" = "backup" ] && { [ "$first_arg" = "list" ] || [ "$first_arg" = "liste" ] || [ "$first_arg" = "anzeigen" ] || [ "$first_arg" = "restore" ] || [ "$first_arg" = "wiederherstellen" ]; }; then
        printf '0\n'
        return
    fi

    printf '%s\n' "$base_needs_server"
}

fn_dispatch_command() {
    local command_name="$1"
    local command_index=""
    local effective_mutating=""
    local effective_needs_server=""
    local lock_scope="default"

    shift || true
    CURRENT_COMMAND="$command_name"

    if [ -n "${COMMAND_INDEX_BY_ALIAS[$command_name]+x}" ]; then
        command_index="${COMMAND_INDEX_BY_ALIAS[$command_name]}"
    else
        fn_log "$red" "error" "unknown_command" "$0" "$command_name"
        fn_help_command
        exit 1
    fi

    effective_mutating="$(fn_command_effective_mutating "$command_name" "${COMMAND_MUTATING[$command_index]}" "${1:-}")"
    effective_needs_server="$(fn_command_effective_needs_server "$command_name" "${COMMAND_NEEDS_SERVER[$command_index]}" "${1:-}")"

    if [ "$effective_mutating" = "1" ]; then
        lock_scope="$(fn_command_lock_scope "$command_name")"
        fn_acquire_lock "$lock_scope"
    fi

    if [ "$effective_needs_server" = "1" ]; then
        fn_checkscreen
    fi

    if [ -n "${COMMAND_REQUIRED_TOOLS[$command_index]}" ] || [ "${COMMAND_REQUIRE_RUNTIME_LIBS[$command_index]}" = "1" ]; then
        fn_require_dependencies "${COMMAND_REQUIRED_TOOLS[$command_index]}" "${COMMAND_REQUIRE_RUNTIME_LIBS[$command_index]}"
    fi

    if [ "${COMMAND_REQUIRES_STEAMLOGIN[$command_index]}" = "1" ]; then
        fn_require_steamlogin
    fi

    if [ "$effective_needs_server" = "1" ] && ! fn_server_installed; then
        fn_log "$yellow" "info" "no_server_install"
        if [ -f "${HOME}/conanserver" ]; then
            chmod u+x "${HOME}/conanserver" 2>/dev/null || true
        fi
        fn_require_dependencies "curl" "0"
        fn_install_dayz
        if fn_server_installed; then
            fn_log "$green" "success" "install_complete"
            exit 0
        fi
        exit 1
    fi

    "${COMMAND_HANDLER[$command_index]}" "$@"
}
