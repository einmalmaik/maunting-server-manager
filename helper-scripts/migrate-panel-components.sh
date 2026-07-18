#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Interactive, failure-safe migration assistant for an existing MSM all-in-one
# installation. DNS/provider configuration is intentionally never guessed.

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MSM_DIR="${MSM_DIR:-/opt/msm}"
SOURCE_BACKEND="$MSM_DIR/backend"
SOURCE_ENV="$SOURCE_BACKEND/.env"
SOURCE_PYTHON="$SOURCE_BACKEND/venv/bin/python"

MIGRATE_FRONTEND=false
MIGRATE_BACKEND=false
MIGRATE_SERVERS=false
FRONTEND_ORIGIN=""
API_DOMAIN=""
BACKEND_TARGET=""
SSH_PORT=22
SERVER_IDS=""
TARGET_NODE_ID=""
DRY_RUN=false
ASSUME_YES=false
EXPLICIT_ACTION=false

WORK_DIR=""
SOURCE_STOPPED=false
SOURCE_UPDATE_TIMER_ACTIVE=false
TARGET_COMMITTED=false
SSH_CONTROL=""
REMOTE_SUDO="sudo"

info() { printf '[MSM] %s\n' "$*"; }
ok() { printf '[OK]  %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[ERR] %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
MSM Komponenten-Migrationsassistent

Interaktiv:
  sudo ./helper-scripts/migrate-panel-components.sh

Geprüfte Automation:
  --migrate-frontend --frontend-origin https://panel.example.com --api-domain api.example.com
  --migrate-servers --server-ids 1,2,3 --target-node-id 4
  --migrate-backend --backend-target root@203.0.113.10 --api-domain api.example.com

Optionen:
  --ssh-port PORT       SSH-Port des Backend-Zielservers (Default 22)
  --dry-run             Auswahl und Vorbedingungen zeigen, nichts verändern
  --yes                 Textbestätigungen überspringen; Node-Owner-Freigabe bleibt Pflicht
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --migrate-frontend) MIGRATE_FRONTEND=true; EXPLICIT_ACTION=true; shift ;;
        --migrate-backend) MIGRATE_BACKEND=true; EXPLICIT_ACTION=true; shift ;;
        --migrate-servers) MIGRATE_SERVERS=true; EXPLICIT_ACTION=true; shift ;;
        --frontend-origin) FRONTEND_ORIGIN="${2:-}"; shift 2 ;;
        --api-domain) API_DOMAIN="${2:-}"; shift 2 ;;
        --backend-target) BACKEND_TARGET="${2:-}"; shift 2 ;;
        --ssh-port) SSH_PORT="${2:-}"; shift 2 ;;
        --server-ids) SERVER_IDS="${2:-}"; shift 2 ;;
        --target-node-id) TARGET_NODE_ID="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --yes) ASSUME_YES=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unbekannte Option: $1" ;;
    esac
done

ask_yes_no() {
    local prompt="$1" default="${2:-N}" answer
    local suffix="[j/N]"
    [[ "$default" == "J" ]] && suffix="[J/n]"
    read -r -p "$prompt $suffix " answer
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[JjYy]$ ]]
}

ask_value() {
    local prompt="$1" variable="$2" value
    read -r -p "$prompt: " value
    printf -v "$variable" '%s' "$value"
}

env_value() {
    local key="$1" file="${2:-$SOURCE_ENV}"
    sed -n "s/^${key}=//p" "$file" 2>/dev/null | tail -1 | sed 's/^"//;s/"$//'
}

validate_origin() {
    [[ "$1" =~ ^https://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$ ]] \
        || fail "Ungültige HTTPS-Origin: $1"
}

validate_domain() {
    [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] \
        || fail "Ungültige API-Domain: $1"
}

confirm_exact() {
    local expected="$1" prompt="$2" answer
    $ASSUME_YES && return 0
    read -r -p "$prompt ('$expected'): " answer
    [[ "$answer" == "$expected" ]] || fail "Abgebrochen; es wurde nichts weiter verändert."
}

cleanup() {
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        rm -rf -- "$WORK_DIR"
    fi
}

restart_source_on_error() {
    local code=$?
    if $SOURCE_STOPPED && ! $TARGET_COMMITTED; then
        warn "Ziel-Cutover fehlgeschlagen; die Quell-Control-Plane wird wieder gestartet."
        systemctl start msm-dis-sidecar.service >/dev/null 2>&1 || true
        systemctl start msm-panel.service >/dev/null 2>&1 || true
        if $SOURCE_UPDATE_TIMER_ACTIVE; then
            systemctl start msm-update.timer >/dev/null 2>&1 || true
        fi
    fi
    cleanup
    exit "$code"
}
trap restart_source_on_error ERR
trap cleanup EXIT

if [[ "$EXPLICIT_ACTION" == "false" ]]; then
    echo ""
    echo "MSM Komponenten-Migration"
    echo "Die Schritte werden gesammelt und in sicherer Reihenfolge ausgeführt."
    echo ""
    ask_yes_no "Ist ein extern gebautes Frontend bereits erreichbar und soll verbunden werden?" "N" \
        && MIGRATE_FRONTEND=true
    ask_yes_no "Sollen ausgewählte Gameserver auf einen anderen bestehenden Node kopiert werden?" "N" \
        && MIGRATE_SERVERS=true
    ask_yes_no "Soll die Backend-Control-Plane auf einen frischen Linux-Server umziehen?" "N" \
        && MIGRATE_BACKEND=true
fi

if ! $MIGRATE_FRONTEND && ! $MIGRATE_BACKEND && ! $MIGRATE_SERVERS; then
    info "Keine Migration ausgewählt."
    exit 0
fi

if [[ ! "$SSH_PORT" =~ ^[0-9]{1,5}$ ]] || (( SSH_PORT < 1 || SSH_PORT > 65535 )); then
    fail "Ungültiger SSH-Port"
fi

if $MIGRATE_FRONTEND; then
    [[ -n "$FRONTEND_ORIGIN" ]] || ask_value "Exakte Frontend-Origin (https://...)" FRONTEND_ORIGIN
    FRONTEND_ORIGIN="${FRONTEND_ORIGIN%/}"
    validate_origin "$FRONTEND_ORIGIN"
    [[ -n "$API_DOMAIN" ]] || ask_value "Öffentliche API-Domain ohne https://" API_DOMAIN
    validate_domain "$API_DOMAIN"
fi

if $MIGRATE_SERVERS; then
    [[ -n "$SERVER_IDS" ]] || ask_value "Gameserver-IDs, komma-separiert (z. B. 1,2,3)" SERVER_IDS
    [[ "$SERVER_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]] || fail "Ungültige Gameserver-ID-Liste"
    [[ -n "$TARGET_NODE_ID" ]] || ask_value "Zielnode-ID" TARGET_NODE_ID
    [[ "$TARGET_NODE_ID" =~ ^[1-9][0-9]*$ ]] || fail "Ungültige Zielnode-ID"
fi

if $MIGRATE_BACKEND; then
    [[ -n "$BACKEND_TARGET" ]] || ask_value "SSH-Ziel (z. B. root@203.0.113.10)" BACKEND_TARGET
    [[ "$BACKEND_TARGET" =~ ^([A-Za-z_][A-Za-z0-9._-]*@)?([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])$ ]] \
        || fail "Ungültiges SSH-Ziel; SSH-Optionen sind hier nicht erlaubt"
    [[ -n "$API_DOMAIN" ]] || ask_value "Neue öffentliche API-Domain ohne https://" API_DOMAIN
    validate_domain "$API_DOMAIN"

    if [[ -z "$FRONTEND_ORIGIN" ]]; then
        current_serve_frontend="$(env_value MSM_SERVE_FRONTEND || true)"
        if [[ "$current_serve_frontend" == "false" ]]; then
            FRONTEND_ORIGIN="$(env_value MSM_PANEL_URL || true)"
            [[ -n "$FRONTEND_ORIGIN" ]] && validate_origin "$FRONTEND_ORIGIN"
        elif [[ "$EXPLICIT_ACTION" == "false" ]] && ask_yes_no "Bleibt das Frontend extern gehostet?" "N"; then
            ask_value "Exakte Frontend-Origin (https://...)" FRONTEND_ORIGIN
            FRONTEND_ORIGIN="${FRONTEND_ORIGIN%/}"
            validate_origin "$FRONTEND_ORIGIN"
        fi
    fi
fi

echo ""
info "Geplanter Ablauf:"
$MIGRATE_FRONTEND && printf '  - externes Frontend %s mit API https://%s verbinden\n' "$FRONTEND_ORIGIN" "$API_DOMAIN"
$MIGRATE_SERVERS && printf '  - Gameserver %s nacheinander auf Node %s migrieren\n' "$SERVER_IDS" "$TARGET_NODE_ID"
$MIGRATE_BACKEND && printf '  - Backend nach %s:%s für https://%s migrieren\n' "$BACKEND_TARGET" "$SSH_PORT" "$API_DOMAIN"

[[ $EUID -eq 0 ]] || fail "Bitte mit sudo ausführen."
[[ -f "$SOURCE_ENV" ]] || fail "Bestehende MSM-Installation fehlt: $SOURCE_ENV"
[[ -x "$SOURCE_PYTHON" ]] || fail "Backend-Python fehlt: $SOURCE_PYTHON"

run_dry_run_preflight() {
    command -v curl >/dev/null || fail "curl fehlt"
    if $MIGRATE_FRONTEND; then
        local status
        status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$FRONTEND_ORIGIN" || true)"
        [[ "$status" =~ ^[23][0-9][0-9]$ ]] \
            || fail "Frontend ist nicht erfolgreich erreichbar (HTTP ${status:-0})"
    fi
    if $MIGRATE_SERVERS; then
        local server_id
        IFS=',' read -r -a ids <<< "$SERVER_IDS"
        for server_id in "${ids[@]}"; do
            (
                cd "$SOURCE_BACKEND"
                "$SOURCE_PYTHON" scripts/migrate_server_to_node.py \
                    --server-id "$server_id" --target-node-id "$TARGET_NODE_ID" \
                    --preflight-only
            )
        done
    fi
    if $MIGRATE_BACKEND; then
        command -v ssh >/dev/null || fail "ssh fehlt"
        command -v scp >/dev/null || fail "scp fehlt"
        command -v tar >/dev/null || fail "tar fehlt"
        command -v pg_dump >/dev/null || fail "pg_dump fehlt"
        systemctl is-active --quiet msm-panel.service || fail "Quell-Panel läuft nicht"
        ssh -o BatchMode=yes -o ConnectTimeout=15 -p "$SSH_PORT" "$BACKEND_TARGET" true \
            || fail "SSH-Ziel ist ohne interaktive Rückfrage nicht erreichbar"
    fi
    ok "Dry-run und lokale Vorprüfung abgeschlossen; es wurde nichts verändert."
}

if $DRY_RUN; then
    run_dry_run_preflight
    exit 0
fi

run_frontend_migration() {
    info "Prüfe das bereits veröffentlichte Frontend..."
    local status
    status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$FRONTEND_ORIGIN" || true)"
    [[ "$status" =~ ^[23][0-9][0-9]$ ]] \
        || fail "Frontend ist unter $FRONTEND_ORIGIN nicht erfolgreich erreichbar (HTTP ${status:-0})"

    echo "Das externe Frontend muss mit VITE_API_URL=https://$API_DOMAIN gebaut worden sein."
    echo "VITE_WS_URL kann leer bleiben und wird dann daraus abgeleitet."
    confirm_exact "FRONTEND READY" "Bestätige den fertigen Build"

    bash "$ROOT_DIR/install.sh" --simple --domain "$API_DOMAIN" \
        --external-frontend "$FRONTEND_ORIGIN"
    curl -fsS --max-time 10 http://127.0.0.1:8000/api/health >/dev/null

    local headers
    headers="$(mktemp)"
    curl -sS -o /dev/null -D "$headers" --max-time 10 -X OPTIONS \
        http://127.0.0.1:8000/api/auth/me \
        -H "Origin: $FRONTEND_ORIGIN" \
        -H 'Access-Control-Request-Method: GET'
    grep -Fqi "access-control-allow-origin: $FRONTEND_ORIGIN" "$headers" \
        || fail "Backend bestätigt die Frontend-Origin nicht per CORS"
    rm -f "$headers"
    ok "Externes Frontend und aktuelle Control-Plane sind verbunden."
}

run_server_migrations() {
    local expected="MIGRATE $SERVER_IDS" completed="" server_id
    echo "Jeder Server wird vollständig gesichert und auf dem Ziel wiederhergestellt."
    echo "Quelldaten werden nicht gelöscht; bei einem Fehler stoppt der Batch."
    confirm_exact "$expected" "Gameserver-Migration bestätigen"
    IFS=',' read -r -a ids <<< "$SERVER_IDS"
    for server_id in "${ids[@]}"; do
        info "Migriere Gameserver $server_id auf Node $TARGET_NODE_ID..."
        if ! (
            cd "$SOURCE_BACKEND"
            "$SOURCE_PYTHON" scripts/migrate_server_to_node.py \
                --server-id "$server_id" --target-node-id "$TARGET_NODE_ID" --yes
        ); then
            warn "Batch gestoppt. Bereits abgeschlossen: ${completed:-keine}. Quelldaten wurden beibehalten."
            return 1
        fi
        completed="${completed:+$completed,}$server_id"
    done
    ok "Gameserver-Migration abgeschlossen: $completed"
}

ssh_run() {
    ssh -o ControlMaster=auto -o ControlPersist=300 -o "ControlPath=$SSH_CONTROL" \
        -o ConnectTimeout=15 -p "$SSH_PORT" "$BACKEND_TARGET" "$@"
}

scp_to_target() {
    local source="$1" destination="$2"
    scp -q -o ControlMaster=auto -o ControlPersist=300 -o "ControlPath=$SSH_CONTROL" \
        -o ConnectTimeout=15 -P "$SSH_PORT" "$source" "$BACKEND_TARGET:$destination"
}

local_node_count() {
    (
        cd "$SOURCE_BACKEND"
        "$SOURCE_PYTHON" -c \
            'from database import SessionLocal; from models import Node; db=SessionLocal(); print(db.query(Node).filter(Node.is_local.is_(True)).count()); db.close()'
    )
}

create_code_archive() {
    local output="$1"
    tar -czf "$output" -C "$ROOT_DIR" \
        --exclude='.git' --exclude='node_modules' --exclude='venv' --exclude='venv-wsl' \
        --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.mypy_cache' \
        --exclude='.ruff_cache' --exclude='*.pyc' --exclude='.env' --exclude='.env.local' \
        --exclude='backend/tests' --exclude='msm-agent/tests' --exclude='frontend/dist' \
        README.md Caddyfile.template install.sh update.sh msm-update.service msm-update.timer \
        msm.service.template backend blueprints dis-sidecar docs frontend msm-agent scripts helper-scripts
}

create_runtime_archive() {
    local output="$1" paths=()
    [[ -d "$MSM_DIR/backups/panel" ]] && paths+=("backups/panel")
    [[ -d "$MSM_DIR/blueprints/community" ]] && paths+=("blueprints/community")
    [[ -f "$MSM_DIR/.setup_completed" ]] && paths+=(".setup_completed")
    if (( ${#paths[@]} == 0 )); then
        tar -czf "$output" --files-from /dev/null
    else
        tar -czf "$output" -C "$MSM_DIR" "${paths[@]}"
    fi
}

run_backend_migration() {
    command -v ssh >/dev/null || fail "ssh fehlt"
    command -v scp >/dev/null || fail "scp fehlt"
    command -v tar >/dev/null || fail "tar fehlt"
    command -v pg_dump >/dev/null || fail "pg_dump fehlt"
    systemctl is-active --quiet msm-panel.service || fail "Quell-Panel läuft nicht"

    WORK_DIR="$(mktemp -d /tmp/msm-component-migration.XXXXXX)"
    chmod 700 "$WORK_DIR"
    SSH_CONTROL="$WORK_DIR/ssh-%C"
    local run_id remote_stage code_archive preflight_dump required_bytes remote_free
    run_id="$(date +%s)-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
    remote_stage="/var/lib/msm-migration/$run_id"
    code_archive="$WORK_DIR/code.tar.gz"
    preflight_dump="$WORK_DIR/preflight.dump"

    info "Prüfe SSH-Ziel und Root-Rechte..."
    local remote_uid
    remote_uid="$(ssh_run "id -u")"
    if [[ "$remote_uid" == "0" ]]; then
        REMOTE_SUDO=""
    else
        ssh_run "command -v sudo >/dev/null && sudo -n true" >/dev/null \
            || fail "Ziel benötigt root-SSH oder passwortloses sudo"
        REMOTE_SUDO="sudo"
    fi
    ssh_run "$REMOTE_SUDO test ! -e /opt/msm/backend/.env" \
        || fail "Auf dem Ziel existiert bereits eine MSM-Installation; es wird nichts überschrieben"

    info "Erzeuge geprüfte Vorab-Sicherung und minimales Codepaket..."
    "$SOURCE_PYTHON" "$SOURCE_BACKEND/scripts/update_database_backup.py" \
        --env-file "$SOURCE_ENV" --output "$preflight_dump"
    create_code_archive "$code_archive"
    required_bytes=$(( $(stat -c '%s' "$code_archive") + $(stat -c '%s' "$preflight_dump") * 3 + 2147483648 ))
    remote_free="$(ssh_run "df -PB1 /opt 2>/dev/null | awk 'NR==2 {print \$4}' || df -PB1 / | awk 'NR==2 {print \$4}'")"
    [[ "$remote_free" =~ ^[0-9]+$ ]] || fail "Freier Speicher des Ziels konnte nicht ermittelt werden"
    (( remote_free >= required_bytes )) \
        || fail "Ziel hat zu wenig freien Speicher für Installation, Dump und Rollbackreserve"
    rm -f "$preflight_dump"

    info "Installiere die neue Backend-only-Control-Plane zunächst parallel..."
    ssh_run "$REMOTE_SUDO install -d -m 700 '$remote_stage' '$remote_stage/code'"
    scp_to_target "$code_archive" "/tmp/msm-code-$run_id.tar.gz"
    ssh_run "$REMOTE_SUDO mv '/tmp/msm-code-$run_id.tar.gz' '$remote_stage/code.tar.gz' && $REMOTE_SUDO tar -xzf '$remote_stage/code.tar.gz' -C '$remote_stage/code'"

    local remote_install="$REMOTE_SUDO bash '$remote_stage/code/install.sh' --simple --domain '$API_DOMAIN' --control-plane-only"
    if [[ -n "$FRONTEND_ORIGIN" ]]; then
        remote_install+=" --external-frontend '$FRONTEND_ORIGIN'"
    fi
    ssh_run "$remote_install"
    ssh_run "$REMOTE_SUDO caddy validate --config /etc/caddy/Caddyfile >/dev/null"
    ssh_run "curl -fsS --max-time 10 http://127.0.0.1:8000/api/health >/dev/null"

    if [[ "$(local_node_count)" != "0" ]]; then
        local current_api replacement_id enrollment_output
        current_api="$(env_value MSM_API_URL || true)"
        [[ -n "$current_api" ]] || current_api="$(env_value MSM_PANEL_URL || true)"
        validate_origin "$current_api"
        echo ""
        echo "Der vorhandene lokale Gameserver-Agent wird jetzt als eigenständiger TLS-Node neu eingerichtet."
        echo "Das Panel zeigt dabei eine Owner-Freigabe. Diese Sicherheitsfreigabe wird nicht umgangen."
        enrollment_output="$WORK_DIR/enrollment-result"
        bash "$ROOT_DIR/helper-scripts/install-msm-node.sh" --panel "$current_api" | tee "$enrollment_output"
        replacement_id="$(sed -n 's/^MSM_ENROLLED_NODE_ID=//p' "$enrollment_output" | tail -1)"
        [[ "$replacement_id" =~ ^[1-9][0-9]*$ ]] || fail "Ungültige Ersatz-Node-ID"
        (
            cd "$SOURCE_BACKEND"
            "$SOURCE_PYTHON" scripts/handoff_local_node.py \
                --replacement-node-id "$replacement_id" --yes
        )
    fi

    "$SOURCE_PYTHON" "$SOURCE_BACKEND/scripts/prepare_component_migration.py" \
        disable-local-agent --env-file "$SOURCE_ENV"

    echo ""
    echo "Für den finalen Cutover muss der DNS-A/AAAA-Eintrag von $API_DOMAIN auf den Zielserver zeigen."
    echo "Das Skript kann DNS ohne Zugang zu deinem Provider nicht selbst ändern."
    confirm_exact "DNS READY" "Bestätige, dass du den DNS-Eintrag beim Cutover setzen kannst"
    confirm_exact "MOVE BACKEND" "Finalen Backend-Cutover mit kurzer Downtime starten"

    local final_dump source_env_copy runtime_archive
    final_dump="$WORK_DIR/panel-final.dump"
    source_env_copy="$WORK_DIR/source.env"
    runtime_archive="$WORK_DIR/runtime.tar.gz"

    info "Stoppe die Quell-Control-Plane für den konsistenten finalen Dump..."
    systemctl is-active --quiet msm-update.timer && SOURCE_UPDATE_TIMER_ACTIVE=true
    systemctl stop msm-update.timer >/dev/null 2>&1 || true
    systemctl stop msm-panel.service
    SOURCE_STOPPED=true
    "$SOURCE_PYTHON" "$SOURCE_BACKEND/scripts/update_database_backup.py" \
        --env-file "$SOURCE_ENV" --output "$final_dump"
    install -m 600 "$SOURCE_ENV" "$source_env_copy"
    create_runtime_archive "$runtime_archive"

    scp_to_target "$final_dump" "/tmp/msm-panel-$run_id.dump"
    scp_to_target "$source_env_copy" "/tmp/msm-source-$run_id.env"
    scp_to_target "$runtime_archive" "/tmp/msm-runtime-$run_id.tar.gz"
    ssh_run "$REMOTE_SUDO mv '/tmp/msm-panel-$run_id.dump' '$remote_stage/panel.dump' && $REMOTE_SUDO mv '/tmp/msm-source-$run_id.env' '$remote_stage/source.env' && $REMOTE_SUDO mv '/tmp/msm-runtime-$run_id.tar.gz' '$remote_stage/runtime.tar.gz' && $REMOTE_SUDO chmod 600 '$remote_stage/panel.dump' '$remote_stage/source.env' '$remote_stage/runtime.tar.gz'"

    info "Stelle Datenbank, Konfiguration, DIS-Schlüssel, Backups und Blueprints auf dem Ziel wieder her..."
    local frontend_arg=""
    [[ -n "$FRONTEND_ORIGIN" ]] && frontend_arg="--frontend-origin '$FRONTEND_ORIGIN'"
    ssh_run "$REMOTE_SUDO bash -s -- '$remote_stage' 'https://$API_DOMAIN'" <<REMOTE_CUTOVER
set -Eeuo pipefail
umask 077
stage="\$1"
api_origin="\$2"
cutover_ok=false
cleanup_stage() {
    if ! \$cutover_ok; then
        systemctl stop msm-panel.service msm-dis-sidecar.service >/dev/null 2>&1 || true
    fi
    rm -rf -- "\$stage"
}
trap cleanup_stage EXIT
systemctl stop msm-panel.service
cp /opt/msm/backend/.env "\$stage/target.env"
chmod 600 "\$stage/target.env"
sudo -u postgres pg_restore --clean --if-exists --no-owner --role=msm \
    --dbname=msm "\$stage/panel.dump"
/opt/msm/backend/venv/bin/python /opt/msm/backend/scripts/prepare_component_migration.py \
    merge-target --source-env "\$stage/source.env" --target-env "\$stage/target.env" \
    --output-env "\$stage/backend.env" --dis-output "\$stage/dis.env" \
    --api-origin "\$api_origin" $frontend_arg
install -o msm -g msm -m 600 "\$stage/backend.env" /opt/msm/backend/.env
install -o msm -g msm -m 600 "\$stage/dis.env" /opt/msm/dis-sidecar/.env
tar -xzf "\$stage/runtime.tar.gz" -C /opt/msm
chown -R msm:msm /opt/msm/backups /opt/msm/blueprints 2>/dev/null || true
systemctl restart msm-dis-sidecar.service
systemctl restart msm-panel.service
deadline=\$((SECONDS + 180))
until curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null; do
    (( SECONDS < deadline )) || { journalctl -u msm-panel.service -n 30 --no-pager; exit 1; }
    sleep 2
done
caddy validate --config /etc/caddy/Caddyfile >/dev/null
if grep -Eq '^MSM_AUTO_UPDATE=(true|"true")$' /opt/msm/backend/.env; then
    systemctl enable --now msm-update.timer >/dev/null 2>&1 || true
fi
cutover_ok=true
REMOTE_CUTOVER

    TARGET_COMMITTED=true
    SOURCE_STOPPED=false
    systemctl disable msm-panel.service msm-dis-sidecar.service msm-update.timer >/dev/null 2>&1 || true
    systemctl stop msm-dis-sidecar.service >/dev/null 2>&1 || true

    ok "Backend-Daten und Konfiguration laufen geprüft auf dem Zielserver."
    echo "Setze den DNS-A/AAAA-Eintrag für $API_DOMAIN jetzt auf den Zielserver."
    echo "Die Ziel-Control-Plane ist bereits aktiv; die alte bleibt gegen Split-Brain deaktiviert."
    info "Warte bis die öffentliche API-Domain das Ziel erreicht (maximal 10 Minuten)..."
    local deadline=$((SECONDS + 600))
    until curl -fsS --max-time 10 "https://$API_DOMAIN/api/health" >/dev/null 2>&1; do
        if (( SECONDS >= deadline )); then
            warn "Ziel läuft lokal, aber die öffentliche Domain ist noch nicht erreichbar. Prüfe DNS/Cloud-Firewall; starte keine zweite Control-Plane."
            return 0
        fi
        sleep 5
    done
    ok "Öffentliche Backend-API ist erreichbar. Die alte Control-Plane bleibt deaktiviert; der Agent läuft weiter."
}

# Gameserver first, backend last: after a successful backend cutover the source
# database intentionally ceases to be authoritative.
$MIGRATE_FRONTEND && run_frontend_migration
$MIGRATE_SERVERS && run_server_migrations
$MIGRATE_BACKEND && run_backend_migration

ok "Alle ausgewählten Migrationsschritte sind abgeschlossen."
