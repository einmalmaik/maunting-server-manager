#!/bin/bash

fn_get_crontab_without_named_blocks() {
    local block_name_a="${1:-}"
    local block_name_b="${2:-}"

    crontab -l 2>/dev/null | awk \
        -v block_a="$block_name_a" \
        -v block_b="$block_name_b" \
        '
            /^# BEGIN / {
                if ($0 == "# BEGIN " block_a || (block_b != "" && $0 == "# BEGIN " block_b)) {
                    skip = 1
                    next
                }
            }
            /^# END / {
                if ($0 == "# END " block_a || (block_b != "" && $0 == "# END " block_b)) {
                    skip = 0
                    next
                }
            }
            !skip { print }
        '
}

fn_get_crontab_without_managed_block() {
    local server="${SERVER_NAME:-default}"
    fn_get_crontab_without_named_blocks \
        "CONANSERVER AUTORESTART ${server}" \
        "CONANSERVER AUTORESTART"
}

fn_join_with_delimiter() {
    local delimiter="$1"
    shift || true
    local output=""
    local item=""

    for item in "$@"; do
        if [ -n "$output" ]; then
            output+="${delimiter}"
        fi
        output+="${item}"
    done

    printf '%s' "$output"
}

fn_is_valid_interval_hours() {
    case "$1" in
        1|2|3|4|6|8|12|24) return 0 ;;
        *) return 1 ;;
    esac
}

fn_is_valid_workshop_autoupdate_minutes() {
    case "$1" in
        10|30) return 0 ;;
        *) return 1 ;;
    esac
}

fn_get_managed_crontab_block() {
    local block_name="$1"

    crontab -l 2>/dev/null | awk \
        -v header="# BEGIN ${block_name}" \
        -v footer="# END ${block_name}" \
        '$0==header{p=1;next} $0==footer{p=0;next} p{print}'
}

fn_has_managed_crontab_block() {
    local block_name="$1"
    local current_crontab=""

    current_crontab="$(crontab -l 2>/dev/null || true)"
    printf '%s\n' "$current_crontab" | grep -qF "# BEGIN ${block_name}"
}

fn_expand_interval_times() {
    local interval_hours="$1"
    local hour="0"

    while [ "$hour" -lt 24 ]; do
        printf '%02d:00\n' "$hour"
        hour=$((hour + interval_hours))
    done
}

fn_get_effective_autorestart_times() {
    local -a configured_times=()

    case "$autorestart_mode" in
        times)
            if [ -n "$autorestart_times" ]; then
                read -r -a configured_times <<< "$autorestart_times"
                printf '%s\n' "${configured_times[@]}" | sort -u
            fi
            ;;
        interval)
            if fn_is_valid_interval_hours "$autorestart_interval_hours"; then
                fn_expand_interval_times "$autorestart_interval_hours"
            fi
            ;;
    esac
}

fn_get_autorestart_times_from_crontab() {
    local server="${SERVER_NAME:-default}"

    fn_get_managed_crontab_block "CONANSERVER AUTORESTART ${server}" | \
        grep -v '^#' | \
        awk '
            NF >= 2 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ {
                printf "%02d:%02d\n", $2, $1
            }
        ' | sort -u
}

fn_get_autorestart_mode_name() {
    case "$autorestart_mode" in
        times) fn_tr "autorestart_mode_times_name" ;;
        interval) fn_tr "autorestart_mode_interval_name" ;;
        *) fn_tr "autorestart_mode_off_name" ;;
    esac
}

fn_get_autorestart_times_display() {
    local -a effective_times=()

    mapfile -t effective_times < <(fn_get_effective_autorestart_times)
    if [ "${#effective_times[@]}" -eq 0 ]; then
        printf '%s' "-"
        return 0
    fi

    fn_join_with_delimiter ", " "${effective_times[@]}"
}

fn_get_autorestart_summary() {
    local times_display=""

    case "$autorestart_mode" in
        times)
            times_display="$(fn_get_autorestart_times_display)"
            if [ "$times_display" = "-" ]; then
                fn_tr "autorestart_summary_off"
            else
                printf "$(fn_tr "autorestart_summary_times")" "$times_display"
            fi
            ;;
        interval)
            if fn_is_valid_interval_hours "$autorestart_interval_hours"; then
                printf "$(fn_tr "autorestart_summary_interval")" "$autorestart_interval_hours"
            else
                fn_tr "autorestart_summary_off"
            fi
            ;;
        *)
            fn_tr "autorestart_summary_off"
            ;;
    esac
}

fn_get_help_restart_display() {
    local mode_name=""
    local summary=""

    mode_name="$(fn_get_autorestart_mode_name)"
    case "$autorestart_mode" in
        times)
            summary="$(fn_get_autorestart_times_display)"
            ;;
        *)
            summary="$(fn_get_autorestart_summary)"
            ;;
    esac

    if [ -z "$summary" ] || [ "$summary" = "-" ] || [ "$mode_name" = "$summary" ]; then
        printf '%s' "$mode_name"
    else
        printf '%s | %s' "$mode_name" "$summary"
    fi
}

fn_apply_autorestart_crontab() {
    local -a times=("$@")
    local cleaned_crontab=""
    local temp_file=""
    local time_value=""
    local hour=""
    local minute=""
    local script_escaped=""
    local log_escaped=""

    cleaned_crontab="$(fn_get_crontab_without_managed_block)"
    temp_file="$(mktemp)"
    script_escaped="$(printf '%q' "$SCRIPT_PATH")"
    log_escaped="$(printf '%q' "$AUTORESTART_CRON_LOG")"

    if [ -n "$cleaned_crontab" ]; then
        printf '%s\n' "$cleaned_crontab" > "$temp_file"
    fi

    if [ "${#times[@]}" -gt 0 ]; then
        if [ -s "$temp_file" ]; then
            printf '\n' >> "$temp_file"
        fi

        local server="${SERVER_NAME:-default}"
        local server_escaped
        server_escaped="$(printf '%q' "$server")"
        {
            printf '# BEGIN CONANSERVER AUTORESTART %s\n' "$server"
            for time_value in "${times[@]}"; do
                hour="${time_value%%:*}"
                minute="${time_value##*:}"
                printf '%s %s * * * bash %s --server %s restart >> %s 2>&1 # conanserver-autorestart\n' \
                    "$minute" "$hour" "$script_escaped" "$server_escaped" "$log_escaped"
            done
            printf '# END CONANSERVER AUTORESTART %s\n' "$server"
        } >> "$temp_file"
    fi

    if ! crontab "$temp_file"; then
        rm -f "$temp_file"
        fn_log "$red" "error" "crontab_update_failed"
        return 1
    fi

    rm -f "$temp_file"
}

fn_normalize_autorestart_times() {
    local -a input_times=("$@")
    local time_value=""

    if [ "${#input_times[@]}" -eq 0 ]; then
        fn_log "$red" "error" "autorestart_times_required"
        exit 1
    fi

    for time_value in "${input_times[@]}"; do
        if [[ ! "$time_value" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
            fn_log "$red" "error" "autorestart_invalid_time" "$time_value"
            exit 1
        fi
    done

    printf '%s\n' "${input_times[@]}" | sort -u
}

fn_set_autorestart_state() {
    local mode="$1"
    local interval_hours="$2"
    shift 2 || true
    local -a times=("$@")

    autorestart_mode="$mode"
    autorestart_interval_hours="$interval_hours"
    autorestart_times="$(fn_join_with_delimiter " " "${times[@]}")"

    fn_write_config_string "autorestart_mode" "$autorestart_mode"
    fn_write_config_string "autorestart_times" "$autorestart_times"
    fn_write_config_string "autorestart_interval_hours" "$autorestart_interval_hours"
}

fn_autorestart_reject_extra_args() {
    if [ "$#" -gt 0 ]; then
        fn_log "$red" "error" "autorestart_unexpected_args" "$(fn_join_with_delimiter " " "$@")"
        return 1
    fi

    return 0
}

# ── Workshop auto-update crontab management ───────────────────────────────────

fn_get_crontab_without_workshop_autoupdate_block() {
    local server="${SERVER_NAME:-default}"
    fn_get_crontab_without_named_blocks \
        "CONANSERVER WORKSHOP AUTOUPDATE ${server}" \
        "CONANSERVER WORKSHOP AUTOUPDATE"
}

fn_apply_workshop_autoupdate_crontab() {
    local interval_minutes="${1:-}"
    local cleaned_crontab temp_file script_escaped cron_schedule=""
    local log_escaped="" hours=""

    cleaned_crontab="$(fn_get_crontab_without_workshop_autoupdate_block)"
    temp_file="$(mktemp)"
    script_escaped="$(printf '%q' "$SCRIPT_PATH")"
    log_escaped="$(printf '%q' "$WORKSHOP_AUTOUPDATE_LOG")"

    if [ -n "$cleaned_crontab" ]; then
        printf '%s\n' "$cleaned_crontab" > "$temp_file"
    fi

    if [ -n "$interval_minutes" ]; then
        if [ "$interval_minutes" -lt 60 ]; then
            cron_schedule="*/${interval_minutes} * * * *"
        elif [ $((interval_minutes % 60)) -eq 0 ]; then
            hours=$((interval_minutes / 60))
            cron_schedule="0 */${hours} * * *"
        else
            rm -f "$temp_file"
            fn_log "$red" "error" "workshop_autoupdate_invalid_minutes" "$interval_minutes"
            return 1
        fi

        if [ -s "$temp_file" ]; then
            printf '\n' >> "$temp_file"
        fi
        local server="${SERVER_NAME:-default}"
        local server_escaped
        server_escaped="$(printf '%q' "$server")"
        {
            printf '# BEGIN CONANSERVER WORKSHOP AUTOUPDATE %s\n' "$server"
            printf '%s bash %s --server %s workshop >> %s 2>&1 # conanserver-workshop-autoupdate\n' \
                "$cron_schedule" "$script_escaped" "$server_escaped" "$log_escaped"
            printf '# END CONANSERVER WORKSHOP AUTOUPDATE %s\n' "$server"
        } >> "$temp_file"
    fi

    if ! crontab "$temp_file"; then
        rm -f "$temp_file"
        fn_log "$red" "error" "crontab_update_failed"
        return 1
    fi

    rm -f "$temp_file"
}

fn_get_workshop_autoupdate_interval_minutes_from_crontab() {
    local server="${SERVER_NAME:-default}"
    local entry=""

    entry="$(fn_get_managed_crontab_block "CONANSERVER WORKSHOP AUTOUPDATE ${server}" | grep -v '^#' | grep -m1 '[0-9]' || true)"
    if [ -z "$entry" ]; then
        return 0
    fi

    if [[ "$entry" =~ ^\*/([0-9]+)[[:space:]]+\*[[:space:]]+\*[[:space:]]+\*[[:space:]]+\*[[:space:]] ]]; then
        printf '%s\n' "${BASH_REMATCH[1]}"
        return 0
    fi

    if [[ "$entry" =~ ^0[[:space:]]+\*/([0-9]+)[[:space:]]+\*[[:space:]]+\*[[:space:]]+\*[[:space:]] ]]; then
        printf '%s\n' "$((BASH_REMATCH[1] * 60))"
        return 0
    fi
}

fn_get_workshop_autoupdate_display() {
    local interval_minutes="${1:-}"

    if [ -z "$interval_minutes" ]; then
        printf '%s' "$(fn_tr "workshop_autoupdate_none")"
        return 0
    fi

    if [ "$interval_minutes" -lt 60 ]; then
        printf "$(fn_tr "workshop_autoupdate_minutes_label")" "$interval_minutes"
        return 0
    fi

    printf "$(fn_tr "workshop_autoupdate_interval_label")" "$((interval_minutes / 60))"
}

fn_workshop_autoupdate_set_interval() {
    local interval_hours="${1:-}"

    if [ -z "$interval_hours" ]; then
        fn_log "$red" "error" "workshop_autoupdate_interval_required"
        printf 'Usage: workshop autoupdate set interval <1|2|3|4|6|8|12|24>\n'
        return 1
    fi

    if ! fn_is_valid_interval_hours "$interval_hours"; then
        fn_log "$red" "error" "workshop_autoupdate_invalid_interval" "$interval_hours"
        return 1
    fi

    fn_require_crontab
    fn_acquire_lock "workshop"
    if ! fn_apply_workshop_autoupdate_crontab "$((interval_hours * 60))"; then
        return 1
    fi
    fn_log "$green" "success" "workshop_autoupdate_set" "$interval_hours"
}

fn_workshop_autoupdate_set_minutes() {
    local interval_minutes="${1:-}"

    if [ -z "$interval_minutes" ]; then
        fn_log "$red" "error" "workshop_autoupdate_minutes_required"
        printf 'Usage: workshop autoupdate set minutes <10|30>\n'
        return 1
    fi

    if ! fn_is_valid_workshop_autoupdate_minutes "$interval_minutes"; then
        fn_log "$red" "error" "workshop_autoupdate_invalid_minutes" "$interval_minutes"
        return 1
    fi

    fn_require_crontab
    fn_acquire_lock "workshop"
    if ! fn_apply_workshop_autoupdate_crontab "$interval_minutes"; then
        return 1
    fi
    fn_log "$green" "success" "workshop_autoupdate_set_minutes" "$interval_minutes"
}

fn_workshop_autoupdate_clear() {
    fn_require_crontab
    fn_acquire_lock "workshop"
    if ! fn_apply_workshop_autoupdate_crontab; then
        return 1
    fi
    fn_log "$green" "success" "workshop_autoupdate_cleared"
}

fn_workshop_autoupdate_list() {
    local interval_minutes=""

    interval_minutes="$(fn_get_workshop_autoupdate_interval_minutes_from_crontab)"
    if [ -n "$interval_minutes" ]; then
        if [ "$interval_minutes" -lt 60 ]; then
            fn_log "$lightblue" "info" "workshop_autoupdate_minutes_label" "$interval_minutes"
        else
            fn_log "$lightblue" "info" "workshop_autoupdate_interval_label" "$((interval_minutes / 60))"
        fi
    else
        fn_log "$lightblue" "info" "workshop_autoupdate_none"
    fi
}

fn_autorestart_command() {
    local subcommand="${1:-}"
    local mode_token=""
    local interval_hours=""
    local -a normalized_times=()
    local -a extra_args=()
    shift || true

    if [ -z "$subcommand" ]; then
        fn_log "$yellow" "warning" "autorestart_no_subcommand"
        printf '%s\n' "$(fn_tr "autorestart_usage")"
        return 1
    fi

    case "$subcommand" in
        set|setzen)
            mode_token="${1:-}"
            if [ -z "$mode_token" ]; then
                fn_log "$yellow" "warning" "autorestart_no_subcommand"
                printf '%s\n' "$(fn_tr "autorestart_usage")"
                return 1
            fi
            shift || true

            case "$mode_token" in
                times|zeiten)
                    fn_require_crontab
                    fn_acquire_lock
                    mapfile -t normalized_times < <(fn_normalize_autorestart_times "$@")
                    if ! fn_apply_autorestart_crontab "${normalized_times[@]}"; then
                        return 1
                    fi
                    fn_set_autorestart_state "times" "" "${normalized_times[@]}"
                    fn_log "$green" "success" "autorestart_saved_times" "$(fn_join_with_delimiter ", " "${normalized_times[@]}")"
                    ;;
                interval|intervall)
                    interval_hours="${1:-}"
                    if [ -z "$interval_hours" ]; then
                        fn_log "$red" "error" "autorestart_interval_required"
                        printf '%s\n' "$(fn_tr "autorestart_usage")"
                        return 1
                    fi
                    if ! fn_is_valid_interval_hours "$interval_hours"; then
                        fn_log "$red" "error" "autorestart_invalid_interval" "$interval_hours"
                        return 1
                    fi
                    extra_args=("${@:2}")
                    if ! fn_autorestart_reject_extra_args "${extra_args[@]}"; then
                        return 1
                    fi
                    fn_require_crontab
                    fn_acquire_lock
                    mapfile -t normalized_times < <(fn_expand_interval_times "$interval_hours")
                    if ! fn_apply_autorestart_crontab "${normalized_times[@]}"; then
                        return 1
                    fi
                    fn_set_autorestart_state "interval" "$interval_hours"
                    fn_log "$green" "success" "autorestart_saved_interval" "$interval_hours"
                    ;;
                *)
                    if [[ "$mode_token" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
                        fn_require_crontab
                        fn_acquire_lock
                        mapfile -t normalized_times < <(fn_normalize_autorestart_times "$mode_token" "$@")
                        if ! fn_apply_autorestart_crontab "${normalized_times[@]}"; then
                            return 1
                        fi
                        fn_set_autorestart_state "times" "" "${normalized_times[@]}"
                        fn_log "$green" "success" "autorestart_saved_times" "$(fn_join_with_delimiter ", " "${normalized_times[@]}")"
                    else
                        fn_log "$red" "error" "autorestart_invalid_mode" "$mode_token"
                        printf '%s\n' "$(fn_tr "autorestart_usage")"
                        return 1
                    fi
                    ;;
            esac
            ;;
        list|liste|anzeigen)
            if ! fn_autorestart_reject_extra_args "$@"; then
                return 1
            fi
            fn_render_autorestart_status_box
            ;;
        clear|off|deaktivieren)
            if ! fn_autorestart_reject_extra_args "$@"; then
                return 1
            fi
            fn_require_crontab
            fn_acquire_lock
            if ! fn_apply_autorestart_crontab; then
                return 1
            fi
            fn_set_autorestart_state "off" ""
            fn_log "$green" "success" "autorestart_cleared"
            ;;
        *)
            fn_log "$red" "error" "autorestart_invalid_subcommand" "$subcommand"
            printf '%s\n' "$(fn_tr "autorestart_usage")"
            return 1
            ;;
    esac
}
