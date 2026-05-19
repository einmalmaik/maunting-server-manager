#!/bin/bash

fn_workshop_normalize_mod_name() {
    local mod_name="${1:-}"
    local fallback="${2:-}"

    mod_name="$(printf '%s' "${mod_name:-$fallback}" | tr '[:upper:]' '[:lower:]')"
    mod_name="${mod_name//$'\r'/}"
    mod_name="${mod_name//$'\n'/}"
    mod_name="${mod_name//$'\t'/ }"
    mod_name="${mod_name//[\/\\]/}"
    mod_name="$(printf '%s' "$mod_name" | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')"
    while [[ "$mod_name" == .* ]]; do mod_name="${mod_name#.}"; done
    [ -n "$mod_name" ] || mod_name="$fallback"
    printf '%s' "$mod_name"
}

fn_workshop_sync_config_mod_name() {
    local old_name="${1:-}" new_name="${2:-}"
    local config_key current_val new_val

    [ -n "$old_name" ] || return 0
    [ -n "$new_name" ] || return 0
    [ "$old_name" = "$new_name" ] && return 0
    [ -f "$CONFIG_FILE" ] || return 0

    for config_key in workshop servermods; do
        current_val="$(grep -m1 "^${config_key}=" "$CONFIG_FILE" 2>/dev/null | tr -d '\r' | sed "s/^${config_key}=//; s/^\"//; s/\"$//" || true)"
        new_val="$(printf '%s' "$current_val" | tr ';' '\n' | awk -v old="@${old_name}" -v new="@${new_name}" '
            {
                if ($0 == old) {
                    print new
                } else {
                    print $0
                }
            }
        ' | paste -sd ';' -)"
        fn_write_config_string "$config_key" "${new_val:-}"
    done
}

fn_workshop_update_registered_mod_name() {
    local mod_id="${1:-}" current_name="${2:-}" resolved_name="${3:-}"
    local workshop_cfg="${WORKSHOP_CFG}" tmp_cfg=""

    [ -n "$mod_id" ] || return 0
    [ -n "$resolved_name" ] || return 0
    [ -f "$workshop_cfg" ] || return 0
    [ "$current_name" = "$resolved_name" ] && return 0

    tmp_cfg="$(mktemp "${workshop_cfg}.XXXXXX" 2>/dev/null || true)"
    [ -n "$tmp_cfg" ] || return 1

    if awk -v mod_id="$mod_id" -v mod_name="$resolved_name" '
        BEGIN { replaced = 0 }
        {
            if ($1 == mod_id) {
                print mod_id " " mod_name
                replaced = 1
            } else {
                print $0
            }
        }
        END { exit(replaced ? 0 : 1) }
    ' "$workshop_cfg" > "$tmp_cfg"; then
        mv "$tmp_cfg" "$workshop_cfg"
        fn_workshop_sync_config_mod_name "$current_name" "$resolved_name"
    else
        rm -f "$tmp_cfg"
        return 1
    fi
}

fn_workshop_ensure_mod_symlink() {
    local mod_id="${1:-}" mod_name="${2:-}"
    local workshopfolder="${WORKSHOPFOLDER}"
    local target_link target_target current_target

    [ -n "$mod_id" ] || return 0
    [ -n "$mod_name" ] || return 0
    [ -d "${workshopfolder}/${mod_id}" ] || return 0

    target_link="${SERVERFILES}/@${mod_name}"
    target_target="${workshopfolder}/${mod_id}"

    if [ -L "$target_link" ] && [ ! -e "$target_link" ]; then
        rm -f "$target_link"
        fn_log "$lightblue" "info" "broken_symlink_removed" "$mod_name"
    fi

    if [ -L "$target_link" ]; then
        current_target="$(readlink "$target_link" 2>/dev/null || true)"
        if [ "$current_target" != "$target_target" ]; then
            rm -f "$target_link"
            ln -snf "$target_target" "$target_link"
        fi
    elif [ ! -e "$target_link" ]; then
        ln -snf "$target_target" "$target_link"
    else
        fn_log "$yellow" "warning" "cannot_symlink_dir_exists" "$mod_name"
    fi
}

fn_workshop_copy_all_mod_keys() {
    return 0
}

fn_lowercase_mod_files() {
    local moddir="$1"

    # Conan Exiles Enhanced must keep .pak filenames unchanged. Renaming paks can
    # make the server fail to load mods.
    return 0

    if [ -d "$moddir" ]; then
        find "$moddir" -depth -exec bash -c '
            dir=$(dirname "$1")
            base=$(basename "$1")
            lower=$(echo "$base" | tr "[:upper:]" "[:lower:]")
            if [ "$base" != "$lower" ]; then
                if [ -e "$dir/$lower" ]; then
                    :
                else
                    mv "$1" "$dir/$lower"
                fi
            fi
        ' _ {} \;
    fi
}

fn_workshop_clear_download_cache() {
    local workshopfolder="${WORKSHOPFOLDER}"
    local workshop_manifest="${SERVERFILES}/steamapps/workshop/appworkshop_${workshop_appid}.acf"
    local download_root="${SERVERFILES}/steamapps/workshop/downloads/${workshop_appid}"
    local mod_id mod_cache_dir

    [ -n "${workshop_appid:-}" ] || return 0

    fn_log "$lightblue" "info" "workshop_cache_clearing"

    for mod_id in "$@"; do
        [[ "$mod_id" =~ ^[0-9]+$ ]] || continue
        rm -rf -- "${workshopfolder}/${mod_id}"
        mod_cache_dir="${download_root}/${mod_id}"
        rm -rf -- "$mod_cache_dir"
    done

    rm -f -- "$workshop_manifest"
    fn_log "$lightblue" "info" "workshop_cache_cleared"
}

fn_workshop_prepare_fresh_download() {
    local workshopfolder="${WORKSHOPFOLDER}"
    local backup_root="${SERVERFILES}/steamapps/workshop/.conanserver-workshop-backup-${workshop_appid}-$$"
    local mod_id mod_dir backup_dir

    mkdir -p -- "$backup_root" || return 1

    for mod_id in "$@"; do
        [[ "$mod_id" =~ ^[0-9]+$ ]] || continue
        mod_dir="${workshopfolder}/${mod_id}"
        backup_dir="${backup_root}/${mod_id}"

        if [ -d "$mod_dir" ]; then
            if ! mv -- "$mod_dir" "$backup_dir"; then
                fn_workshop_restore_download_backup "$backup_root" || true
                return 1
            fi
        fi
    done

    printf '%s' "$backup_root"
}

fn_workshop_find_pak() {
    local mod_id="${1:-}"
    local mod_dir="${WORKSHOPFOLDER}/${mod_id}"
    [ -d "$mod_dir" ] || return 1
    find "$mod_dir" -maxdepth 1 -type f -name "*.pak" -print | sort | head -n 1
}

fn_workshop_refresh_conan_modlist() {
    local workshop_cfg="${WORKSHOP_CFG}"
    local modlist="${CONAN_MODLIST}"
    local line mod_id mod_name pak_path

    mkdir -p "${CONAN_MODS_DIR}" || return 1
    : > "$modlist" || return 1

    [ -f "$workshop_cfg" ] || return 0
    while IFS= read -r line; do
        mod_id="$(echo "$line" | awk '{print $1}')"
        [[ "$mod_id" =~ ^[0-9]+$ ]] || continue
        pak_path="$(fn_workshop_find_pak "$mod_id" || true)"
        if [ -n "$pak_path" ]; then
            printf '%s\n' "$pak_path" >> "$modlist"
        else
            fn_log "$yellow" "warning" "mod_pak_missing" "$mod_id"
        fi
    done < "$workshop_cfg"
    chmod 600 "$modlist" 2>/dev/null || true
}

fn_workshop_restore_download_backup() {
    local backup_root="${1:-}"
    local workshopfolder="${WORKSHOPFOLDER}"
    local backup_dir mod_id mod_dir

    [ -d "$backup_root" ] || return 0

    for backup_dir in "$backup_root"/*; do
        [ -d "$backup_dir" ] || continue
        mod_id="$(basename "$backup_dir")"
        mod_dir="${workshopfolder}/${mod_id}"
        rm -rf -- "$mod_dir"
        mv -- "$backup_dir" "$mod_dir" || return 1
    done

    rmdir -- "$backup_root" 2>/dev/null || true
}

fn_workshop_discard_download_backup() {
    local backup_root="${1:-}"
    [ -d "$backup_root" ] || return 0
    rm -rf -- "$backup_root"
}

# ── Update check ──────────────────────────────────────────────────────────────
# Outputs newline-separated mod IDs that need downloading/updating.
# Rules:
#   * Mod directory does not exist  ->  always include (first install)
#   * steam time_updated > local timestamp in mod_timestamps.json  ->  include (update available)
#   * Otherwise  ->  skip (already current)

fn_workshop_check_updates() {
    local workshop_cfg="${WORKSHOP_CFG}"
    local timestamp_file="${TIMESTAMP_FILE}"
    local workshopfolder="${WORKSHOPFOLDER}"
    local -a all_ids=()
    local -a check_ids=()
    local mod_id mod_name

    [ -f "$workshop_cfg" ] || return 0

    while IFS=' ' read -r mod_id mod_name; do
        [[ "$mod_id" =~ ^[0-9]+$ ]] || continue
        all_ids+=("$mod_id")
    done < "$workshop_cfg"

    [ "${#all_ids[@]}" -eq 0 ] && return 0

    for mod_id in "${all_ids[@]}"; do
        if [ ! -d "${workshopfolder}/${mod_id}" ]; then
            # No local copy -> must install
            printf '%s\n' "$mod_id"
        else
            check_ids+=("$mod_id")
        fi
    done

    [ "${#check_ids[@]}" -eq 0 ] && return 0

    # Build POST body for GetPublishedFileDetails (no API key required)
    local post_body="itemcount=${#check_ids[@]}"
    local i=0
    for mod_id in "${check_ids[@]}"; do
        post_body+="&publishedfileids[${i}]=${mod_id}"
        i=$((i + 1))
    done

    # Query Steam API
    local steam_json
    steam_json="$(curl -sS --max-time 15 -X POST \
        "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/" \
        -d "$post_body" 2>/dev/null)" || {
        fn_log "$yellow" "warning" "steam_api_unreachable"
        printf '%s\n' "${check_ids[@]}"
        return 0
    }

    if ! printf '%s' "$steam_json" | jq -e . >/dev/null 2>&1; then
        fn_log "$yellow" "warning" "steam_api_unreachable"
        printf '%s\n' "${check_ids[@]}"
        return 0
    fi

    local local_ts_json="{}"
    [ -f "$timestamp_file" ] && local_ts_json="$(cat "$timestamp_file")"

    local outdated_ids
    local jq_exit=0
    outdated_ids="$(printf '%s' "$steam_json" | jq -r \
        --argjson local "$local_ts_json" \
        '.response.publishedfiledetails[]?
         | select(.result == 1)
         | . as $d
         | ($local[($d.publishedfileid | tostring)] // 0) as $local_ts
         | if ($d.time_updated > $local_ts) then $d.publishedfileid else empty end')" || jq_exit=$?

    if [ "$jq_exit" -ne 0 ]; then
        # jq failure — treat all as outdated (fallback)
        printf '%s\n' "${check_ids[@]}"
    elif [ -n "$outdated_ids" ]; then
        printf '%s\n' "$outdated_ids"
    fi
    # empty + jq success = all mods current → print nothing
}

# ── Smart download orchestrator ───────────────────────────────────────────────
# Checks which mods actually need an update, downloads only those.
# Called by: conanserver.sh workshop, fn_prestart_dayz (start/restart)

fn_workshop_mods() {
    local workshop_cfg="${WORKSHOP_CFG}"
    local workshopfolder="${WORKSHOPFOLDER}"
    local timestamp_file="${TIMESTAMP_FILE}"
    local updated_workshop_cfg=""
    local mod_id mod_name resolved_mod_name mod_meta_file line

    if [ ! -f "$workshop_cfg" ]; then
        touch "$workshop_cfg"
        chmod 600 "$workshop_cfg"
    fi

    if [ ! -f "$timestamp_file" ]; then
        printf '{}\n' > "$timestamp_file"
        fn_log "$lightblue" "info" "timestamp_file_created" "$timestamp_file"
    fi

    fn_log "$lightblue" "info" "workshop_update_checking"

    local -a outdated_ids=()
    while IFS= read -r line; do
        [ -n "$line" ] && outdated_ids+=("$line")
    done < <(fn_workshop_check_updates)

    if [ "${#outdated_ids[@]}" -eq 0 ]; then
        fn_log "$green" "dayz" "workshop_already_uptodate"
    else
        fn_log "$green" "dayz" "workshop_update_needed" "$(IFS=','; printf '%s' "${outdated_ids[*]}")"
        fn_workshop_mods_selective "${outdated_ids[@]}"
    fi

    # Update workshop.cfg with names resolved from meta.cpp (all mods, not just updated ones)
    while IFS= read -r line; do
        mod_id="$(echo "$line" | awk '{print $1}')"
        mod_name="$(echo "$line" | cut -d ' ' -f 2-)"

        if [[ ! "$mod_id" =~ ^[0-9]+$ ]]; then
            continue
        fi

        mod_meta_file="${workshopfolder}/${mod_id}/meta.cpp"
        if [ -f "$mod_meta_file" ]; then
            resolved_mod_name="$(grep -m 1 '^[[:space:]]*name[[:space:]]*=' "$mod_meta_file" | cut -d '"' -f 2)"
        else
            resolved_mod_name="${mod_name:-Unknown}"
        fi

        resolved_mod_name="$(fn_workshop_normalize_mod_name "$resolved_mod_name" "${mod_name:-$mod_id}")"
        fn_workshop_sync_config_mod_name "$mod_name" "$resolved_mod_name"
        updated_workshop_cfg+="${mod_id} ${resolved_mod_name}"$'\n'
    done < "$workshop_cfg"

    if [ -n "$updated_workshop_cfg" ]; then
        printf '%s' "$updated_workshop_cfg" > "$workshop_cfg"
        fn_log "$lightblue" "info" "workshop_cfg_updated"
    fi

    fn_workshop_refresh_conan_modlist
}

# ── Selective downloader ──────────────────────────────────────────────────────
# Called by fn_workshop_mods() with the outdated IDs, and directly by the
# panel bridge for "update selective" requests.

fn_workshop_mods_selective() {
    local -a requested_ids=("$@")
    local workshopfolder="${WORKSHOPFOLDER}"
    local timestamp_file="${TIMESTAMP_FILE}"
    local workshop_cfg="${WORKSHOP_CFG}"
    local -a download_args=()
    local mod_id mod_name actual_mod_name resolved_mod_name mod_meta_file mod_last_modified prev_timestamp webhook_message
    local backup_root="" download_failed="0"

    if [ "${#requested_ids[@]}" -eq 0 ]; then
        fn_log "$yellow" "warning" "no_mods_specified"
        return 0
    fi

    if [ -z "${workshop_appid:-}" ]; then
        fn_log "$red" "error" "dayz_id_not_set"
        return 1
    fi

    if [ -z "${steamlogin:-}" ]; then
        fn_log "$red" "error" "steamlogin_not_set"
        return 1
    fi

    for mod_id in "${requested_ids[@]}"; do
        if [[ ! "$mod_id" =~ ^[0-9]+$ ]]; then
            fn_log "$red" "error" "invalid_mod_id" "$mod_id"
            return 1
        fi
        download_args+=(+workshop_download_item "$workshop_appid" "$mod_id")
    done

    [ -f "$timestamp_file" ] || printf '{}\n' > "$timestamp_file"

    # Keep the current workshop content restorable while forcing a fresh download.
    backup_root="$(fn_workshop_prepare_fresh_download "${requested_ids[@]}")" || {
        fn_log "$red" "error" "workshop_backup_prepare_failed"
        return 1
    }

    fn_workshop_clear_download_cache "${requested_ids[@]}"

    # SteamCMD download (only the requested IDs)
    if ! "${STEAMCMD_DIR}/steamcmd.sh" +force_install_dir "${SERVERFILES}" \
        +login "${steamlogin}" "${download_args[@]}" +quit; then
        download_failed="1"
    fi

    for mod_id in "${requested_ids[@]}"; do
        if [ ! -d "${workshopfolder}/${mod_id}" ]; then
            download_failed="1"
            break
        fi
    done

    if [ "$download_failed" = "1" ]; then
        fn_workshop_restore_download_backup "$backup_root" || true
        fn_log "$red" "error" "steamcmd_download_failed"
        return 1
    fi

    fn_workshop_discard_download_backup "$backup_root"

    fn_log "$green" "dayz" "lowercase_fix_done"

    # Symlinks, timestamps, Discord notification
    for mod_id in "${requested_ids[@]}"; do
        if [ ! -d "${workshopfolder}/${mod_id}" ]; then
            fn_log "$yellow" "warning" "mod_dir_missing" "$mod_id"
            continue
        fi

        actual_mod_name=""
        mod_name="$(grep -m1 "^${mod_id} " "$workshop_cfg" 2>/dev/null | cut -d' ' -f2-)"
        mod_meta_file="${workshopfolder}/${mod_id}/meta.cpp"
        if [ -f "$mod_meta_file" ]; then
            actual_mod_name="$(grep -m 1 '^[[:space:]]*name[[:space:]]*=' "$mod_meta_file" | cut -d '"' -f 2)"
        fi
        resolved_mod_name="$(fn_workshop_normalize_mod_name "$actual_mod_name" "${mod_name:-$mod_id}")"
        fn_workshop_update_registered_mod_name "$mod_id" "$mod_name" "$resolved_mod_name" || true
        mod_name="$resolved_mod_name"

        # Timestamp update + optional Discord notification
        if [ -f "$mod_meta_file" ]; then
            mod_last_modified="$(date -r "$mod_meta_file" +%s 2>/dev/null)" || mod_last_modified=0
            mod_last_modified="${mod_last_modified:-0}"
            prev_timestamp="$(jq -r --arg mod "$mod_id" '.[$mod] // 0' "$timestamp_file")"
            prev_timestamp="${prev_timestamp:-0}"

            [[ "$mod_last_modified" =~ ^[0-9]+$ ]] || mod_last_modified=0
            [[ "$prev_timestamp" =~ ^[0-9]+$ ]] || prev_timestamp=0
            if [ "$mod_last_modified" -gt "$prev_timestamp" ]; then
                if [ -n "$discord_webhook_url" ]; then
                    # fn_tr returns a controlled (hardcoded) format string; user data passed as args
                    # shellcheck disable=SC2059
                    _wh_fmt="$(fn_tr "mod_updated_notification")"
                    webhook_message="$(printf "$_wh_fmt" "$mod_name" "$mod_id")"
                    curl --max-time 10 -sS -H "Content-Type: application/json" -X POST \
                        -d "$(jq -n --arg msg "$webhook_message" '{"content":$msg}')" \
                        "$discord_webhook_url" >/dev/null 2>&1 || true
                fi
                jq --arg mod "$mod_id" --argjson time "$mod_last_modified" \
                    '.[$mod] = $time' "$timestamp_file" \
                    > "${timestamp_file}.tmp" && mv "${timestamp_file}.tmp" "$timestamp_file"
            fi
        fi
    done

    fn_workshop_refresh_conan_modlist
}

# ── Top-level command dispatcher ──────────────────────────────────────────────

fn_workshop_command() {
    local subcmd="${1:-}"
    case "$subcmd" in
        autoupdate|auto-update)
            shift || true
            fn_workshop_autoupdate_dispatch "$@"
            ;;
        "")
            fn_workshop_mods
            ;;
        *)
            local maybe_mod_id=""
            local all_numeric="1"

            for maybe_mod_id in "$@"; do
                if ! [[ "$maybe_mod_id" =~ ^[0-9]+$ ]]; then
                    all_numeric="0"
                    break
                fi
            done

            if [ "$all_numeric" = "1" ]; then
                fn_workshop_mods_selective "$@"
            else
                fn_workshop_mods
            fi
            ;;
    esac
}

fn_workshop_autoupdate_dispatch() {
    local subcommand="${1:-}"
    shift || true
    case "$subcommand" in
        set|setzen)
            local mode_token="${1:-}"
            shift || true
            case "$mode_token" in
                interval|intervall)
                    fn_workshop_autoupdate_set_interval "${1:-}"
                    ;;
                minutes|minuten)
                    fn_workshop_autoupdate_set_minutes "${1:-}"
                    ;;
                *)
                    fn_log "$red" "error" "workshop_autoupdate_invalid_subcommand" "$mode_token"
                    return 1
                    ;;
            esac
            ;;
        clear|off|deaktivieren)
            fn_workshop_autoupdate_clear
            ;;
        list|liste|anzeigen)
            fn_workshop_autoupdate_list
            ;;
        *)
            fn_log "$yellow" "warning" "workshop_autoupdate_no_subcommand"
            printf 'Usage: workshop autoupdate set interval <1|2|3|4|6|8|12|24> | clear | list\n'
            return 1
            ;;
    esac
}
