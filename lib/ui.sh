#!/bin/bash

fn_get_config_display_path() {
    printf '%s\n' "$CONFIG_FILE"
}

fn_get_script_display_name() {
    printf '%s\n' "$(basename "$SCRIPT_PATH")"
}

fn_get_terminal_width() {
    local width="${COLUMNS:-}"

    if [[ ! "$width" =~ ^[0-9]+$ ]] || [ "$width" -lt 40 ]; then
        if command -v tput >/dev/null 2>&1; then
            width="$(tput cols 2>/dev/null)"
        fi
    fi

    if [[ ! "$width" =~ ^[0-9]+$ ]] || [ "$width" -lt 40 ]; then
        width="100"
    fi

    printf '%s\n' "$width"
}

fn_repeat_char() {
    local character="$1"
    local count="$2"

    if [ "$count" -le 0 ]; then
        return 0
    fi

    printf '%*s' "$count" '' | tr ' ' "$character"
}

fn_wrap_line_fallback() {
    local width="$1"
    local remaining="$2"
    local segment=""
    local break_index="0"
    local i="0"

    while [ "${#remaining}" -gt "$width" ]; do
        break_index="0"
        for ((i=width; i>0; i--)); do
            if [ "${remaining:i-1:1}" = " " ]; then
                break_index="$i"
                break
            fi
        done

        if [ "$break_index" -gt 0 ]; then
            segment="${remaining:0:break_index}"
            while [ -n "$segment" ] && [ "${segment: -1}" = " " ]; do
                segment="${segment% }"
            done

            if [ -n "$segment" ]; then
                printf '%s\n' "$segment"
                remaining="${remaining:$break_index}"
                while [ -n "$remaining" ] && [ "${remaining:0:1}" = " " ]; do
                    remaining="${remaining:1}"
                done
                continue
            fi
        fi

        printf '%s\n' "${remaining:0:$width}"
        remaining="${remaining:$width}"
    done

    if [ -n "$remaining" ]; then
        printf '%s\n' "$remaining"
    fi
}

fn_wrap_text_block() {
    local width="$1"
    local text="$2"
    local line=""

    if [ -z "$text" ]; then
        return 0
    fi

    if [ "$width" -lt 20 ]; then
        width="20"
    fi

    while IFS= read -r line || [ -n "$line" ]; do
        if [ -z "$line" ]; then
            printf '\n'
        else
            if command -v fold >/dev/null 2>&1; then
                printf '%s\n' "$line" | fold -s -w "$width"
            else
                fn_wrap_line_fallback "$width" "$line"
            fi
        fi
    done <<< "$text"
}

fn_get_text_block_max_length() {
    local text="$1"
    local max_length="0"
    local line=""

    while IFS= read -r line || [ -n "$line" ]; do
        if [ "${#line}" -gt "$max_length" ]; then
            max_length="${#line}"
        fi
    done <<< "$text"

    printf '%s\n' "$max_length"
}

fn_get_help_primary_alias() {
    local alias_string="$1"
    local -a alias_list=()

    IFS=';' read -r -a alias_list <<< "$alias_string"

    if [ "$CURRENT_LANGUAGE" = "de" ] && [ "${#alias_list[@]}" -ge 3 ] && [ -n "${alias_list[2]}" ]; then
        printf '%s\n' "${alias_list[2]}"
    elif [ "${#alias_list[@]}" -ge 2 ] && [ -n "${alias_list[1]}" ]; then
        printf '%s\n' "${alias_list[1]}"
    elif [ "${#alias_list[@]}" -ge 1 ]; then
        printf '%s\n' "${alias_list[0]}"
    else
        printf '\n'
    fi
}

fn_get_help_command_display_name() {
    local index="$1"
    local command_name=""

    command_name="$(fn_get_help_primary_alias "${COMMAND_ALIASES[$index]}")"
    if [ -n "$command_name" ]; then
        printf '%s\n' "$command_name"
    else
        printf '%s\n' "${COMMAND_DISPLAY[$index]}"
    fi
}

fn_get_help_command_syntax() {
    local index="$1"
    local display="${COMMAND_DISPLAY[$index]}"
    local primary_alias=""

    primary_alias="$(fn_get_help_primary_alias "${COMMAND_ALIASES[$index]}")"

    case "$display" in
        language)
            printf '%s [en|de]\n' "$primary_alias"
            ;;
        backup)
            if [ "$CURRENT_LANGUAGE" = "de" ]; then
                printf '%s | %s liste | %s wiederherstellen <zeitstempel>\n' "$primary_alias" "$primary_alias" "$primary_alias"
            else
                printf '%s | %s list | %s restore <timestamp>\n' "$primary_alias" "$primary_alias" "$primary_alias"
            fi
            ;;
        panel)
            printf '%s install | %s update | %s repair | %s status\n' "$primary_alias" "$primary_alias" "$primary_alias" "$primary_alias"
            ;;
        autorestart)
            if [ "$CURRENT_LANGUAGE" = "de" ]; then
                printf '%s setzen zeiten HH:MM... | %s setzen intervall <stunden> | %s liste | %s deaktivieren\n' "$primary_alias" "$primary_alias" "$primary_alias" "$primary_alias"
            else
                printf '%s set times HH:MM... | %s set interval <hours> | %s list | %s clear\n' "$primary_alias" "$primary_alias" "$primary_alias" "$primary_alias"
            fi
            ;;
        server)
            if [ "$CURRENT_LANGUAGE" = "de" ]; then
                printf '%s liste | %s erstellen <name> | %s klonen <quelle> <ziel> | %s loeschen [--force] <name>\n' "$primary_alias" "$primary_alias" "$primary_alias" "$primary_alias"
            else
                printf '%s list | %s create <name> | %s clone <source> <target> | %s delete [--force] <name>\n' "$primary_alias" "$primary_alias" "$primary_alias" "$primary_alias"
            fi
            ;;
        migrate)
            printf '%s [name]\n' "$primary_alias"
            ;;
        *)
            printf '%s\n' "$primary_alias"
            ;;
    esac
}

fn_format_help_detail_line() {
    local label="$1"
    local value="$2"

    printf '  %-9s %s' "$label" "$value"
}

fn_print_box_line() {
    local content_width="$1"
    local border="+-$(fn_repeat_char "-" "$content_width")-+"

    if [ -n "$lightblue" ]; then
        printf '%b%s%b\n' "$lightblue" "$border" "$default"
    else
        printf '%s\n' "$border"
    fi
}

fn_print_box() {
    local title="$1"
    local max_content_width="$2"
    local body="$3"
    local wrapped_body=""
    local content_width="0"
    local padded_line=""
    local line=""

    if [ "$max_content_width" -lt 30 ]; then
        max_content_width="30"
    fi

    wrapped_body="$(fn_wrap_text_block "$max_content_width" "$body")"
    content_width="$(fn_get_text_block_max_length "$wrapped_body")"
    if [ "${#title}" -gt "$content_width" ]; then
        content_width="${#title}"
    fi
    if [ "$content_width" -gt "$max_content_width" ]; then
        content_width="$max_content_width"
    fi

    fn_print_box_line "$content_width"
    if [ "${#title}" -gt "$content_width" ]; then
        title="${title:0:$content_width}"
    fi
    printf -v padded_line "%-${content_width}s" "$title"
    if [ -n "$cyan" ]; then
        printf '| %b%b%s%b |\n' "$bld" "$cyan" "$padded_line" "$default"
    else
        printf '| %s |\n' "$padded_line"
    fi
    fn_print_box_line "$content_width"

    while IFS= read -r line || [ -n "$line" ]; do
        printf -v padded_line "%-${content_width}s" "$line"
        printf '| %s |\n' "$padded_line"
    done <<< "$wrapped_body"

    fn_print_box_line "$content_width"
}

fn_format_aliases() {
    local alias_string="$1"
    local formatted=""
    local alias=""
    local -a alias_list=()

    IFS=';' read -r -a alias_list <<< "$alias_string"
    for alias in "${alias_list[@]}"; do
        if [ -n "$formatted" ]; then
            formatted+=", "
        fi
        formatted+="$alias"
    done

    printf '%s\n' "$formatted"
}

fn_build_help_modes_body() {
    local body=""

    body="$(fn_tr "help_restart_mode_times")"
    body+=$'\n'"$(fn_tr "help_restart_mode_interval")"
    body+=$'\n'"$(fn_tr "help_restart_mode_exclusive")"
    printf '%s' "$body"
}

fn_build_help_group_body() {
    local layout="$1"
    shift || true
    local body=""
    local command=""
    local index=""
    local aliases=""
    local syntax=""
    local description=""
    local first_line=""
    local command_name=""
    local first_entry="1"

    for command in "$@"; do
        index="${COMMAND_INDEX_BY_DISPLAY[$command]}"
        aliases="$(fn_format_aliases "${COMMAND_ALIASES[$index]}")"
        syntax="$(fn_get_help_command_syntax "$index")"
        description="$(fn_tr "${COMMAND_DESC_KEY[$index]}")"
        command_name="$(fn_get_help_command_display_name "$index")"

        if [ "$first_entry" != "1" ]; then
            body+="  $(fn_repeat_char "-" 18)"$'\n'
        fi
        first_entry="0"

        if [ "$layout" = "wide" ]; then
            printf -v first_line '[%-12s] %s' "$command_name" "$description"
            body+="${first_line}"$'\n'
        else
            body+="[${command_name}]"$'\n'
            body+="  ${description}"$'\n'
        fi

        body+="$(fn_format_help_detail_line "$(fn_tr "help_alias_label")" "$aliases")"$'\n'
        body+="$(fn_format_help_detail_line "$(fn_tr "help_syntax_label")" "$syntax")"$'\n'
    done

    printf '%s' "${body%$'\n'}"
}

fn_render_autorestart_status_box() {
    local terminal_width=""
    local content_width=""
    local body=""
    local times_display=""

    terminal_width="$(fn_get_terminal_width)"
    content_width=$((terminal_width - 6))
    if [ "$content_width" -gt 110 ]; then
        content_width="110"
    fi

    times_display="$(fn_get_autorestart_times_display)"
    body="$(printf "$(fn_tr "autorestart_mode_label")" "$(fn_get_autorestart_mode_name)")"
    body+=$'\n'"$(printf "$(fn_tr "autorestart_summary_label")" "$(fn_get_autorestart_summary)")"

    if [ "$autorestart_mode" = "interval" ] && [ -n "$autorestart_interval_hours" ]; then
        body+=$'\n'"$(printf "$(fn_tr "autorestart_interval_label")" "$autorestart_interval_hours")"
    fi

    if [ "$times_display" != "-" ]; then
        body+=$'\n'"$(printf "$(fn_tr "autorestart_times_label")" "$times_display")"
    fi

    body+=$'\n'"$(printf "$(fn_tr "autorestart_config_label")" "$(fn_get_config_display_path)")"
    fn_print_box "$(fn_tr "autorestart_list_title")" "$content_width" "$body"
}


fn_help_command() {
    local terminal_width=""
    local content_width=""
    local layout="narrow"
    local header_body=""
    local modes_body=""
    local examples_body=""
    local script_name=""

    terminal_width="$(fn_get_terminal_width)"
    content_width=$((terminal_width - 6))
    if [ "$content_width" -gt 110 ]; then
        content_width="110"
    fi

    if [ "$terminal_width" -ge 110 ]; then
        layout="wide"
    fi

    script_name="$(fn_get_script_display_name)"
    header_body="$(fn_tr "help_tagline")"
    header_body+=$'\n\n'"$(printf "$(fn_tr "help_usage")" "$script_name")"
    header_body+=$'\n'"$(printf "$(fn_tr "help_language_line")" "$(fn_current_language_name "$CURRENT_LANGUAGE")" "$CURRENT_LANGUAGE")"
    header_body+=$'\n'"$(printf "$(fn_tr "help_restart_line")" "$(fn_get_help_restart_display)")"
    header_body+=$'\n'"$(printf "$(fn_tr "help_config_line")" "$(fn_get_config_display_path)")"
    header_body+=$'\n'"$(printf "$(fn_tr "help_maintainer")" "$PROJECT_MAINTAINER_NAME" "$PROJECT_MAINTAINER_BRAND")"
    header_body+=$'\n'"$(printf "$(fn_tr "help_origin")" "$PROJECT_ORIGIN_AUTHORS")"
    header_body+=$'\n'"$(fn_tr "help_modes_note")"
    modes_body="$(fn_build_help_modes_body)"

    printf '\n'
    fn_print_box "$(fn_tr "help_title")" "$content_width" "$header_body"
    printf '\n'
    fn_print_box "$(fn_tr "help_group_server")" "$content_width" "$(fn_build_help_group_body "$layout" start stop restart monitor console server)"
    printf '\n'
    fn_print_box "$(fn_tr "help_group_maintenance")" "$content_width" "$(fn_build_help_group_body "$layout" install update validate workshop backup wipe panel migrate)"
    printf '\n'
    fn_print_box "$(fn_tr "help_group_config")" "$content_width" "$(fn_build_help_group_body "$layout" language autorestart help)"
    printf '\n'
    fn_print_box "$(fn_tr "help_restart_modes_title")" "$content_width" "$modes_body"

    examples_body="$(printf "$(fn_tr "help_example_language")" "$script_name")"
    examples_body+=$'\n'"$(printf "$(fn_tr "help_example_times")" "$script_name")"
    examples_body+=$'\n'"$(printf "$(fn_tr "help_example_interval")" "$script_name")"
    examples_body+=$'\n'"$(fn_tr "help_example_hint")"

    printf '\n'
    fn_print_box "$(fn_tr "help_examples_title")" "$content_width" "$examples_body"
}
