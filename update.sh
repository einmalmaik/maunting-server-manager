#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════
#  Maunting Server Manager — GitHub-Release Updater
#
#  Usage:  sudo bash update.sh [--check-only] [--force]
#
#  Prüft GitHub Releases auf neue Versionen und installiert
#  sie automatisch (mit Backup + Rollback bei Fehler).
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
GITHUB_REPO="${GITHUB_REPO:-mauntingservermanager}"
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
if [[ -d "$MSM_DIR/.git" ]]; then
    cd "$MSM_DIR"
    CURRENT_VERSION=$(git describe --tags --always 2>/dev/null || echo "unknown")
fi

log "Aktuelle Version: $CURRENT_VERSION"

# ── GitHub Release prüfen ──
log "Prüfe GitHub Releases..."
RELEASE_JSON=$(curl -s -L \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/releases/latest" 2>/dev/null) || true

if [[ -z "$RELEASE_JSON" ]] || [[ "$RELEASE_JSON" == *"Not Found"* ]]; then
    warn "Kein GitHub-Release gefunden oder API-Limit erreicht."
    if ! $FORCE; then
        exit 0
    fi
fi

LATEST_TAG=$(echo "$RELEASE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name',''))" 2>/dev/null || echo "")

if [[ -z "$LATEST_TAG" ]]; then
    warn "Konnte neueste Version nicht ermitteln."
    if ! $FORCE; then
        exit 0
    fi
    LATEST_TAG="unknown"
fi

log "Neueste Release: $LATEST_TAG"

# ── Vergleich ──
if [[ "$CURRENT_VERSION" == "$LATEST_TAG" ]]; then
    ok "Panel ist bereits auf dem neuesten Stand ($CURRENT_VERSION)."
    exit 0
fi

# ── Nur prüfen? ──
if $CHECK_ONLY; then
    echo ""
    echo -e "${YELLOW}Update verfügbar!${NC}"
    echo -e "  Aktuell: ${CYAN}$CURRENT_VERSION${NC}"
    echo -e "  Neu:     ${CYAN}$LATEST_TAG${NC}"
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
echo -e "${YELLOW}Update wird installiert:${NC} $CURRENT_VERSION → $LATEST_TAG"
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

if [[ -d ".git" ]]; then
    log "Aktualisiere via Git..."
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
su - msm -c "
    cd $MSM_DIR/frontend
    npm install -q
    npm run build
" 2>&1 | tee -a "$LOG_FILE"

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
