#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
#  Maunting Server Manager — Updater
#
#  Usage:  sudo bash update.sh [--check-only] [--force]
#
#  Prüft zuerst GitHub-Releases, falls keine existieren
#  wird der neueste main-Branch-Commit verwendet.
#
#  Für Tauri: derselbe Release-Feed (latest.json auf GitHub).
# ═══════════════════════════════════════════════════════════════

MSM_DIR="/opt/msm"
LOG_FILE="/tmp/msm-update.log"
BACKUP_DIR="/opt/msm/backups"
ENV_FILE="$MSM_DIR/backend/.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${CYAN}[UPDATE]${NC} $1" | tee -a "$LOG_FILE"; }
ok()   { echo -e "${GREEN}[OK]${NC}   $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }
err()  { echo -e "${RED}[ERR]${NC}  $1" | tee -a "$LOG_FILE"; exit 1; }

CHECK_ONLY=false
FORCE=false

for arg in "$@"; do
    case "$arg" in
        --check-only) CHECK_ONLY=true ;;
        --force) FORCE=true ;;
    esac
done

# ── Config laden ──
if [[ -f "$ENV_FILE" ]]; then
    # Shell-sicheres Einlesen der .env
    GITHUB_OWNER=$(grep -E '^MSM_GITHUB_OWNER=' "$ENV_FILE" | cut -d'"' -f2 || echo "")
    GITHUB_REPO=$(grep -E '^MSM_GITHUB_REPO=' "$ENV_FILE" | cut -d'"' -f2 || echo "")
    AUTO_UPDATE=$(grep -E '^MSM_AUTO_UPDATE=' "$ENV_FILE" | cut -d'=' -f2 || echo "false")
fi

GITHUB_OWNER="${GITHUB_OWNER:-einmalmaik}"
GITHUB_REPO="${GITHUB_REPO:-maunting-server-manager}"
AUTO_UPDATE="${AUTO_UPDATE:-false}"

log "=== Maunting Server Manager Updater ==="
log "Repository: $GITHUB_OWNER/$GITHUB_REPO"
log ""

# ── Root-Check ──
if [[ $EUID -ne 0 ]]; then
    err "Bitte als root ausführen: sudo bash update.sh"
fi

# ── Aktuelle Version ermitteln ──
CURRENT_VERSION="unknown"
UPDATE_MODE="release"   # "release" oder "git"
LATEST_TAG=""
RELEASE_JSON=""

if [[ -d "$MSM_DIR/.git" ]]; then
    cd "$MSM_DIR"
    CURRENT_VERSION=$(git describe --tags --always 2>/dev/null || echo "unknown")
fi

log "Aktuelle Version: $CURRENT_VERSION"

# ═══════════════════════════════════════════════════════════════
# 1) Zuerst: GitHub Release prüfen
# ═══════════════════════════════════════════════════════════════
log "Prüfe GitHub Releases..."
RELEASE_JSON=$(curl -s -L \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/releases/latest" 2>/dev/null) || true

if [[ -n "$RELEASE_JSON" ]] && [[ "$RELEASE_JSON" != *"Not Found"* ]]; then
    LATEST_TAG=$(echo "$RELEASE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name',''))" 2>/dev/null || echo "")
fi

# ═══════════════════════════════════════════════════════════════
# 2) Kein Release? → Git main-Branch als Fallback
# ═══════════════════════════════════════════════════════════════
if [[ -z "$LATEST_TAG" ]]; then
    if [[ -d "$MSM_DIR/.git" ]]; then
        UPDATE_MODE="git"
        log "Kein GitHub-Release gefunden. Prüfe Git main-Branch..."
        cd "$MSM_DIR"
        git fetch origin main 2>/dev/null || {
            warn "Konnte origin/main nicht fetchen. Prüfe Internet-Verbindung."
            if ! $FORCE; then exit 0; fi
        }
        LOCAL_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
        REMOTE_SHA=$(git rev-parse origin/main 2>/dev/null || echo "")

        if [[ -z "$LOCAL_SHA" ]] || [[ -z "$REMOTE_SHA" ]]; then
            warn "Konnte Git-Commits nicht ermitteln."
            if ! $FORCE; then exit 0; fi
            LATEST_TAG="unknown"
        elif [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
            ok "Panel ist bereits auf dem neuesten Stand (main: ${LOCAL_SHA:0:8})."
            exit 0
        else
            LATEST_TAG="main-${REMOTE_SHA:0:8}"
            log "Neuer Commit auf main: ${REMOTE_SHA:0:8}"
        fi
    else
        warn "Kein GitHub-Release und kein Git-Repo gefunden."
        if ! $FORCE; then exit 0; fi
        LATEST_TAG="unknown"
    fi
else
    log "Neueste Release: $LATEST_TAG"
fi

# ── Vergleich (nur bei Release-Mode) ──
if [[ "$UPDATE_MODE" == "release" ]] && [[ "$CURRENT_VERSION" == "$LATEST_TAG" ]]; then
    ok "Panel ist bereits auf dem neuesten Stand ($CURRENT_VERSION)."
    exit 0
fi

# ── Nur prüfen? ──
if $CHECK_ONLY; then
    echo ""
    if [[ "$UPDATE_MODE" == "git" ]]; then
        echo -e "${YELLOW}Update verfügbar auf main!${NC}"
        echo -e "  Aktuell: ${CYAN}${LOCAL_SHA:0:8}${NC}"
        echo -e "  Neu:     ${CYAN}${REMOTE_SHA:0:8}${NC}"
    else
        echo -e "${YELLOW}Update verfügbar!${NC}"
        echo -e "  Aktuell: ${CYAN}$CURRENT_VERSION${NC}"
        echo -e "  Neu:     ${CYAN}$LATEST_TAG${NC}"
    fi
    echo -e "  Installieren: ${BOLD}sudo bash update.sh${NC}"
    echo ""
    exit 0
fi

# ── Auto-Update deaktiviert? ──
if [[ "$AUTO_UPDATE" != "true" ]] && ! $FORCE; then
    warn "Automatisches Update ist deaktiviert."
    echo "  Setze MSM_AUTO_UPDATE=true in $ENV_FILE"
    echo "  Oder führe aus: sudo bash update.sh --force"
    exit 0
fi

# ═══════════════════════════════════════════════════════════════
# Update durchführen
# ═══════════════════════════════════════════════════════════════

echo ""
if [[ "$UPDATE_MODE" == "git" ]]; then
    echo -e "${YELLOW}Update wird installiert:${NC} main ${LOCAL_SHA:0:8} → ${REMOTE_SHA:0:8}"
else
    echo -e "${YELLOW}Update wird installiert:${NC} $CURRENT_VERSION → $LATEST_TAG"
fi
echo ""

# ── Backup ──
log "Erstelle Backup..."
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/msm-backup-$(date +%Y%m%d-%H%M%S).tar.gz"

tar -czf "$BACKUP_FILE" \
    -C "$MSM_DIR" \
    --exclude=venv \
    --exclude=node_modules \
    --exclude=__pycache__ \
    backend frontend .env 2>/dev/null || true

# DB extra backup (wenn SQLite)
DB_PATH="$MSM_DIR/backend/msm.db"
if [[ -f "$DB_PATH" ]]; then
    cp "$DB_PATH" "$BACKUP_DIR/msm-db-$(date +%Y%m%d-%H%M%S).db"
fi

ok "Backup erstellt: $BACKUP_FILE"

# ── Git Pull oder Tarball ──
cd "$MSM_DIR"

if [[ "$UPDATE_MODE" == "git" ]]; then
    log "Aktualisiere via Git pull..."
    # Backup des aktuellen HEAD für Rollback
    ROLLBACK_SHA="$LOCAL_SHA"
    git pull origin main || {
        err "Git pull fehlgeschlagen. Versuche Rollback..."
        git reset --hard "$ROLLBACK_SHA" 2>/dev/null || true
        exit 1
    }
    # Alte Build-Artefakte entfernen (nur dist/, keine Server-Daten!)
    rm -rf frontend/dist 2>/dev/null || true
elif [[ -d ".git" ]]; then
    log "Aktualisiere via Git checkout..."
    git fetch origin --tags
    git checkout "$LATEST_TAG" || {
        err "Konnte nicht auf $LATEST_TAG wechseln. Rollback..."
        git checkout "$CURRENT_VERSION" 2>/dev/null || true
        exit 1
    }
else
    log "Lade Release-Tarball..."
    TARBALL_URL=$(echo "$RELEASE_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for asset in data.get('assets', []):
    if asset['name'].endswith('.tar.gz'):
        print(asset['browser_download_url'])
        break
" 2>/dev/null || echo "")

    if [[ -z "$TARBALL_URL" ]]; then
        warn "Kein Tarball im Release gefunden. Versuche git clone..."
        git clone --depth 1 --branch "$LATEST_TAG" \
            "https://github.com/$GITHUB_OWNER/$GITHUB_REPO.git" /tmp/msm-update
        cp -r /tmp/msm-update/* "$MSM_DIR/"
        rm -rf /tmp/msm-update
    else
        curl -sL "$TARBALL_URL" | tar -xz -C /tmp
        # Annahme: Tarball enthält Ordner mit Release-Name
        EXTRACTED=$(ls -d /tmp/mauntingservermanager* 2>/dev/null | head -1)
        if [[ -n "$EXTRACTED" ]]; then
            cp -r "$EXTRACTED"/* "$MSM_DIR/"
            rm -rf "$EXTRACTED"
        fi
    fi
fi

# ── sudoers aktualisieren (immer, damit neue Regeln sofort gelten) ──
if [[ -d /etc/sudoers.d ]]; then
    log "Aktualisiere sudoers-Regeln..."
    cat > /etc/sudoers.d/msm-panel <<'SUDOEOF'
# MSM Panel — Game-Server systemd-Unit-Verwaltung
msm ALL=(root) NOPASSWD: /usr/bin/systemctl daemon-reload
msm ALL=(root) NOPASSWD: /usr/bin/systemctl enable msm-*.service
msm ALL=(root) NOPASSWD: /usr/bin/systemctl disable msm-*.service
msm ALL=(root) NOPASSWD: /usr/bin/systemctl start msm-*.service
msm ALL=(root) NOPASSWD: /usr/bin/systemctl stop msm-*.service
msm ALL=(root) NOPASSWD: /usr/bin/systemctl is-active msm-*.service
msm ALL=(root) NOPASSWD: /usr/bin/tee /etc/systemd/system/msm-*.service
msm ALL=(root) NOPASSWD: /usr/bin/rm -f /etc/systemd/system/msm-*.service
msm ALL=(root) NOPASSWD: /usr/sbin/useradd -r -m -s /usr/sbin/nologin -d * msm_srv_*
msm ALL=(root) NOPASSWD: /usr/sbin/usermod -s /usr/sbin/nologin msm_srv_*
msm ALL=(root) NOPASSWD: /usr/sbin/userdel -r msm_srv_*
msm ALL=(root) NOPASSWD: /usr/bin/chown msm_srv_*:msm_srv_* *
msm ALL=(root) NOPASSWD: /usr/bin/chmod 750 *
SUDOEOF
    chmod 440 /etc/sudoers.d/msm-panel
    ok "sudoers aktualisiert"
fi

# ── Backups-Verzeichnis sicherstellen ──
mkdir -p /opt/msm/backups
chown msm:msm /opt/msm/backups 2>/dev/null || true

# ── Backend aktualisieren ──
log "Aktualisiere Python-Abhängigkeiten..."
su - msm -c "
    cd $MSM_DIR/backend
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
" 2>&1 | tee -a "$LOG_FILE"

# ── Datenbank-Migrationen ──
log "Führe Datenbank-Migrationen durch..."
su - msm -c "
    cd $MSM_DIR/backend
    source venv/bin/activate
    alembic upgrade head 2>/dev/null || python3 -c \"from database import engine, Base; from models import *; Base.metadata.create_all(engine)\"
" 2>&1 | tee -a "$LOG_FILE"

# ── Frontend bauen ──
log "Baue Frontend..."
if ! su - msm -c "
    set -e
    cd $MSM_DIR/frontend
    npm install -q
    npm run build
" 2>&1 | tee -a "$LOG_FILE"; then
    err "Frontend-Build fehlgeschlagen. Update abgebrochen."
fi

# ── Service neustarten ──
log "Starte Services neu..."
systemctl restart msm-panel.service
systemctl restart caddy 2>/dev/null || true

# ── Version aktualisieren ──
cd "$MSM_DIR"
NEW_VERSION=$(git describe --tags --always 2>/dev/null || echo "$LATEST_TAG")

# ── Fertig ──
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Update erfolgreich!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Version:${NC} $CURRENT_VERSION → ${GREEN}$NEW_VERSION${NC}"
echo -e "  ${BOLD}Backup:${NC}  $BACKUP_FILE"
echo -e "  ${BOLD}Log:${NC}     $LOG_FILE"
echo ""
echo -e "  ${BOLD}Wichtig:${NC}"
echo -e "    - Prüfe das Panel im Browser"
echo -e "    - Bei Problemen: Rollback via Backup"
echo ""
