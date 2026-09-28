#!/usr/bin/env bash
# Einen Wegwerf-PostgreSQL fuer die Backend-Tests starten.
#
# Die Suite laeuft auf PostgreSQL, derselben Datenbank wie der Betrieb. Sie
# braucht einen Server, auf dem sie eigene Datenbanken anlegen und wieder
# loeschen darf (`CREATEDB`); jede davon heisst `msm_test_…`, andere fasst sie
# nicht an. Dieser Container ist genau das und nichts sonst: Daten im
# Arbeitsspeicher (`--tmpfs`), ohne fsync, ohne Passwort, nur an 127.0.0.1.
# Er darf nie die Panel-Datenbank sein.
#
# Aufruf:
#   scripts/test-postgres.sh          # startet (oder weckt) den Container
#   export MSM_TEST_DATABASE_URL=...  # die Zeile, die das Skript ausgibt
#   cd backend && python -m pytest -q
#
# Ohne Docker geht jeder andere PostgreSQL ab Version 16 ebenso, z. B. ein
# Cluster in WSL: `initdb -D /tmp/msm-test --auth=trust`, dann
# `pg_ctl -D /tmp/msm-test -o "-p 15499 -k /tmp" start`.
set -euo pipefail

NAME="msm-test-postgres"
PORT="${MSM_TEST_POSTGRES_PORT:-15499}"
URL="postgresql://postgres@127.0.0.1:${PORT}/postgres"

if docker container inspect "$NAME" >/dev/null 2>&1; then
    docker start "$NAME" >/dev/null
else
    # Git-Bash schreibt sonst `/var/lib/...` in einen Windows-Pfad um.
    MSYS_NO_PATHCONV=1 docker run -d --name "$NAME" \
        -p "127.0.0.1:${PORT}:5432" \
        -e POSTGRES_HOST_AUTH_METHOD=trust \
        --tmpfs /var/lib/postgresql/data \
        postgres:16-alpine \
        -c fsync=off -c synchronous_commit=off -c full_page_writes=off \
        -c max_connections=300 >/dev/null
fi

for _ in $(seq 1 30); do
    if docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1; then
        echo "export MSM_TEST_DATABASE_URL=\"$URL\""
        exit 0
    fi
    sleep 1
done
echo "PostgreSQL im Container $NAME antwortet nicht." >&2
exit 1
