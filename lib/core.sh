#!/bin/bash

fn_init_colors() {
    if [ "${ansi}" != "off" ]; then
        default="\e[0m"
        bld="\e[1m"
        red="\e[31m"
        green="\e[32m"
        yellow="\e[33m"
        lightyellow="\e[93m"
        blue="\e[34m"
        lightblue="\e[94m"
        magenta="\e[35m"
        cyan="\e[36m"
        creeol="\r\033[K"
    else
        default=""
        bld=""
        red=""
        green=""
        yellow=""
        lightyellow=""
        blue=""
        lightblue=""
        magenta=""
        cyan=""
        creeol=""
    fi
}


fn_label() {
    local key="$1"

    case "${CURRENT_LANGUAGE}:${key}" in
        de:warning) printf '%s' 'Warnung' ;;
        en:warning) printf '%s' 'Warning' ;;
        de:important) printf '%s' 'Wichtig' ;;
        en:important) printf '%s' 'Important' ;;
        de:success) printf '%s' 'Erfolg' ;;
        en:success) printf '%s' 'Success' ;;
        de:finished) printf '%s' 'Fertig' ;;
        en:finished) printf '%s' 'Finished' ;;
        de:error) printf '%s' 'Fehler' ;;
        en:error) printf '%s' 'Error' ;;
        de:fail) printf '%s' 'Fehler' ;;
        en:fail) printf '%s' 'FAIL' ;;
        de:info) printf '%s' 'Info' ;;
        en:info) printf '%s' 'INFO' ;;
        de:ok) printf '%s' 'OK' ;;
        en:ok) printf '%s' 'OK' ;;
        de:warn) printf '%s' 'WARNUNG' ;;
        en:warn) printf '%s' 'WARN' ;;
        de:dayz) printf '%s' 'Conan Exiles' ;;
        en:dayz) printf '%s' 'Conan Exiles' ;;
        de:steam) printf '%s' 'STEAM' ;;
        en:steam) printf '%s' 'STEAM' ;;
        de:progress) printf '%s' '...' ;;
        en:progress) printf '%s' '...' ;;
        *) printf '%s' "$key" ;;
    esac
}

fn_log() {
    local color="$1"
    local label_key="$2"
    local message_key="$3"
    shift 3

    printf "[ ${color}%s${default} ] " "$(fn_label "$label_key")"
    printf "$(fn_tr "$message_key")" "$@"
    printf '\n'
}


fn_log_inline() {
    local color="$1"
    local label_key="$2"
    local message_key="$3"
    shift 3

    printf "[ ${color}%s${default} ] " "$(fn_label "$label_key")"
    printf "$(fn_tr "$message_key")" "$@"
}


fn_checkroot_dayz() {
    if [ "$(whoami)" = "root" ] && ! { { [ "$REQUESTED_COMMAND" = "panel" ] || [ "$REQUESTED_COMMAND" = "webpanel" ]; } && { [ "$REQUESTED_SUBCOMMAND" = "install" ] || [ "$REQUESTED_SUBCOMMAND" = "repair" ] || [ "$REQUESTED_SUBCOMMAND" = "update" ]; }; }; then
        fn_log "$red" "fail" "root_forbidden"
        printf '\t%s\n' "$(fn_tr "switch_user")"
        exit 1
    fi
}

fn_checkscreen() {
    if [ -n "${STY}" ]; then
        fn_log "$red" "fail" "screen_forbidden"
        printf '\t%s\n' "$(fn_tr "screen_nested")"
        exit 1
    fi
}

fn_require_dependencies() {
    local tools_string="$1"
    local require_runtime_libs="$2"
    local -a missing=()
    local -a tools=()
    local tool

    if [ -n "$tools_string" ]; then
        read -r -a tools <<< "$tools_string"
        for tool in "${tools[@]}"; do
            if ! command -v "$tool" >/dev/null 2>&1; then
                missing+=("$tool")
            fi
        done
    fi

    if [ "$require_runtime_libs" = "1" ] && command -v dpkg >/dev/null 2>&1; then
        if ! dpkg -s lib32gcc-s1 >/dev/null 2>&1; then
            missing+=("lib32gcc-s1")
        fi
    fi

    if [ "${#missing[@]}" -gt 0 ]; then
        fn_log "$red" "error" "dependencies_missing"
        printf '  - %s\n' "${missing[@]}"
        printf '%s\n' "$(fn_tr "dependencies_install_hint")"
        exit 1
    fi

    if [ -n "$tools_string" ] || [ "$require_runtime_libs" = "1" ]; then
        fn_log "$green" "ok" "dependencies_all_present"
    fi
}

fn_require_crontab() {
    if ! fn_is_crontab_installed; then
        fn_log "$red" "error" "crontab_missing"
        printf '%s\n' "$(fn_tr "dependencies_install_hint")"
        exit 1
    fi

    if ! fn_is_cron_service_active; then
        fn_log "$red" "error" "cron_service_inactive" "$(fn_get_cron_service_name)"
        exit 1
    fi
}

fn_get_cron_service_name() {
    printf '%s' "cron"
}

fn_is_crontab_installed() {
    command -v crontab >/dev/null 2>&1
}

fn_is_cron_service_active() {
    local cron_service=""

    cron_service="$(fn_get_cron_service_name)"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl is-active --quiet "$cron_service" >/dev/null 2>&1
        return $?
    fi

    pgrep -x "$cron_service" >/dev/null 2>&1
}

fn_is_scheduler_ready() {
    fn_is_crontab_installed && fn_is_cron_service_active
}

fn_get_scheduler_error() {
    if ! fn_is_crontab_installed; then
        printf '%s' "$(fn_tr "crontab_missing")"
        return 0
    fi

    if ! fn_is_cron_service_active; then
        printf "$(fn_tr "cron_service_inactive")" "$(fn_get_cron_service_name)"
        return 0
    fi

    printf ''
}

fn_require_steamlogin() {
    if [ "${steamlogin}" = "CHANGEME" ] || [ -z "${steamlogin}" ]; then
        fn_log "$red" "error" "steamlogin_required" "$CONFIG_FILE"
        exit 1
    fi
}

fn_is_help_request() {
    case "$REQUESTED_COMMAND" in
        ""|h|help|hilfe) return 0 ;;
        *) return 1 ;;
    esac
}

fn_is_quiet_request() {
    if fn_is_help_request; then
        return 0
    fi

    if { [ "$REQUESTED_COMMAND" = "panel" ] || [ "$REQUESTED_COMMAND" = "webpanel" ]; } && [ "$REQUESTED_SUBCOMMAND" = "bridge" ]; then
        return 0
    fi

    if { [ "$REQUESTED_COMMAND" = "panel" ] || [ "$REQUESTED_COMMAND" = "webpanel" ]; } && [ "$REQUESTED_SUBCOMMAND" = "status" ] && [ "$REQUESTED_THIRD_ARG" = "--json" ]; then
        return 0
    fi

    return 1
}

fn_resolve_script_path() {
    local source="$1"
    local dir=""

    while [ -h "$source" ]; do
        dir="$(cd -P "$(dirname "$source")" && pwd)"
        source="$(readlink "$source")"
        if [[ "$source" != /* ]]; then
            source="${dir}/${source}"
        fi
    done

    dir="$(cd -P "$(dirname "$source")" && pwd)"
    printf '%s/%s\n' "$dir" "$(basename "$source")"
}

fn_get_lock_dir_for_scope() {
    local scope="${1:-default}"

    case "$scope" in
        workshop)
            printf '%s\n' "${SERVER_DIR}/.conanserver.workshop.lock"
            ;;
        *)
            printf '%s\n' "${LOCK_DIR}"
            ;;
    esac
}

fn_acquire_lock() {
    local scope="${1:-default}"
    local lock_dir=""
    local owner_pid=""
    local owner_command=""
    local suffix=""

    lock_dir="$(fn_get_lock_dir_for_scope "$scope")"

    if [ "${LOCK_HELD:-0}" = "1" ] && [ "${LOCK_ACTIVE_DIR:-}" = "$lock_dir" ]; then
        return 0
    fi

    if [ "${LOCK_HELD:-0}" = "1" ]; then
        fn_log "$yellow" "warning" "nested_lock_not_allowed"
        exit 1
    fi

    if mkdir "$lock_dir" 2>/dev/null; then
        printf '%s\n' "$$" > "${lock_dir}/pid"
        printf '%s\n' "${CURRENT_COMMAND:-}" > "${lock_dir}/command"
        LOCK_HELD="1"
        LOCK_ACTIVE_DIR="$lock_dir"
        trap 'fn_release_lock' EXIT INT TERM
        return 0
    fi

    if [ -f "${lock_dir}/pid" ]; then
        owner_pid="$(cat "${lock_dir}/pid" 2>/dev/null)"
    fi
    if [ -f "${lock_dir}/command" ]; then
        owner_command="$(cat "${lock_dir}/command" 2>/dev/null)"
    fi

    if [ -n "$owner_pid" ] && ! kill -0 "$owner_pid" 2>/dev/null; then
        rm -rf "$lock_dir"
        if mkdir "$lock_dir" 2>/dev/null; then
            printf '%s\n' "$$" > "${lock_dir}/pid"
            printf '%s\n' "${CURRENT_COMMAND:-}" > "${lock_dir}/command"
            LOCK_HELD="1"
            LOCK_ACTIVE_DIR="$lock_dir"
            trap 'fn_release_lock' EXIT INT TERM
            return 0
        fi
    fi

    if [ -n "$owner_command" ]; then
        suffix="$(printf "$(fn_tr "operation_locked_suffix")" "$owner_command")"
    fi

    fn_log "$yellow" "warning" "operation_locked" "$suffix"
    exit 1
}

fn_release_lock() {
    if [ "${LOCK_HELD:-0}" = "1" ] && [ -n "${LOCK_ACTIVE_DIR:-}" ] && [ -d "${LOCK_ACTIVE_DIR}" ]; then
        rm -rf "${LOCK_ACTIVE_DIR}"
    fi
    LOCK_HELD="0"
    LOCK_ACTIVE_DIR=""
}

fn_server_installed() {
    [ -n "${STEAMCMD_DIR:-}" ] && [ -n "${SERVERFILES:-}" ] && \
    [ -f "${STEAMCMD_DIR}/steamcmd.sh" ] && [ -x "${SERVERFILES}/${server_binary:-ConanSandbox/Binaries/Linux/ConanSandboxServer}" ]
}

fn_remove_file_if_exists() {
    local path="$1"

    if [ -e "$path" ] || [ -L "$path" ]; then
        rm -f -- "$path"
    fi
}
