#!/bin/bash

fn_check_steam_credentials() {
    if [ -z "${steamlogin:-}" ] || [ "${steamlogin}" = "CHANGEME" ]; then
        fn_log "$red" "error" "steamlogin_required" "$CONFIG_FILE"
        return 1
    fi
    if [ "${steamlogin}" != "anonymous" ]; then
        if [ -z "${steampassword:-}" ] || [ "${steampassword}" = "CHANGEME" ]; then
            fn_log "$red" "error" "steampassword_required" "$CONFIG_FILE"
            return 1
        fi
    fi
    return 0
}

fn_install_dayz() {
    if [ -z "${STEAMCMD_DIR:-}" ] || [ -z "${SERVERFILES:-}" ] || [ -z "${SERVERPROFILE:-}" ] || [ -z "${LOCKUPDATE_FILE:-}" ] || [ -z "${appid:-}" ]; then
        fn_log "$red" "error" "server_dir_not_set"
        return 1
    fi
    if ! fn_check_steam_credentials; then
        return 1
    fi
    if [ ! -f "${STEAMCMD_DIR}/steamcmd.sh" ]; then
        mkdir -p "${STEAMCMD_DIR}" >/dev/null 2>&1
        if ! curl -sqL "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz" | tar zxf - -C "${STEAMCMD_DIR}"; then
            fn_log "$red" "error" "steamcmd_download_failed"
            return 1
        fi
        fn_log "$yellow" "steam" "steamcmd_installed"
    else
        fn_log "$lightblue" "steam" "steamcmd_present"
    fi

    if [ ! -x "${SERVERFILES}/${server_binary:-ConanSandbox/Binaries/Linux/ConanSandboxServer}" ]; then
        mkdir -p "${SERVERFILES}" >/dev/null 2>&1
        mkdir -p "${SERVERPROFILE}" >/dev/null 2>&1
        fn_log "$yellow" "dayz" "downloading_serverfiles"
        if ! fn_runvalidate_dayz; then
            fn_log "$red" "error" "serverfiles_download_failed"
            return 1
        fi
    else
        fn_log "$lightblue" "dayz" "server_already_installed"
    fi
}

fn_runupdate_dayz() {
    if [ "${steamlogin}" = "anonymous" ]; then
        "${STEAMCMD_DIR}/steamcmd.sh" +@sSteamCmdForcePlatformType linux +force_install_dir "${SERVERFILES}" +login anonymous +app_update "${appid}" +quit
    else
        "${STEAMCMD_DIR}/steamcmd.sh" +@sSteamCmdForcePlatformType linux +force_install_dir "${SERVERFILES}" +login "${steamlogin}" "${steampassword}" +app_update "${appid}" +quit
    fi
    return $?
}

fn_update_dayz() {
    if [ -z "${STEAMCMD_DIR:-}" ] || [ -z "${SERVERFILES:-}" ] || [ -z "${SERVERPROFILE:-}" ] || [ -z "${LOCKUPDATE_FILE:-}" ] || [ -z "${appid:-}" ]; then
        fn_log "$red" "error" "server_dir_not_set"
        return 1
    fi
    if ! fn_check_steam_credentials; then
        return 1
    fi
    local appmanifestfile="${SERVERFILES}/steamapps/appmanifest_${appid}.acf"
    local currentbuild=""
    local availablebuild=""
    local seconds=""

    fn_log_inline "$lightblue" "progress" "checking_update"

    currentbuild="$(grep buildid "${appmanifestfile}" 2>/dev/null | tr '[:blank:]"' ' ' | tr -s ' ' | cut -d ' ' -f 3 | head -n 1)"

    if [ -f "${HOME}/Steam/appcache/appinfo.vdf" ]; then
        rm -f "${HOME}/Steam/appcache/appinfo.vdf"  # Steam-level cache, always in HOME
        sleep 1
    fi

    if [ "${steamlogin}" = "anonymous" ]; then
        availablebuild="$("${STEAMCMD_DIR}/steamcmd.sh" +@sSteamCmdForcePlatformType linux +login anonymous +app_info_update 1 +app_info_print "${appid}" +app_info_print "${appid}" +quit | sed -n '/branch/,$p' | grep -m 1 buildid | tr -cd '[:digit:]')"
    else
        availablebuild="$("${STEAMCMD_DIR}/steamcmd.sh" +@sSteamCmdForcePlatformType linux +login "${steamlogin}" "${steampassword}" +app_info_update 1 +app_info_print "${appid}" +app_info_print "${appid}" +quit | sed -n '/branch/,$p' | grep -m 1 buildid | tr -cd '[:digit:]')"
    fi

    if [ -z "${availablebuild}" ]; then
        printf "\r[ ${red}%s${default} ] %s\n" "$(fn_label "fail")" "$(fn_tr "checking_update")"
        fn_log "$red" "fail" "steamcmd_no_version"
        exit 1
    fi

    printf "\r[ ${green}%s${default} ] %s\n" "$(fn_label "ok")" "$(fn_tr "checking_update")"

    if [ "${currentbuild}" != "${availablebuild}" ]; then
        fn_log "$green" "ok" "update_available"
        printf '\t'
        printf "$(fn_tr "current_build")" "${currentbuild}"
        printf '\n\t'
        printf "$(fn_tr "available_build")" "${availablebuild}"
        printf '\n\t%s\n' "https://steamdb.info/app/${appid}/"
        trap 'fn_remove_file_if_exists "${LOCKUPDATE_FILE}"' EXIT
        date > "${LOCKUPDATE_FILE}"
        printf '\n%s' "$(fn_tr "applying_update")"
        for seconds in {1..3}; do
            printf '.'
            sleep 1
        done
        printf '\n'

        fn_status_dayz
        if [ "${dayzstatus}" = "0" ]; then
            fn_runupdate_dayz
            fn_workshop_mods
        else
            fn_stop_dayz
            fn_runupdate_dayz
            fn_workshop_mods
            fn_launch_dayz_process
        fi
        trap - EXIT
        fn_remove_file_if_exists "${LOCKUPDATE_FILE}"
        return 0
    fi

    fn_log "$green" "ok" "no_update_available"
    printf '\t'
    printf "$(fn_tr "current_version")" "${currentbuild}"
    printf '\n\t'
    printf "$(fn_tr "available_version")" "${availablebuild}"
    printf '\n\t%s\n\n' "https://steamdb.info/app/${appid}/"
}

fn_runvalidate_dayz() {
    if [ "${steamlogin}" = "anonymous" ]; then
        "${STEAMCMD_DIR}/steamcmd.sh" +@sSteamCmdForcePlatformType linux +force_install_dir "${SERVERFILES}" +login anonymous +app_update "${appid}" validate +quit
    else
        "${STEAMCMD_DIR}/steamcmd.sh" +@sSteamCmdForcePlatformType linux +force_install_dir "${SERVERFILES}" +login "${steamlogin}" "${steampassword}" +app_update "${appid}" validate +quit
    fi
    return $?
}

fn_validate_dayz() {
    if [ -z "${STEAMCMD_DIR:-}" ] || [ -z "${SERVERFILES:-}" ] || [ -z "${SERVERPROFILE:-}" ] || [ -z "${LOCKUPDATE_FILE:-}" ] || [ -z "${appid:-}" ]; then
        fn_log "$red" "error" "server_dir_not_set"
        return 1
    fi
    if ! fn_check_steam_credentials; then
        return 1
    fi
    fn_status_dayz

    if [ "${dayzstatus}" = "0" ]; then
        fn_runvalidate_dayz
        return 0
    fi

    trap 'fn_remove_file_if_exists "${LOCKUPDATE_FILE}"' EXIT
    date > "${LOCKUPDATE_FILE}"
    fn_stop_dayz
    if ! fn_runvalidate_dayz; then
        fn_log "$red" "error" "serverfiles_download_failed"
        trap - EXIT
        fn_remove_file_if_exists "${LOCKUPDATE_FILE}"
        return 1
    fi
    fn_workshop_mods
    trap - EXIT
    fn_remove_file_if_exists "${LOCKUPDATE_FILE}"
    fn_launch_dayz_process
}

