#!/usr/bin/env bash
set -euo pipefail

readonly app_id="2430930"
readonly game_dir="/home/container/ShooterGame"
readonly win64_dir="${game_dir}/Binaries/Win64"
readonly sentry_dir="${game_dir}/Plugins/sentry"

# ASA 92.x bleibt unter Wine 10 im Sentry-Crashpad-Plugin stehen. Steam kann
# den Ordner bei Updates erneut anlegen; deshalb wird er vor jedem Start unter
# einem eindeutigen Namen beiseitegelegt, ohne Dateien zu löschen.
if [[ -d "${sentry_dir}" ]]; then
  mv "${sentry_dir}" "${sentry_dir}.disabled.$(date +%s)"
  printf '%s\n' '[MSM ASA] Inkompatibles Sentry-Crashpad-Plugin für diesen Start deaktiviert.'
fi

# SteamGameServer benötigt die nativen SDK-Bibliotheken zusätzlich zur
# Windows-DLL neben der EXE. Die Ziele liegen unveränderlich im Runtime-Image.
mkdir -p "${HOME}/.steam/sdk32" "${HOME}/.steam/sdk64"
ln -sfn /opt/steamcmd/linux32/steamclient.so "${HOME}/.steam/sdk32/steamclient.so"
ln -sfn /opt/steamcmd/linux64/steamclient.so "${HOME}/.steam/sdk64/steamclient.so"
printf '%s\n' "${app_id}" > "${win64_dir}/steam_appid.txt"

exec "$@"
