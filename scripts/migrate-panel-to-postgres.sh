#!/usr/bin/env bash
set -euo pipefail
umask 077

# One-time Phase-8 bridge for existing panel installations. This script is
# intentionally non-interactive and handles only SQLite -> local PostgreSQL.

MSM_DIR="${MSM_DIR:-/opt/msm}"
MSM_USER="${MSM_USER:-msm}"
ENV_FILE="$MSM_DIR/backend/.env"
SQL_FILE=""
ENV_BACKUP=""
ENV_CHANGED=false
MIGRATION_COMMITTED=false
PG_ROLE_CREATED=false
PG_DATABASE_CREATED=false

fail() {
  echo "FEHLER: $1" >&2
  exit 1
}

cleanup() {
  local exit_code=$?
  [[ -z "$SQL_FILE" ]] || rm -f "$SQL_FILE"
  unset PHASE8_PG_PASSWORD
  if [[ $exit_code -ne 0 && "$ENV_CHANGED" == "true" && "$MIGRATION_COMMITTED" != "true" ]]; then
    cp -p "$ENV_BACKUP" "$ENV_FILE" 2>/dev/null || true
    echo "Die ursprüngliche Datenbank-Konfiguration wurde wiederhergestellt." >&2
  fi
  if [[ $exit_code -ne 0 && "$MIGRATION_COMMITTED" != "true" ]]; then
    if [[ "$PG_DATABASE_CREATED" == "true" ]]; then
      su - postgres -c "dropdb --if-exists msm" >/dev/null 2>&1 || true
      rm -f "$MSM_DIR/backend/msm.db.migration-complete"
    fi
    if [[ "$PG_ROLE_CREATED" == "true" ]]; then
      su - postgres -c "dropuser --if-exists msm" >/dev/null 2>&1 || true
    fi
  fi
}
trap cleanup EXIT

[[ $EUID -eq 0 ]] || fail "Bitte als root ausführen."
[[ -f "$ENV_FILE" ]] || fail "Panel-Konfiguration fehlt."
[[ -f "$MSM_DIR/backend/scripts/migrate_sqlite_to_postgres.py" ]] \
  || fail "SQLite-Importwerkzeug fehlt."

CURRENT_URL=$(grep -E '^MSM_DATABASE_URL=' "$ENV_FILE" | head -1 | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
if [[ "$CURRENT_URL" == postgresql* ]]; then
  echo "Panel-Datenbank verwendet bereits PostgreSQL."
  MIGRATION_COMMITTED=true
  exit 0
fi
[[ "$CURRENT_URL" == sqlite* ]] || fail "Nicht unterstützte bestehende Datenbank-Konfiguration."

LEGACY_SQLITE="$MSM_DIR/backend/msm.db"
[[ -s "$LEGACY_SQLITE" ]] || fail "Legacy-SQLite-Datenbank fehlt oder ist leer."

if ! command -v psql >/dev/null 2>&1; then
  apt-get update -qq --allow-releaseinfo-change=true
  apt-get install -y -qq postgresql postgresql-contrib libpq-dev python3-dev
fi
if [[ -d /run/systemd/system ]]; then
  systemctl enable --now postgresql >/dev/null
else
  service postgresql start >/dev/null 2>&1 || fail "PostgreSQL konnte nicht gestartet werden."
fi

PHASE8_PG_PASSWORD=$(python3 -c "import secrets,string; a=string.ascii_letters+string.digits+'_-'; print(''.join(secrets.choice(a) for _ in range(32)))")
export PHASE8_PG_PASSWORD

if su - postgres -c "psql --no-psqlrc -tAc \"SELECT 1 FROM pg_roles WHERE rolname='msm'\"" | grep -q 1; then
  fail "PostgreSQL-Rolle 'msm' existiert bereits. Migration greift fremde/bestehende Daten nicht an."
fi
if su - postgres -c "psql --no-psqlrc -tAc \"SELECT 1 FROM pg_database WHERE datname='msm'\"" | grep -q 1; then
  fail "PostgreSQL-Datenbank 'msm' existiert bereits. Migration greift fremde/bestehende Daten nicht an."
fi

SQL_FILE=$(mktemp /tmp/msm-phase8-postgres.XXXXXX.sql)
chmod 600 "$SQL_FILE"
cat > "$SQL_FILE" <<EOF
CREATE USER msm WITH PASSWORD '${PHASE8_PG_PASSWORD}';
EOF
chown postgres:postgres "$SQL_FILE"
su - postgres -c "psql --no-psqlrc --set ON_ERROR_STOP=1 -f '$SQL_FILE'" >/dev/null
PG_ROLE_CREATED=true
su - postgres -c "createdb --owner=msm msm"
PG_DATABASE_CREATED=true
su - postgres -c "psql --no-psqlrc --set ON_ERROR_STOP=1 -d msm -c 'GRANT ALL ON SCHEMA public TO msm'" >/dev/null
rm -f "$SQL_FILE"
SQL_FILE=""

ENV_BACKUP="$ENV_FILE.pre-phase8-$(date +%Y%m%d-%H%M%S)"
cp -p "$ENV_FILE" "$ENV_BACKUP"
chmod 600 "$ENV_BACKUP"

python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import os
import sys

path = Path(sys.argv[1])
password = os.environ["PHASE8_PG_PASSWORD"]
sync_url = f"postgresql+psycopg2://msm:{password}@localhost:5432/msm"
async_url = f"postgresql+asyncpg://msm:{password}@localhost:5432/msm"
lines = path.read_text(encoding="utf-8").splitlines()
values = {
    "MSM_DATABASE_URL": sync_url,
    "MSM_DATABASE_URL_ASYNC": async_url,
}
seen: set[str] = set()
output: list[str] = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in values:
        output.append(f'{key}="{values[key]}"')
        seen.add(key)
    else:
        output.append(line)
for key, value in values.items():
    if key not in seen:
        output.append(f'{key}="{value}"')
path.write_text("\n".join(output) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
ENV_CHANGED=true
chown "$MSM_USER:$MSM_USER" "$ENV_FILE" "$ENV_BACKUP"

su - "$MSM_USER" -c "
  set -e
  cd '$MSM_DIR/backend'
  source venv/bin/activate
  python3 scripts/migrate_sqlite_to_postgres.py --sqlite '$LEGACY_SQLITE'
  python3 scripts/manage_schema.py
  python3 scripts/migrate_sqlite_to_postgres.py --sqlite '$LEGACY_SQLITE' --archive-source
"

MIGRATION_COMMITTED=true
echo "Legacy-SQLite wurde vollständig nach PostgreSQL migriert."
echo "Sicherheitskopie der alten Konfiguration: $ENV_BACKUP"
