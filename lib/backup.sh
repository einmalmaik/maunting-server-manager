#!/bin/bash

fn_get_backup_root() {
    if [ -z "${BACKUP_DIR:-}" ]; then
        fn_log "$red" "error" "backup_dir_not_set" >&2
        return 1
    fi
    printf '%s\n' "${BACKUP_DIR}"
}

fn_get_backup_run_path() {
    local timestamp="$1"
    printf '%s/%s\n' "$(fn_get_backup_root)" "$timestamp"
}

fn_create_backup_run_directory() {
    local base_timestamp=""
    local candidate=""
    local suffix="1"
    local run_path=""

    base_timestamp="$(date +%Y-%m-%d_%H-%M)"
    candidate="$base_timestamp"
    run_path="$(fn_get_backup_run_path "$candidate")"

    while [ -e "$run_path" ]; do
        candidate="${base_timestamp}-$(printf '%02d' "$suffix")"
        run_path="$(fn_get_backup_run_path "$candidate")"
        suffix=$((suffix + 1))
    done

    if ! mkdir -p "$run_path" 2>/dev/null; then
        fn_log "$red" "error" "backup_mkdir_failed" "$run_path"
        return 1
    fi
    printf '%s\n' "$candidate"
}

fn_get_backup_mission_archive_path() {
    local run_dir="$1"
    printf '%s/conan-saved.tar\n' "$run_dir"
}

fn_get_backup_profile_archive_path() {
    local run_dir="$1"
    printf '%s/conan-panel.tar\n' "$run_dir"
}

fn_backup_status_label() {
    if [ "$1" = "1" ]; then
        fn_tr "backup_status_present"
    else
        fn_tr "backup_status_missing"
    fi
}

fn_list_backup_run_directories() {
    local backup_root=""

    backup_root="$(fn_get_backup_root)" || return 1
    if [ ! -d "$backup_root" ]; then
        return 0
    fi

    find "$backup_root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -r
}

fn_get_backup_run_summary() {
    local timestamp="$1"
    local run_dir=""
    local mission_present="0"
    local profile_present="0"
    local saved_archive=""

    run_dir="$(fn_get_backup_run_path "$timestamp")"
    saved_archive="$(fn_get_backup_mission_archive_path "$run_dir")"

    if [ -f "$saved_archive" ]; then
        mission_present="1"
    fi
    if [ -f "$(fn_get_backup_profile_archive_path "$run_dir")" ]; then
        profile_present="1"
    fi

    printf "$(fn_tr "backup_list_entry")" \
        "$timestamp" \
        "$(fn_backup_status_label "$mission_present")" \
        "$(fn_backup_status_label "$profile_present")"
    printf '\n'
}

fn_cleanup_old_backup_runs() {
    local backup_root=""
    local -a runs=()
    local index="0"
    local removed_any="0"

    backup_root="$(fn_get_backup_root)" || return 1
    mapfile -t runs < <(fn_list_backup_run_directories)

    for ((index=10; index<${#runs[@]}; index++)); do
        rm -rf -- "${backup_root}/${runs[$index]}"
        removed_any="1"
    done

    if [ "$removed_any" = "1" ]; then
        fn_log "$green" "dayz" "backup_retention_applied" "10"
    fi
}

fn_render_backup_list_box() {
    local terminal_width=""
    local content_width=""
    local body=""
    local timestamp=""
    local found_any="0"

    terminal_width="$(fn_get_terminal_width)"
    content_width=$((terminal_width - 6))
    if [ "$content_width" -gt 110 ]; then
        content_width="110"
    fi

    while IFS= read -r timestamp; do
        [ -z "$timestamp" ] && continue
        body+="$(fn_get_backup_run_summary "$timestamp")"
        found_any="1"
    done < <(fn_list_backup_run_directories)

    if [ "$found_any" != "1" ]; then
        body="$(fn_tr "backup_list_empty")"
    else
        body="${body%$'\n'}"
    fi

    printf '\n'
    fn_print_box "$(fn_tr "backup_list_title")" "$content_width" "$body"
}

fn_backup_restore_run() {
    local timestamp="$1"
    local run_dir=""
    local saved_archive=""
    local profile_archive=""
    local was_running="0"

    if [ -z "$timestamp" ]; then
        fn_log "$red" "error" "backup_restore_requires_timestamp"
        printf '%s\n' "$(fn_tr "backup_usage")"
        return 1
    fi

    run_dir="$(fn_get_backup_run_path "$timestamp")"
    if [ ! -d "$run_dir" ]; then
        fn_log "$red" "error" "backup_run_not_found" "$timestamp"
        return 1
    fi

    saved_archive="$(fn_get_backup_mission_archive_path "$run_dir")"
    profile_archive="$(fn_get_backup_profile_archive_path "$run_dir")"
    if [ ! -f "$saved_archive" ] || [ ! -f "$profile_archive" ]; then
        fn_log "$red" "error" "backup_run_incomplete" "$timestamp"
        return 1
    fi

    # Validate archives BEFORE stopping the server — avoids downtime on corrupt archives.
    if ! tar -tf "$saved_archive" >/dev/null 2>&1; then
        fn_log "$red" "error" "backup_archive_corrupt" "$saved_archive"
        return 1
    fi
    if ! tar -tf "$profile_archive" >/dev/null 2>&1; then
        fn_log "$red" "error" "backup_archive_corrupt" "$profile_archive"
        return 1
    fi

    fn_log "$green" "dayz" "backup_restore_started" "$timestamp"

    fn_status_dayz
    if [ "${dayzstatus}" != "0" ]; then
        was_running="1"
        fn_stop_dayz
    fi

    local restore_failed="0"
    local saved_path="${SERVERFILES}/ConanSandbox/Saved"
    local saved_bak="${saved_path}.restore-bak"
    local profile_path="${SERVER_DIR}/config.ini"
    local profile_bak="${SERVER_DIR}/config.ini.restore-bak"

    if ! mkdir -p "${SERVERFILES}/ConanSandbox" 2>/dev/null; then
        fn_log "$red" "error" "backup_mkdir_failed" "${SERVERFILES}/ConanSandbox"
        return 1
    fi
    # Remove leftover .restore-bak from any previous failed restore attempt
    [ -d "$saved_bak" ] && rm -rf -- "$saved_bak"
    if [ -d "$saved_path" ] && ! mv -- "$saved_path" "$saved_bak"; then
        fn_log "$red" "error" "backup_mv_failed" "$saved_path"
        restore_failed="1"
    fi
    if [ "$restore_failed" = "0" ]; then
        if ! tar -xf "$saved_archive" -C "${SERVERFILES}"; then
            fn_log "$red" "error" "backup_extract_failed" "$saved_archive"
            [ -d "$saved_path" ] && rm -rf -- "$saved_path"
            [ -d "$saved_bak" ] && mv -- "$saved_bak" "$saved_path"
            restore_failed="1"
        fi
        # saved_bak is kept until panel config restore also succeeds (atomic cleanup below)
    fi

    if [ "$restore_failed" != "1" ]; then
        # Remove leftover .restore-bak from any previous failed restore attempt
        [ -f "$profile_bak" ] && rm -f -- "$profile_bak"
        if [ -f "$profile_path" ] && ! mv -- "$profile_path" "$profile_bak"; then
            fn_log "$red" "error" "backup_mv_failed" "$profile_path"
            # Roll back save data to its previous state
            [ -d "$saved_path" ] && rm -rf -- "$saved_path"
            [ -d "$saved_bak" ] && mv -- "$saved_bak" "$saved_path"
            restore_failed="1"
        fi
        if [ "$restore_failed" = "0" ]; then
            if ! tar -xf "$profile_archive" -C "${SERVER_DIR}"; then
                fn_log "$red" "error" "backup_extract_failed" "$profile_archive"
                [ -f "$profile_path" ] && rm -f -- "$profile_path"
                [ -f "$profile_bak" ] && mv -- "$profile_bak" "$profile_path"
                # Roll back save data to its previous state
                [ -d "$saved_path" ] && rm -rf -- "$saved_path"
                [ -d "$saved_bak" ] && mv -- "$saved_bak" "$saved_path"
                restore_failed="1"
            else
                # Both restores succeeded — clean up both backups atomically
                rm -rf -- "$saved_bak"
                rm -f -- "$profile_bak"
            fi
        fi
    fi

    if [ "$was_running" = "1" ]; then
        fn_launch_dayz_process
    fi

    if [ "$restore_failed" = "1" ]; then
        return 1
    fi

    fn_log "$green" "success" "backup_restore_finished" "$timestamp"
}

fn_backup_dayz() {
    fn_backup_command
}

fn_backup_command() {
    local subcommand="${1:-}"
    local backup_root=""
    local backup_timestamp=""
    local run_dir=""
    local saved_archive=""
    local profile_archive=""
    local was_running="0"

    case "$subcommand" in
        "")
            backup_root="$(fn_get_backup_root)" || return 1
            if ! mkdir -p "$backup_root" 2>/dev/null; then
                fn_log "$red" "error" "backup_mkdir_failed" "$backup_root"
                return 1
            fi

            backup_timestamp="$(fn_create_backup_run_directory)" || return 1
            run_dir="$(fn_get_backup_run_path "$backup_timestamp")"

            saved_archive="$(fn_get_backup_mission_archive_path "$run_dir")"
            profile_archive="$(fn_get_backup_profile_archive_path "$run_dir")"

            fn_status_dayz
            if [ "${dayzstatus}" != "0" ]; then
                was_running="1"
                fn_stop_dayz
            fi

            local backup_failed="0"

            if [ -d "${SERVERFILES}/ConanSandbox/Saved" ]; then
                fn_log "$green" "dayz" "creating_backup_mission" "ConanSandbox/Saved"
                if ! tar --exclude='Logs' --exclude='*.log' --exclude='*.RPT' --exclude='*.mdmp' -cf "$saved_archive" -C "${SERVERFILES}" "ConanSandbox/Saved"; then
                    fn_log "$red" "error" "backup_tar_failed" "$saved_archive"
                    backup_failed="1"
                fi
            else
                fn_log "$yellow" "warning" "mission_backup_missing" "${SERVERFILES}/ConanSandbox/Saved"
            fi

            if [ "$backup_failed" != "1" ] && [ -f "${SERVER_DIR}/config.ini" ]; then
                fn_log "$green" "dayz" "creating_backup_profile" "${SERVER_DIR}/config.ini"
                [ -f "${SERVER_DIR}/workshop.cfg" ] || : > "${SERVER_DIR}/workshop.cfg"
                [ -f "${SERVER_DIR}/mod_timestamps.json" ] || printf '{}\n' > "${SERVER_DIR}/mod_timestamps.json"
                if ! tar -cf "$profile_archive" -C "${SERVER_DIR}" "config.ini" "workshop.cfg" "mod_timestamps.json" 2>/dev/null; then
                    fn_log "$red" "error" "backup_tar_failed" "$profile_archive"
                    backup_failed="1"
                fi
            elif [ "$backup_failed" != "1" ]; then
                fn_log "$yellow" "warning" "serverprofile_missing" "${SERVERPROFILE}"
            fi

            # Fail if nothing was actually backed up
            if [ ! -f "$saved_archive" ] && [ ! -f "$profile_archive" ]; then
                fn_log "$red" "error" "backup_nothing_to_backup"
                rm -rf -- "$run_dir"
                backup_failed="1"
            fi

            if [ "$backup_failed" = "1" ]; then
                [ -d "$run_dir" ] && rm -rf -- "$run_dir"
                if [ "$was_running" = "1" ]; then
                    fn_launch_dayz_process
                fi
                return 1
            fi

            fn_log "$green" "success" "backup_created" "$backup_timestamp"
            fn_log "$green" "dayz" "backup_cleanup"
            fn_cleanup_old_backup_runs

            if [ "$was_running" = "1" ]; then
                fn_launch_dayz_process
            fi
            ;;
        list|liste|anzeigen)
            fn_render_backup_list_box
            ;;
        restore|wiederherstellen)
            shift || true
            fn_backup_restore_run "${1:-}"
            ;;
        *)
            fn_log "$red" "error" "backup_invalid_subcommand" "$subcommand"
            printf '%s\n' "$(fn_tr "backup_usage")"
            return 1
            ;;
    esac
}
