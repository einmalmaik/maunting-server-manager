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
MSM_USER="msm"
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

# Setzt Besitz der Panel-Dateien zurueck auf $MSM_USER.
# WICHTIG: /opt/msm/servers (per-Game-Server-User) und /opt/msm/backups
# (eigene Backup-Daten) NICHT anfassen, sonst kippen Datei-Rechte fuer
# laufende Game-Server-Container. Wir chownen nur die Panel-Code-Pfade,
# die `git pull` als root ueberschrieben hat.
restore_panel_ownership() {
    [[ -d "$MSM_DIR" ]] || return 0
    # Wenn der msm-User noch gar nicht existiert (z.B. allererster Run vor
    # install.sh), gibt es nichts zu chownen.
    id "$MSM_USER" &>/dev/null || return 0
    # .git muss msm geschrieben werden koennen (sonst kann msm spaeter nicht
    # `git describe` o.ae. ausfuehren).
    if [[ -d "$MSM_DIR/.git" ]]; then
        chown -R "$MSM_USER:$MSM_USER" "$MSM_DIR/.git" 2>/dev/null || true
    fi
    for sub in backend frontend docs; do
        if [[ -d "$MSM_DIR/$sub" ]]; then
            chown -R "$MSM_USER:$MSM_USER" "$MSM_DIR/$sub" 2>/dev/null || true
        fi
    done
    # npm-Cache des msm-Users (HOME=/opt/msm). Falls ein frueherer fehlge-
    # schlagener Run als root in den Cache geschrieben hat, scheitert npm
    # sonst beim naechsten Lauf.
    if [[ -d "$MSM_DIR/.npm" ]]; then
        chown -R "$MSM_USER:$MSM_USER" "$MSM_DIR/.npm" 2>/dev/null || true
    fi
    # Top-Level-Dateien (Scripts, Templates, README, etc.).
    # `find -maxdepth 1` haelt /opt/msm/servers und /opt/msm/backups raus.
    find "$MSM_DIR" -maxdepth 1 -type f \
        -exec chown "$MSM_USER:$MSM_USER" {} + 2>/dev/null || true
}

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

SYSTEMD_AVAILABLE=false
if [[ -d /run/systemd/system ]] && command -v systemctl &>/dev/null; then
    SYSTEMD_AVAILABLE=true
fi

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
            # Recovery: ein frueherer Run (z.B. mit der alten update.sh ohne
            # Chown-Fix) kann Dateien als root zurueckgelassen haben. Bevor wir
            # frueh aussteigen, stellen wir Besitz wieder her, damit der
            # Panel-Service als msm-User schreibend zugreifen kann.
            restore_panel_ownership
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
    # Recovery wie im Git-Pfad: Besitz zurueck auf msm, falls ein frueherer
    # Run Dateien als root liegen gelassen hat.
    restore_panel_ownership
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
    # git pull lief als root -> neue/geaenderte Dateien sind jetzt root-owned.
    # Ohne den folgenden Chown scheitert `su - msm -c 'npm install'` mit EACCES
    # auf z.B. frontend/package-lock.json.
    restore_panel_ownership
elif [[ -d ".git" ]]; then
    log "Aktualisiere via Git checkout..."
    git fetch origin --tags
    git checkout "$LATEST_TAG" || {
        err "Konnte nicht auf $LATEST_TAG wechseln. Rollback..."
        git checkout "$CURRENT_VERSION" 2>/dev/null || true
        exit 1
    }
    restore_panel_ownership
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
    restore_panel_ownership
fi

# ── sudoers + iptables-Wrapper direkt als root schreiben (Sicherheitsfix) ──
# Nie aus dem (nach restore_panel_ownership) msm-owned $MSM_DIR lesen.
# Direkte Heredocs als root (Wrapper + Policy). SSOT im Repo bleibt Review-Master.
if [[ -d /etc/sudoers.d ]]; then
    log "Aktualisiere sudoers-Regeln..."
    mkdir -p /usr/local/sbin
    # Wrapper direkt als root
    cat > /usr/local/sbin/msm-iptables <<'WRAPEOF'
#!/bin/sh
# MSM iptables wrapper — thin, root-owned privilege gate for DOCKER-USER defense-in-depth.
set -eu
IPT="/usr/sbin/iptables"
if [ $# -eq 0 ]; then
    printf 'msm-iptables: refused disallowed invocation (no args)\n' >&2
    exit 1
fi
case "${1:-}" in
    --version)
        exec "$IPT" "$@"
        ;;
    -L)
        if [ $# -eq 3 ] && [ "$2" = "DOCKER-USER" ] && [ "$3" = "-n" ]; then
            exec "$IPT" "$@"
        fi
        ;;
    -C|-A|-D)
        if [ "$2" = "DOCKER-USER" ]; then
            exec "$IPT" "$@"
        fi
        ;;
    -I)
        if [ "$2" = "DOCKER-USER" ]; then
            exec "$IPT" "$@"
        fi
        ;;
esac
printf 'msm-iptables: refused disallowed invocation: %s\n' "$*" >&2
exit 1
WRAPEOF
    chown root:root /usr/local/sbin/msm-iptables
    chmod 755 /usr/local/sbin/msm-iptables

    # Policy direkt als root (nur Firewall-Gates, kein Container-systemctl)
    cat > /etc/sudoers.d/msm-panel <<'SUDOEOF'
# MSM Panel — Firewall (UFW/iptables) only
# Deployed via root heredoc (never from msm-writable tree at update time).

# UFW (exact; delete tightened)
msm ALL=(root) NOPASSWD: /usr/sbin/ufw --version
msm ALL=(root) NOPASSWD: /usr/sbin/ufw allow [0-9]*/[a-z]* comment *
msm ALL=(root) NOPASSWD: /usr/sbin/ufw delete allow [0-9]*/[a-z]*
msm ALL=(root) NOPASSWD: /usr/sbin/ufw status numbered

# iptables ONLY via vetted wrapper
msm ALL=(root) NOPASSWD: /usr/local/sbin/msm-iptables
SUDOEOF
    chmod 440 /etc/sudoers.d/msm-panel
    ok "sudoers aktualisiert (direkt als root + Wrapper, ohne Container-systemctl)"
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
# Letzte Verteidigungslinie: selbst wenn alle vorherigen Pfade einen Chown
# ausgelassen haben sollten (z.B. unerwarteter Code-Pfad), stellen wir hier
# noch einmal sicher, dass der msm-User in frontend/ schreiben kann. Idempotent
# und billig.
restore_panel_ownership
log "Baue Frontend..."
if ! su - msm -c "
    set -e
    cd $MSM_DIR/frontend
    npm install -q
    npm run build
" 2>&1 | tee -a "$LOG_FILE"; then
    err "Frontend-Build fehlgeschlagen. Update abgebrochen."
fi

# ── Panel-Service reparieren: NoNewPrivileges entfernen (blockiert sudo) ──
PANEL_UNIT="/etc/systemd/system/msm-panel.service"
if [[ -f "$PANEL_UNIT" ]] && grep -q 'NoNewPrivileges=true' "$PANEL_UNIT"; then
    log "Entferne NoNewPrivileges aus Panel-Service (inkompatibel mit sudo)..."
    sed -i '/^NoNewPrivileges=true$/d' "$PANEL_UNIT"
    if $SYSTEMD_AVAILABLE; then
        systemctl daemon-reload
    fi
    ok "Panel-Service aktualisiert"
fi

# ── Panel-Service reparieren: System-PATH erweitern, damit docker auffindbar ──
# Alte Installationen hatten ``PATH=/opt/msm/backend/venv/bin`` ohne ``/usr/bin``.
# Folge: ``shutil.which("docker")`` schlug fehl, der Console-Stream meldete
# permanent "Docker CLI nicht im PATH des Backends". Idempotenter sed-Fix.
NEW_PATH_LINE='Environment="PATH=/opt/msm/backend/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"'
if [[ -f "$PANEL_UNIT" ]] && grep -qE '^Environment="PATH=/opt/msm/backend/venv/bin"\s*$' "$PANEL_UNIT"; then
    log "Erweitere Panel-Service PATH um System-Pfade (Docker-CLI auffindbar machen)..."
    sed -i "s|^Environment=\"PATH=/opt/msm/backend/venv/bin\"\s*\$|${NEW_PATH_LINE}|" "$PANEL_UNIT"
    if $SYSTEMD_AVAILABLE; then
        systemctl daemon-reload
    fi
    ok "Panel-Service PATH erweitert"
fi

# ── Panel-Service reparieren: UFW-ReadWritePaths optional markieren und -/run/run hinzufügen ──
# Wenn UFW beim Service-Start die Lockdatei noch nicht erzeugt hat,
# scheitert das systemd-Namespacing mit ``status=226/NAMESPACE``.
# ``-``-Praefix laesst systemd fehlende Pfade still ueberspringen.
# ``-/run/user`` wird fuer die Verbindung zum Rootless Docker Socket benoetigt.
GOOD_RWP="ReadWritePaths=/opt/msm -/etc/ufw -/var/lib/ufw -/run/ufw -/run/ufw.lock -/run/user"
BAD_RWP_1="ReadWritePaths=/opt/msm /etc/systemd/system /run/ufw /var/lib/ufw /etc/ufw /run/ufw.lock"
BAD_RWP_2="ReadWritePaths=/opt/msm -/etc/ufw -/var/lib/ufw -/run/ufw -/run/ufw.lock"

UPDATED_RWP=false
if [[ -f "$PANEL_UNIT" ]]; then
    if grep -qF "$BAD_RWP_1" "$PANEL_UNIT"; then
        sed -i "s|${BAD_RWP_1}|${GOOD_RWP}|" "$PANEL_UNIT"
        UPDATED_RWP=true
    elif grep -qF "$BAD_RWP_2" "$PANEL_UNIT"; then
        sed -i "s|${BAD_RWP_2}|${GOOD_RWP}|" "$PANEL_UNIT"
        UPDATED_RWP=true
    elif ! grep -qF "-/run/user" "$PANEL_UNIT"; then
        sed -i 's|^ReadWritePaths=\(.*\)|ReadWritePaths=\1 -/run/user|' "$PANEL_UNIT"
        UPDATED_RWP=true
    fi
fi

if $UPDATED_RWP; then
    log "Aktualisiere ReadWritePaths des Panel-Services (füge -/run/user hinzu)..."
    if $SYSTEMD_AVAILABLE; then
        systemctl daemon-reload
    fi
    ok "Panel-Service ReadWritePaths aktualisiert"
fi

# ── Panel-Service reparieren: ProtectHome=true deaktivieren ──
# ProtectHome=true macht /run/user fuer das Panel unsichtbar, was den Zugriff
# auf den Rootless Docker Socket blockiert. Wir aendern es auf false.
if [[ -f "$PANEL_UNIT" ]] && grep -q 'ProtectHome=true' "$PANEL_UNIT"; then
    log "Deaktiviere ProtectHome im Panel-Service (erforderlich für Zugriff auf /run/user)..."
    sed -i 's|ProtectHome=true|ProtectHome=false|' "$PANEL_UNIT"
    if $SYSTEMD_AVAILABLE; then
        systemctl daemon-reload
    fi
    ok "Panel-Service ProtectHome deaktiviert"
fi

# ── Rootless Docker reparieren: UDP Source-IP Fix (slirp4netns) ──
# Rootless Docker's default 'builtin' port driver drops Source-IPs for UDP connections.
# This prevents games like DayZ or Ark from functioning properly. We force slirp4netns.
if ! command -v slirp4netns &>/dev/null; then
    log "Installiere slirp4netns für UDP Source-IP Erhalt..."
    apt-get install -y -qq slirp4netns 2>/dev/null || true
fi
if id "msm" &>/dev/null; then
    MSM_UID=$(id -u "msm")
    MSM_OVERRIDE_CONF="/opt/msm/.config/systemd/user/docker.service.d/override.conf"
    if ! su - msm -c "grep -q 'DOCKERD_ROOTLESS_ROOTLESSKIT_PORT_DRIVER=slirp4netns' $MSM_OVERRIDE_CONF 2>/dev/null"; then
        log "Konfiguriere Rootless Docker für Source-IP Erhalt bei UDP (slirp4netns)..."
        su - msm -c "mkdir -p /opt/msm/.config/systemd/user/docker.service.d && echo -e '[Service]\nEnvironment=\"DOCKERD_ROOTLESS_ROOTLESSKIT_PORT_DRIVER=slirp4netns\"' > $MSM_OVERRIDE_CONF" 2>/dev/null || true
        if $SYSTEMD_AVAILABLE; then
            su - msm -c "export XDG_RUNTIME_DIR=/run/user/${MSM_UID}; systemctl --user daemon-reload && systemctl --user restart docker.service" 2>/dev/null || true
            ok "Rootless Docker mit slirp4netns neugestartet"
        fi
    fi
fi

# ── Service neustarten ──
log "Starte Services neu..."
if $SYSTEMD_AVAILABLE; then
    systemctl restart msm-panel.service
    systemctl restart caddy 2>/dev/null || true
else
    warn "systemd nicht verfügbar — Services können nicht neu gestartet werden."
fi

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
