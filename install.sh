#!/usr/bin/env bash
set -euo pipefail

# ── Global PATH: verhindert "mkdir: not found" in steamcmd-Wrapper ──
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# ═══════════════════════════════════════════════════════════════
#  Maunting Server Manager — Zero-Config One-Line Installer
#  Supports: Ubuntu 22.04+, Debian 12+, WSL2
#
#  Usage:  sudo bash install.sh
#  ─────────────────────────────────────────────────────────────
#  Dieses Script richtet VOLLSTÄNDIG und AUTONOM ein:
#    - System-Abhängigkeiten (Node 20, Python, Caddy)
#    - Redis (optional, empfohlen für Rate-Limiting)
#    - Sichere .env mit automatisch generiertem SECRET_KEY
#    - SMTP-Konfiguration (für Email-Verifikation & 2FA)
#    - Caddy Reverse-Proxy mit automatischem TLS (Let's Encrypt)
#    - Firewall (UFW) + Fail2ban (Brute-Force-Schutz)
#    - systemd Service mit Auto-Restart
#    - Frontend-Build + Datenbank-Initialisierung
#  ─────────────────────────────────────────────────────────────
#  Unterstützt Frisch-Installation UND Re-Install / Config-Change
# ═══════════════════════════════════════════════════════════════

MSM_USER="msm"
MSM_DIR="/opt/msm"
LOG_FILE="/tmp/msm-install.log"

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${CYAN}[MSM]${NC}  $1" | tee -a "$LOG_FILE"; }
ok()   { echo -e "${GREEN}[OK]${NC}   $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }
err()  { echo -e "${RED}[ERR]${NC}  $1" | tee -a "$LOG_FILE"; exit 1; }
ask()  { read -rp "$(echo -e "${BOLD}[?]${NC} $1 ")" "$2"; }
ask_yesno() {
    local ans
    while true; do
        read -rp "$(echo -e "${BOLD}[?]${NC} $1 [Y/n]: ")" ans
        case "${ans:-Y}" in
            [Yy]*) return 0 ;;
            [Nn]*) return 1 ;;
        esac
    done
}
ask_yesno_default() {
    local question="$1"
    local default="$2"
    local ans
    while true; do
        if [[ "$default" == "Y" ]]; then
            read -rp "$(echo -e "${BOLD}[?]${NC} $question [Y/n]: ")" ans
            case "${ans:-Y}" in
                [Yy]*) return 0 ;;
                [Nn]*) return 1 ;;
            esac
        else
            read -rp "$(echo -e "${BOLD}[?]${NC} $question [y/N]: ")" ans
            case "${ans:-N}" in
                [Yy]*) return 0 ;;
                [Nn]*) return 1 ;;
            esac
        fi
    done
}

# ═══════════════════════════════════════════════════════════════
# Re-Install Hilfsfunktionen
# ═══════════════════════════════════════════════════════════════

load_current_env() {
    local env_file="$MSM_DIR/backend/.env"

    CURRENT_DOMAIN=""
    CURRENT_EMAIL_PROVIDER="smtp"
    CURRENT_SMTP_HOST=""
    CURRENT_SMTP_PORT="587"
    CURRENT_SMTP_USER=""
    CURRENT_SMTP_PASS=""
    CURRENT_SMTP_FROM=""
    CURRENT_RESEND_API_KEY=""
    CURRENT_USE_POSTGRES=false
    CURRENT_REDIS_URL=""
    CURRENT_AUTO_UPDATE="false"
    CURRENT_SECRET_KEY=""
    CURRENT_DB_URL=""
    CURRENT_DB_URL_ASYNC=""

    [[ -f "$env_file" ]] || return

    local val

    val=$(grep -E '^MSM_PANEL_URL=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    if [[ -n "$val" ]]; then
        CURRENT_DOMAIN="$val"
        CURRENT_DOMAIN="${CURRENT_DOMAIN#http://}"
        CURRENT_DOMAIN="${CURRENT_DOMAIN#https://}"
    fi

    val=$(grep -E '^MSM_EMAIL_PROVIDER=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_EMAIL_PROVIDER="$val"

    val=$(grep -E '^MSM_SMTP_HOST=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_SMTP_HOST="$val"

    val=$(grep -E '^MSM_SMTP_PORT=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_SMTP_PORT="$val"

    val=$(grep -E '^MSM_SMTP_USER=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_SMTP_USER="$val"

    val=$(grep -E '^MSM_SMTP_PASSWORD=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_SMTP_PASS="$val"

    val=$(grep -E '^MSM_SMTP_FROM=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_SMTP_FROM="$val"

    val=$(grep -E '^MSM_RESEND_API_KEY=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_RESEND_API_KEY="$val"

    val=$(grep -E '^MSM_DATABASE_URL=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    if [[ "$val" == postgresql* ]]; then
        CURRENT_USE_POSTGRES=true
    else
        CURRENT_USE_POSTGRES=false
    fi
    [[ -n "$val" ]] && CURRENT_DB_URL="$val"

    val=$(grep -E '^MSM_DATABASE_URL_ASYNC=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_DB_URL_ASYNC="$val"

    val=$(grep -E '^MSM_REDIS_URL=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_REDIS_URL="$val"

    val=$(grep -E '^MSM_AUTO_UPDATE=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_AUTO_UPDATE="$val"

    val=$(grep -E '^MSM_SECRET_KEY=' "$env_file" | cut -d'=' -f2- | sed 's/^"//;s/"$//' || true)
    [[ -n "$val" ]] && CURRENT_SECRET_KEY="$val"
}

show_current_config() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}  Aktuelle Konfiguration gefunden${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  ${BOLD}Domain:${NC}          ${CURRENT_DOMAIN:-<nicht gesetzt>}"
    echo -e "  ${BOLD}Email-Provider:${NC}  ${CURRENT_EMAIL_PROVIDER}"
    if [[ "$CURRENT_EMAIL_PROVIDER" == "resend" ]]; then
        if [[ -n "$CURRENT_RESEND_API_KEY" ]]; then
            echo -e "  ${BOLD}Resend API-Key:${NC}  ${CURRENT_RESEND_API_KEY:0:8}..."
        else
            echo -e "  ${BOLD}Resend API-Key:${NC}  <nicht gesetzt>"
        fi
    else
        echo -e "  ${BOLD}SMTP-Host:${NC}       ${CURRENT_SMTP_HOST:-<nicht gesetzt>}"
        echo -e "  ${BOLD}SMTP-Port:${NC}       ${CURRENT_SMTP_PORT:-587}"
        echo -e "  ${BOLD}SMTP-User:${NC}       ${CURRENT_SMTP_USER:-<nicht gesetzt>}"
        if [[ -n "$CURRENT_SMTP_PASS" ]]; then
            echo -e "  ${BOLD}SMTP-Passwort:${NC}   *** konfiguriert ***"
        else
            echo -e "  ${BOLD}SMTP-Passwort:${NC}   <nicht gesetzt>"
        fi
    fi
    echo -e "  ${BOLD}SMTP-From:${NC}       ${CURRENT_SMTP_FROM:-<nicht gesetzt>}"
    if $CURRENT_USE_POSTGRES; then
        echo -e "  ${BOLD}Datenbank:${NC}       PostgreSQL"
    else
        echo -e "  ${BOLD}Datenbank:${NC}       SQLite"
    fi
    if [[ -n "$CURRENT_REDIS_URL" ]]; then
        echo -e "  ${BOLD}Redis:${NC}           Aktiviert"
    else
        echo -e "  ${BOLD}Redis:${NC}           Deaktiviert"
    fi
    if [[ "$CURRENT_AUTO_UPDATE" == "true" ]]; then
        echo -e "  ${BOLD}Auto-Update:${NC}     Aktiviert"
    else
        echo -e "  ${BOLD}Auto-Update:${NC}     Deaktiviert"
    fi
    echo ""
}

# ═══════════════════════════════════════════════════════════════
# 0. Prüfungen
# ═══════════════════════════════════════════════════════════════
if [[ $EUID -ne 0 ]]; then
    err "Bitte als root ausführen: sudo bash install.sh"
fi

if ! grep -qEi 'ubuntu|debian' /etc/os-release 2>/dev/null; then
    warn "Nicht Ubuntu/Debian erkannt. Fortfahren auf eigene Gefahr."
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -d "$SCRIPT_DIR/backend" || ! -d "$SCRIPT_DIR/frontend" ]]; then
    err "Backend/Frontend nicht gefunden. Bitte aus dem Repository-Root ausführen."
fi

# Prüfe ob systemd aktiv und verfügbar ist (einmalig, damit die Prüfung nicht wiederholt werden muss)
if [[ -d /run/systemd/system ]] && command -v systemctl &>/dev/null; then
    SYSTEMD_AVAILABLE=true
else
    SYSTEMD_AVAILABLE=false
fi

ensure_subid_entry() {
    local file="$1"
    local user="$2"
    local count="65536"
    touch "$file"
    chmod 644 "$file"
    if grep -qE "^${user}:" "$file"; then
        return 0
    fi
    local start
    start=$(awk -F: '
        BEGIN { max = 99999 }
        NF >= 3 {
            end = $2 + $3 - 1
            if (end > max) max = end
        }
        END { print max + 1 }
    ' "$file")
    echo "${user}:${start}:${count}" >> "$file"
}

stop_legacy_rootful_msm_containers() {
    if ! $REINSTALL_MODE || ! command -v docker &>/dev/null; then
        return 0
    fi
    local containers
    containers=$(env -u DOCKER_HOST docker ps --format '{{.Names}}' 2>/dev/null | awk '/^msm-srv-/ {print}' || true)
    if [[ -z "$containers" ]]; then
        return 0
    fi
    warn "Rootless-Docker-Migration: Stoppe bestehende rootful MSM-Container. Sie werden nicht gelöscht."
    while read -r container; do
        [[ -z "$container" ]] && continue
        env -u DOCKER_HOST docker stop "$container" 2>&1 | tee -a "$LOG_FILE" || true
    done <<< "$containers"
    warn "Migration aktiv: MSM verwaltet Container ab jetzt über Rootless Docker. Bestehende rootful Container bleiben als Altbestand erhalten."
}

setup_rootless_docker() {
    MSM_UID=$(id -u "$MSM_USER")
    MSM_DOCKER_HOST="unix:///run/user/${MSM_UID}/docker.sock"

    ensure_subid_entry /etc/subuid "$MSM_USER"
    ensure_subid_entry /etc/subgid "$MSM_USER"

    if id -nG "$MSM_USER" 2>/dev/null | grep -qw docker; then
        log "Entferne $MSM_USER aus der globalen docker-Gruppe..."
        gpasswd -d "$MSM_USER" docker 2>&1 | tee -a "$LOG_FILE" || true
    fi

    stop_legacy_rootful_msm_containers

    if ! command -v dockerd-rootless-setuptool.sh &>/dev/null; then
        err "dockerd-rootless-setuptool.sh nicht gefunden. Docker Engine wurde nicht korrekt installiert."
    fi

    if $SYSTEMD_AVAILABLE; then
        loginctl enable-linger "$MSM_USER" 2>&1 | tee -a "$LOG_FILE" || true
        # Erzwinge den Start des Systemd-User-Managers für den msm-User,
        # damit systemctl --user innerhalb von su - msm funktioniert.
        systemctl start "user@${MSM_UID}.service" 2>&1 | tee -a "$LOG_FILE" || true
        install -d -o "$MSM_USER" -g "$MSM_USER" -m 700 "/run/user/${MSM_UID}" 2>/dev/null || true
        if [[ -f "$MSM_DIR/.config/systemd/user/docker.service" ]]; then
            log "Rootless-Docker-User-Service existiert bereits."
        else
            log "Richte Rootless Docker für $MSM_USER ein..."
            su - "$MSM_USER" -c "export XDG_RUNTIME_DIR=/run/user/${MSM_UID}; dockerd-rootless-setuptool.sh install" 2>&1 | tee -a "$LOG_FILE" || \
                err "Rootless-Docker-Setup fehlgeschlagen"
        fi
        log "Konfiguriere slirp4netns für Source-IP Erhalt bei UDP..."
        su - "$MSM_USER" -c "mkdir -p ~/.config/systemd/user/docker.service.d && echo -e '[Service]\nEnvironment=\"DOCKERD_ROOTLESS_ROOTLESSKIT_PORT_DRIVER=slirp4netns\"' > ~/.config/systemd/user/docker.service.d/override.conf" 2>&1 | tee -a "$LOG_FILE" || true
        su - "$MSM_USER" -c "export XDG_RUNTIME_DIR=/run/user/${MSM_UID}; systemctl --user daemon-reload" 2>&1 | tee -a "$LOG_FILE" || true
        su - "$MSM_USER" -c "export XDG_RUNTIME_DIR=/run/user/${MSM_UID}; systemctl --user enable docker.service && systemctl --user start docker.service" 2>&1 | tee -a "$LOG_FILE" || \
            err "Rootless-Docker-User-Service konnte nicht gestartet werden"
        log "Lade SteamCMD-Container-Image über Rootless Docker vor (cm2network/steamcmd:root)..."
        su - "$MSM_USER" -c "export DOCKER_HOST='${MSM_DOCKER_HOST}'; docker pull cm2network/steamcmd:root" 2>&1 | tee -a "$LOG_FILE" || \
            warn "Konnte SteamCMD-Image nicht vorziehen — wird beim ersten Server-Install nachgeholt."
    else
        warn "systemd nicht verfügbar. Rootless Docker wird vorbereitet, aber der User-Service kann nicht aktiviert werden."
    fi

    ok "Rootless Docker bereit (${MSM_DOCKER_HOST})"
}

log "=== Maunting Server Manager Installation ==="
log "Log: $LOG_FILE"
log ""

# ═══════════════════════════════════════════════════════════════
# 0b. Existierende Installation erkennen & laden
# ═══════════════════════════════════════════════════════════════
REINSTALL_MODE=false
KEEP_SETTINGS=false
CHANGED_DOMAIN=false
CHANGED_EMAIL=false
CHANGED_DB=false
CHANGED_REDIS=false
CHANGED_AUTO_UPDATE=false
NEED_FULL_REBUILD=false
CODE_CHANGED=false

if [[ "$SCRIPT_DIR" != "$MSM_DIR" ]]; then
    CODE_CHANGED=true
fi

if [[ -f "$MSM_DIR/backend/.env" ]]; then
    REINSTALL_MODE=true
    load_current_env
    show_current_config

    echo -e "${BOLD}[?]${NC} Einstellungen beibehalten oder ändern?"
    echo "    1) Beibehalten — nur Code aktualisieren, Frontend neu bauen, Services neustarten"
    echo "    2) Ändern — Konfiguration anpassen (Domain, Email, DB, Redis, Auto-Update)"
    ask "[1/2]: " choice

    if [[ "$choice" == "1" ]]; then
        KEEP_SETTINGS=true
        NEED_FULL_REBUILD=true
        log "Re-Install Modus: Einstellungen beibehalten, Code aktualisieren..."
    else
        KEEP_SETTINGS=false
        log "Re-Install Modus: Konfiguration wird angepasst..."
    fi
else
    log "Frische Installation erkannt..."
fi

# ═══════════════════════════════════════════════════════════════
# 1. System-Abhängigkeiten
# ═══════════════════════════════════════════════════════════════
log "Aktualisiere Paketlisten..."
apt-get update -qq | tee -a "$LOG_FILE"

log "Installiere Basis-Pakete..."
apt-get install -y -qq \
    curl wget git jq \
    python3 python3-pip python3-venv \
    sqlite3 \
    systemd systemd-sysv \
    libc6-i386 lib32stdc++6 lib32gcc-s1 \
    software-properties-common \
    debian-archive-keyring apt-transport-https \
    2>&1 | tee -a "$LOG_FILE"

# ── Node.js 20 (nicht das veraltete aus apt) ──
if ! command -v node &>/dev/null || [[ "$(node -v | cut -d'v' -f2 | cut -d'.' -f1)" -lt 20 ]]; then
    log "Installiere Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - 2>&1 | tee -a "$LOG_FILE"
    apt-get install -y -qq nodejs 2>&1 | tee -a "$LOG_FILE"
fi

# ── Caddy (offizielles Repo für aktuelle Version) ──
# Nur installieren, wenn Caddy noch nicht vorhanden ist.
# Bestehende Caddyfile wird niemals überschrieben.
if ! command -v caddy &>/dev/null; then
    log "Installiere Caddy (erstmalige Installation)..."
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https 2>&1 | tee -a "$LOG_FILE"
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' 2>/dev/null | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>&1 | tee -a "$LOG_FILE"
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' 2>/dev/null | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -qq | tee -a "$LOG_FILE"
    apt-get install -y -qq caddy 2>&1 | tee -a "$LOG_FILE"
else
    if [[ -f /etc/caddy/Caddyfile ]]; then
        ok "Caddy bereits vorhanden — bestehende Caddyfile wird erhalten."
    else
        # Caddy ist installiert, aber keine Caddyfile → Minimal-Datei anlegen
        log "Erstelle minimale Caddyfile (bestehende fehlt)..."
        mkdir -p /etc/caddy
        cat > /etc/caddy/Caddyfile <<'CADDYEOF'
import /etc/caddy/conf.d/*.conf
CADDYEOF
        ok "Minimal-Caddyfile angelegt."
    fi
fi

# ── Docker (Game-Server-Runtime — Rootless) ──
# Docker Engine/CLI bleiben Systempakete. MSM nutzt danach ausschließlich den
# Rootless-Daemon des msm-Users, nie /var/run/docker.sock.
apt-get install -y -qq uidmap dbus-user-session 2>&1 | tee -a "$LOG_FILE"
if ! command -v docker &>/dev/null; then
    log "Installiere Docker (offizieller Installer von docker.com)..."
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
    sh /tmp/get-docker.sh 2>&1 | tee -a "$LOG_FILE"
    rm -f /tmp/get-docker.sh
    ok "Docker installiert"
else
    log "Docker bereits installiert ($(docker --version 2>/dev/null || echo unbekannt))"
fi

ok "System-Abhängigkeiten installiert"

# ═══════════════════════════════════════════════════════════════
# 2. Redis (optional, empfohlen für Rate-Limiting)
# ═══════════════════════════════════════════════════════════════
INSTALL_REDIS=false
MSM_REDIS_URL=""

if $REINSTALL_MODE && $KEEP_SETTINGS; then
    # Keep mode: aktuellen Redis-Status beibehalten
    if [[ -n "$CURRENT_REDIS_URL" ]]; then
        INSTALL_REDIS=true
        MSM_REDIS_URL="$CURRENT_REDIS_URL"
        log "Redis bleibt aktiviert..."
    else
        INSTALL_REDIS=false
        MSM_REDIS_URL=""
        log "Redis bleibt deaktiviert..."
    fi
elif $REINSTALL_MODE && ! $KEEP_SETTINGS; then
    # Change mode: frage Toggle
    if [[ -n "$CURRENT_REDIS_URL" ]]; then
        echo ""
        echo -e "${BOLD}Redis ist aktuell aktiviert.${NC}"
        if ask_yesno "Redis deaktivieren?"; then
            INSTALL_REDIS=false
            MSM_REDIS_URL=""
            CHANGED_REDIS=true
            if $SYSTEMD_AVAILABLE; then
                systemctl stop redis-server 2>/dev/null || true
            else
                service redis-server stop 2>/dev/null || true
            fi
        else
            INSTALL_REDIS=true
            MSM_REDIS_URL="$CURRENT_REDIS_URL"
        fi
    else
        echo ""
        echo -e "${BOLD}Redis ist aktuell deaktiviert.${NC}"
        if ask_yesno "Redis für verteiltes Rate-Limiting aktivieren?"; then
            INSTALL_REDIS=true
            MSM_REDIS_URL="redis://localhost:6379"
            CHANGED_REDIS=true
        else
            INSTALL_REDIS=false
            MSM_REDIS_URL=""
        fi
    fi
else
    # Fresh install
    if ask_yesno "Redis für verteiltes Rate-Limiting installieren? (empfohlen für Produktion)"; then
        INSTALL_REDIS=true
        MSM_REDIS_URL="redis://localhost:6379"
    else
        INSTALL_REDIS=false
        MSM_REDIS_URL=""
    fi
fi

# Redis installieren/Starten wenn nötig
if $INSTALL_REDIS; then
    if ! command -v redis-server &>/dev/null; then
        log "Installiere Redis..."
        apt-get install -y -qq redis-server 2>&1 | tee -a "$LOG_FILE"
    fi
    if $SYSTEMD_AVAILABLE; then
        systemctl enable redis-server >/dev/null 2>&1 || true
        systemctl start redis-server >/dev/null 2>&1 || true
    else
        service redis-server start 2>/dev/null || true
    fi
    if ! $REINSTALL_MODE || ($REINSTALL_MODE && ! $KEEP_SETTINGS && $CHANGED_REDIS); then
        ok "Redis installiert und gestartet"
    fi
else
    if ! $REINSTALL_MODE; then
        log "Redis wird übersprungen (Rate-Limiting nutzt in-memory Fallback)."
    fi
fi

# ═══════════════════════════════════════════════════════════════
# 3. MSM System-User
# ═══════════════════════════════════════════════════════════════
if ! id "$MSM_USER" &>/dev/null; then
    log "Erstelle System-User '$MSM_USER'..."
    useradd -r -m -s /bin/bash -d "$MSM_DIR" "$MSM_USER"
fi
ok "User '$MSM_USER' bereit"

# ── Servers- und Backups-Verzeichnis anlegen ──
mkdir -p /opt/msm/servers
chown "$MSM_USER:$MSM_USER" /opt/msm/servers
mkdir -p /opt/msm/backups
chown "$MSM_USER:$MSM_USER" /opt/msm/backups

setup_rootless_docker

# ═══════════════════════════════════════════════════════════════
# 4. Dateien kopieren
# ═══════════════════════════════════════════════════════════════
SHOULD_COPY_FILES=false
if ! $REINSTALL_MODE; then
    SHOULD_COPY_FILES=true
elif $KEEP_SETTINGS; then
    SHOULD_COPY_FILES=true
elif $REINSTALL_MODE && ! $KEEP_SETTINGS && $CODE_CHANGED; then
    SHOULD_COPY_FILES=true
fi

if $SHOULD_COPY_FILES; then
    log "Kopiere Panel-Dateien nach $MSM_DIR..."
    if [[ "$SCRIPT_DIR" == "$MSM_DIR" ]]; then
        # Install.sh läuft direkt im Zielverzeichnis → nicht löschen, nicht kopieren!
        # Nur alte Build-Artefakte und Konfigurationen aufräumen
        rm -rf "$MSM_DIR/frontend/dist" 2>/dev/null || true
        rm -rf "$MSM_DIR/frontend/node_modules" 2>/dev/null || true
        rm -rf "$MSM_DIR/backend/venv" 2>/dev/null || true
    else
        # Install.sh läuft außerhalb des Zielverzeichnisses → sauberes Verzeichnis anlegen
        rm -rf "$MSM_DIR" 2>/dev/null || true
        mkdir -p "$MSM_DIR"

        cp -r "$SCRIPT_DIR/backend" "$MSM_DIR/"
        cp -r "$SCRIPT_DIR/frontend" "$MSM_DIR/"
        cp -r "$SCRIPT_DIR/docs" "$MSM_DIR/" 2>/dev/null || true
        cp "$SCRIPT_DIR/Caddyfile.template" "$MSM_DIR/" 2>/dev/null || true
        cp "$SCRIPT_DIR/msm.service.template" "$MSM_DIR/" 2>/dev/null || true
        cp "$SCRIPT_DIR/update.sh" "$MSM_DIR/" 2>/dev/null || true
        chmod +x "$MSM_DIR/update.sh" 2>/dev/null || true
    fi
    chown -R "$MSM_USER:$MSM_USER" "$MSM_DIR"
    ok "Dateien bereit"
elif $REINSTALL_MODE && ! $KEEP_SETTINGS && ! $CODE_CHANGED; then
    log "Quellverzeichnis identisch mit Ziel — keine Datei-Kopie nötig."
fi

# ═══════════════════════════════════════════════════════════════
# 5. Interaktive Konfiguration
# ═══════════════════════════════════════════════════════════════

# Defaults
DOMAIN=""
EMAIL_PROVIDER="smtp"
SMTP_HOST=""
SMTP_PORT=587
SMTP_USER=""
SMTP_PASS=""
SMTP_FROM=""
RESEND_API_KEY=""
USE_POSTGRES=false
PG_PASSWORD=""
MSM_AUTO_UPDATE="false"

if $REINSTALL_MODE && $KEEP_SETTINGS; then
    # Keep mode: alle aktuellen Werte übernehmen
    DOMAIN="$CURRENT_DOMAIN"
    EMAIL_PROVIDER="$CURRENT_EMAIL_PROVIDER"
    SMTP_HOST="$CURRENT_SMTP_HOST"
    SMTP_PORT="${CURRENT_SMTP_PORT:-587}"
    SMTP_USER="$CURRENT_SMTP_USER"
    SMTP_PASS="$CURRENT_SMTP_PASS"
    SMTP_FROM="$CURRENT_SMTP_FROM"
    RESEND_API_KEY="$CURRENT_RESEND_API_KEY"
    if $CURRENT_USE_POSTGRES; then
        USE_POSTGRES=true
    else
        USE_POSTGRES=false
    fi
    MSM_AUTO_UPDATE="$CURRENT_AUTO_UPDATE"

elif $REINSTALL_MODE && ! $KEEP_SETTINGS; then
    # ═══════════════════════════════════════════════════════════════
    # Change mode: 4 Fragen mit aktuellen Werten als Default
    # ═══════════════════════════════════════════════════════════════

    # ── 1/4 Domain ──
    echo ""
    echo -e "${BOLD}Schritt 1/4: Domain${NC}"
    echo "  Gib eine Domain an, damit Caddy automatisch ein SSL-Zertifikat erstellt."
    echo "  Ohne Domain wird das Panel über HTTP (nur IP) erreichbar — nicht empfohlen."
    echo "  Leer lassen um aktuellen Wert zu behalten: ${CURRENT_DOMAIN:-<nicht gesetzt>}"
    echo ""
    ask "Domain [${CURRENT_DOMAIN:-}]: " DOMAIN_INPUT
    DOMAIN="${DOMAIN_INPUT:-$CURRENT_DOMAIN}"
    if [[ "$DOMAIN" != "$CURRENT_DOMAIN" ]]; then
        CHANGED_DOMAIN=true
    fi

    # ── 2/4 Email ──
    echo ""
    echo -e "${BOLD}Schritt 2/4: Email-Versand${NC}"
    echo "  Aktueller Provider: ${CURRENT_EMAIL_PROVIDER}"
    echo "  'Ändern' wählen um Provider oder Details zu ändern."
    echo ""

    EMAIL_PROVIDER="$CURRENT_EMAIL_PROVIDER"
    SMTP_HOST="$CURRENT_SMTP_HOST"
    SMTP_PORT="${CURRENT_SMTP_PORT:-587}"
    SMTP_USER="$CURRENT_SMTP_USER"
    SMTP_PASS="$CURRENT_SMTP_PASS"
    SMTP_FROM="${CURRENT_SMTP_FROM:-noreply@mauntingstudios.de}"
    RESEND_API_KEY="$CURRENT_RESEND_API_KEY"

    if ask_yesno "Email-Einstellungen ändern?"; then
        echo ""
        echo -e "  ${BOLD}Provider wählen:${NC}"
        echo "    1) Resend (API-Key, einfach)"
        echo "    2) SMTP (Server-Daten)"
        ask "[1/2]: " EMAIL_CHOICE

        if [[ "$EMAIL_CHOICE" == "1" ]]; then
            EMAIL_PROVIDER="resend"
            ask "Resend API-Key [${CURRENT_RESEND_API_KEY:-re_...}]: " RESEND_API_KEY
            RESEND_API_KEY="${RESEND_API_KEY:-$CURRENT_RESEND_API_KEY}"
            ask "Absender-Adresse [${CURRENT_SMTP_FROM:-noreply@mauntingstudios.de}]: " SMTP_FROM_INPUT
            SMTP_FROM="${SMTP_FROM_INPUT:-${CURRENT_SMTP_FROM:-noreply@mauntingstudios.de}}"
            ok "Resend konfiguriert"
        else
            EMAIL_PROVIDER="smtp"
            ask "SMTP-Host [${CURRENT_SMTP_HOST:-smtp.strato.de}]: " SMTP_HOST
            SMTP_HOST="${SMTP_HOST:-$CURRENT_SMTP_HOST}"
            ask "SMTP-Port [${CURRENT_SMTP_PORT:-587}]: " SMTP_PORT_INPUT
            SMTP_PORT="${SMTP_PORT_INPUT:-${CURRENT_SMTP_PORT:-587}}"
            ask "SMTP-Benutzername [${CURRENT_SMTP_USER:-}]: " SMTP_USER
            SMTP_USER="${SMTP_USER:-$CURRENT_SMTP_USER}"
            ask "SMTP-Passwort [leer = bestehendes behalten]: " SMTP_PASS
            if [[ -z "$SMTP_PASS" ]]; then
                SMTP_PASS="$CURRENT_SMTP_PASS"
            fi
            ask "Absender-Adresse [${CURRENT_SMTP_FROM:-noreply@mauntingstudios.de}]: " SMTP_FROM_INPUT
            SMTP_FROM="${SMTP_FROM_INPUT:-${CURRENT_SMTP_FROM:-noreply@mauntingstudios.de}}"
            ok "SMTP konfiguriert"
        fi

        # Prüfe ob Email wirklich geändert wurde
        if [[ "$EMAIL_PROVIDER" != "$CURRENT_EMAIL_PROVIDER" ]]; then
            CHANGED_EMAIL=true
        elif [[ "$EMAIL_PROVIDER" == "resend" && "$RESEND_API_KEY" != "$CURRENT_RESEND_API_KEY" ]]; then
            CHANGED_EMAIL=true
        elif [[ "$EMAIL_PROVIDER" == "smtp" && ( "$SMTP_HOST" != "$CURRENT_SMTP_HOST" || "$SMTP_PORT" != "$CURRENT_SMTP_PORT" || "$SMTP_USER" != "$CURRENT_SMTP_USER" || "$SMTP_PASS" != "$CURRENT_SMTP_PASS" || "$SMTP_FROM" != "$CURRENT_SMTP_FROM" ) ]]; then
            CHANGED_EMAIL=true
        fi
    else
        log "Email-Einstellungen bleiben unverändert."
    fi

    # ── 3/4 PostgreSQL ──
    echo ""
    echo -e "${BOLD}Schritt 3/4: Datenbank${NC}"
    if $CURRENT_USE_POSTGRES; then
        echo "  Aktuell: PostgreSQL"
    else
        echo "  Aktuell: SQLite"
    fi
    echo "  'Ja' wählen um zu wechseln (mit Warnung)."
    echo ""

    USE_POSTGRES=$CURRENT_USE_POSTGRES

    if ask_yesno "Datenbank-Typ wechseln?"; then
        if $CURRENT_USE_POSTGRES; then
            warn "WARNUNG: Wechsel von PostgreSQL zu SQLite!"
            warn "Existierende PostgreSQL-Daten werden NICHT automatisch migriert."
            if ask_yesno "Wirklich zu SQLite wechseln?"; then
                USE_POSTGRES=false
                CHANGED_DB=true
            fi
        else
            warn "WARNUNG: Wechsel von SQLite zu PostgreSQL!"
            warn "Existierende SQLite-Daten werden NICHT automatisch migriert."
            if ask_yesno "Wirklich zu PostgreSQL wechseln?"; then
                USE_POSTGRES=true
                CHANGED_DB=true
            fi
        fi
    fi

    # ── 4/4 Auto-Update ──
    echo ""
    echo -e "${BOLD}Schritt 4/4: Automatische Updates${NC}"
    if [[ "$CURRENT_AUTO_UPDATE" == "true" ]]; then
        echo "  Aktuell: Aktiviert"
        if ask_yesno_default "Automatische Updates beibehalten?" "Y"; then
            MSM_AUTO_UPDATE="true"
        else
            MSM_AUTO_UPDATE="false"
            CHANGED_AUTO_UPDATE=true
        fi
    else
        echo "  Aktuell: Deaktiviert"
        if ask_yesno_default "Automatische Updates aktivieren?" "N"; then
            MSM_AUTO_UPDATE="true"
            CHANGED_AUTO_UPDATE=true
        else
            MSM_AUTO_UPDATE="false"
        fi
    fi

    # Bestimme ob Full Rebuild nötig
    if $CODE_CHANGED || $CHANGED_DB; then
        NEED_FULL_REBUILD=true
    fi

else
    # ═══════════════════════════════════════════════════════════════
    # Fresh install flow (Original)
    # ═══════════════════════════════════════════════════════════════

    # ── 5a. Domain ──
    echo ""
    echo -e "${BOLD}Schritt 1/4: Domain${NC}"
    echo "  Gib eine Domain an, damit Caddy automatisch ein SSL-Zertifikat erstellt."
    echo "  Ohne Domain wird das Panel über HTTP (nur IP) erreichbar — nicht empfohlen."
    echo ""
    ask "Domain (z.B. panel.deinserver.de) oder leer lassen für IP-Modus: " DOMAIN
    DOMAIN="${DOMAIN:-}"

    # ── 5b. Email ──
    echo ""
    echo -e "${BOLD}Schritt 2/4: Email-Versand${NC}"
    echo "  Wird für Setup-Verifikation, 2FA-Setup und Backup-Codes benötigt."
    echo "  Optionen:"
    echo "    1. Resend (resend.com) — API-Key, kein SMTP nötig, empfohlen"
    echo "    2. SMTP (eigener Server, Strato, Gmail, etc.)"
    echo "  Du kannst das später in /opt/msm/backend/.env nachholen."
    echo ""

    if ask_yesno "Email jetzt konfigurieren?"; then
        echo ""
        echo -e "  ${BOLD}Provider wählen:${NC}"
        echo "    1) Resend (API-Key, einfach)"
        echo "    2) SMTP (Server-Daten)"
        ask "[1/2]: " EMAIL_CHOICE

        if [[ "$EMAIL_CHOICE" == "1" ]]; then
            EMAIL_PROVIDER="resend"
            ask "Resend API-Key (re_...): " RESEND_API_KEY
            ask "Absender-Adresse [noreply@mauntingstudios.de]: " SMTP_FROM_INPUT
            SMTP_FROM="${SMTP_FROM_INPUT:-noreply@mauntingstudios.de}"
            ok "Resend konfiguriert"
        else
            EMAIL_PROVIDER="smtp"
            ask "SMTP-Host (z.B. smtp.strato.de): " SMTP_HOST
            ask "SMTP-Port [587]: " SMTP_PORT_INPUT
            SMTP_PORT="${SMTP_PORT_INPUT:-587}"
            ask "SMTP-Benutzername: " SMTP_USER
            ask "SMTP-Passwort: " SMTP_PASS
            ask "Absender-Adresse [noreply@mauntingstudios.de]: " SMTP_FROM_INPUT
            SMTP_FROM="${SMTP_FROM_INPUT:-noreply@mauntingstudios.de}"
            ok "SMTP konfiguriert"
        fi
    else
        warn "Email übersprungen. Setup-Verifikation und 2FA stehen erst nach Konfiguration zur Verfügung."
    fi

    # ── 5c. PostgreSQL ──
    echo ""
    echo -e "${BOLD}Schritt 3/4: Datenbank${NC}"
    if ask_yesno "PostgreSQL für die Datenbank nutzen? (empfohlen für Produktion, sonst SQLite)"; then
        USE_POSTGRES=true
    else
        USE_POSTGRES=false
    fi

    # ── 5d. Auto-Update ──
    echo ""
    echo -e "${BOLD}Schritt 4/4: Automatische Updates${NC}"
    echo "  Prüft täglich auf neue GitHub-Releases und installiert sie automatisch."
    echo "  Empfohlen für Produktion, aber Updates sollten vorher getestet werden."
    echo ""
    if ask_yesno "Automatische Updates aktivieren?"; then
        MSM_AUTO_UPDATE="true"
    else
        MSM_AUTO_UPDATE="false"
    fi
fi

# ═══════════════════════════════════════════════════════════════
# 5b. PostgreSQL Setup (falls nötig)
# ═══════════════════════════════════════════════════════════════
if $USE_POSTGRES; then
    if ! command -v psql &>/dev/null; then
        log "Installiere PostgreSQL..."
        apt-get install -y -qq postgresql postgresql-contrib libpq-dev python3-dev 2>&1 | tee -a "$LOG_FILE"
    fi

    # Nur bei frischer Installation oder Wechsel zu PostgreSQL: Passwort + User/DB erstellen
    if ! $REINSTALL_MODE || $CHANGED_DB; then
        PG_PASSWORD=$(python3 -c "import secrets, string; a=string.ascii_letters+string.digits+'_-'; print(''.join(secrets.choice(a) for _ in range(32)))")

        log "Richte PostgreSQL-User und Datenbank ein..."
        cat > /tmp/msm_pg_setup.sql <<EOF
DO \$\$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'msm') THEN
    ALTER USER msm WITH PASSWORD '${PG_PASSWORD}';
  ELSE
    CREATE USER msm WITH PASSWORD '${PG_PASSWORD}';
  END IF;
END \$\$;
EOF
        su - postgres -c "psql -f /tmp/msm_pg_setup.sql" 2>&1 | tee -a "$LOG_FILE"
        rm -f /tmp/msm_pg_setup.sql

        # CREATE DATABASE darf NICHT in einem DO/Transaktions-Block laufen
        su - postgres -c "psql -c \"CREATE DATABASE msm OWNER msm;\"" 2>&1 | tee -a "$LOG_FILE" || true

        su - postgres -c "psql -d msm -c \"GRANT ALL ON SCHEMA public TO msm;\"" 2>&1 | tee -a "$LOG_FILE" || true

        ok "PostgreSQL eingerichtet (DB: msm, User: msm)"
    else
        log "PostgreSQL bleibt bestehend — User/DB nicht neu angelegt."
    fi

    # pg_hba.conf sicherstellen (auch bei Re-Install)
    PG_HBA=$(find /etc/postgresql -name pg_hba.conf | head -1)
    if [[ -n "$PG_HBA" ]]; then
        sed -i -E 's/^(host\s+all\s+all\s+127\.0\.0\.1\/32)\s+.*/\1            scram-sha-256/' "$PG_HBA"
        sed -i -E 's/^(host\s+all\s+all\s+::1\/128)\s+.*/\1                 scram-sha-256/' "$PG_HBA"
        if ! grep -qE '^host\s+all\s+all\s+127\.0\.0\.1/32' "$PG_HBA"; then
            echo "host    all             all             127.0.0.1/32            scram-sha-256" >> "$PG_HBA"
        fi
        if ! grep -qE '^host\s+all\s+all\s+::1/128' "$PG_HBA"; then
            echo "host    all             all             ::1/128                 scram-sha-256" >> "$PG_HBA"
        fi
        if $SYSTEMD_AVAILABLE; then
            systemctl restart postgresql
        else
            service postgresql restart 2>/dev/null || pg_ctlcluster $(pg_lsclusters | tail -1 | awk '{print $1}') main restart 2>/dev/null || true
        fi
    fi

    if ! $REINSTALL_MODE || $CHANGED_DB; then
        ok "PostgreSQL installiert (DB: msm, User: msm)"
    fi
else
    if ! $REINSTALL_MODE; then
        log "SQLite wird als Datenbank genutzt (einfach, aber nicht für hohe Last)."
    fi
fi

# ═══════════════════════════════════════════════════════════════
# 6. SECRET_KEY generieren & .env schreiben
# ═══════════════════════════════════════════════════════════════
log "Generiere kryptographischen Secret-Key..."
if $REINSTALL_MODE && [[ -n "$CURRENT_SECRET_KEY" ]]; then
    SECRET_KEY="$CURRENT_SECRET_KEY"
    log "Bestehender SECRET_KEY wird beibehalten."
else
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
fi

PANEL_URL="http://localhost"
if [[ -n "$DOMAIN" ]]; then
    PANEL_URL="https://$DOMAIN"
fi

ENV_FILE="$MSM_DIR/backend/.env"

# Datenbank-URL bestimmen
if ! $REINSTALL_MODE || $CHANGED_DB; then
    # Frische URL generieren
    if $USE_POSTGRES; then
        PG_PASSWORD_ENCODED=$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$PG_PASSWORD''', safe=''))")
        DB_URL="postgresql+psycopg2://msm:${PG_PASSWORD_ENCODED}@localhost:5432/msm"
        DB_URL_ASYNC="postgresql+asyncpg://msm:${PG_PASSWORD_ENCODED}@localhost:5432/msm"
    else
        DB_URL="sqlite:///./msm.db"
        DB_URL_ASYNC="sqlite+aiosqlite:///./msm.db"
    fi
else
    # Bestehende URLs beibehalten
    if [[ -n "${CURRENT_DB_URL:-}" && -n "${CURRENT_DB_URL_ASYNC:-}" ]]; then
        DB_URL="$CURRENT_DB_URL"
        DB_URL_ASYNC="$CURRENT_DB_URL_ASYNC"
    else
        # Fallback (sollte bei gültigem .env nie passieren)
        if $USE_POSTGRES; then
            DB_URL="postgresql+psycopg2://msm:@localhost:5432/msm"
            DB_URL_ASYNC="postgresql+asyncpg://msm:@localhost:5432/msm"
        else
            DB_URL="sqlite:///./msm.db"
            DB_URL_ASYNC="sqlite+aiosqlite:///./msm.db"
        fi
        warn "Bestehende DB-URL nicht gefunden — Fallback generiert."
    fi
fi

cat > "$ENV_FILE" <<EOF
# Automatisch generiert durch install.sh am $(date -Iseconds)
# ÄNDERUNGEN NUR MIT VORSICHT

MSM_APP_NAME="Maunting Server Manager"
MSM_DEBUG=false
MSM_DATABASE_URL="$DB_URL"
MSM_DATABASE_URL_ASYNC="$DB_URL_ASYNC"
MSM_SECRET_KEY="$SECRET_KEY"
MSM_ALGORITHM="HS256"
MSM_ACCESS_TOKEN_EXPIRE_MINUTES=15
MSM_REFRESH_TOKEN_EXPIRE_DAYS=30
MSM_CSRF_TOKEN_EXPIRE_MINUTES=1440
MSM_EMAIL_PROVIDER="$EMAIL_PROVIDER"
MSM_SMTP_HOST="$SMTP_HOST"
MSM_SMTP_PORT=$SMTP_PORT
MSM_SMTP_USER="$SMTP_USER"
MSM_SMTP_PASSWORD="$SMTP_PASS"
MSM_SMTP_TLS=true
MSM_SMTP_FROM="${SMTP_FROM:-noreply@mauntingstudios.de}"
MSM_RESEND_API_KEY="$RESEND_API_KEY"
MSM_PANEL_URL="$PANEL_URL"
MSM_SETUP_COMPLETED_FILE="/opt/msm/.setup_completed"
MSM_DOCKER_HOST="$MSM_DOCKER_HOST"
MSM_STEAMCMD_PATH="/usr/games/steamcmd"
# Redis-URL Fallback (sicherstellen, dass sie nie leer ist wenn Redis aktiv sein soll)
if $INSTALL_REDIS && [[ -z "$MSM_REDIS_URL" ]]; then
    MSM_REDIS_URL="redis://localhost:6379"
fi
MSM_REDIS_URL="$MSM_REDIS_URL"

# Auto-Update (GitHub Releases)
MSM_GITHUB_OWNER="einmalmaik"
MSM_GITHUB_REPO="maunting-server-manager"
MSM_AUTO_UPDATE=$MSM_AUTO_UPDATE
MSM_AUTO_UPDATE_INTERVAL_HOURS=24
EOF

chmod 600 "$ENV_FILE"
chown "$MSM_USER:$MSM_USER" "$ENV_FILE"
ok ".env geschrieben (chmod 600)"

# ═══════════════════════════════════════════════════════════════
# 7. Python-Backend einrichten
# ═══════════════════════════════════════════════════════════════
RUN_BACKEND_SETUP=false
if ! $REINSTALL_MODE; then
    RUN_BACKEND_SETUP=true
elif $KEEP_SETTINGS; then
    RUN_BACKEND_SETUP=true
elif $REINSTALL_MODE && ! $KEEP_SETTINGS && ($CODE_CHANGED || $CHANGED_DB); then
    RUN_BACKEND_SETUP=true
fi

if $RUN_BACKEND_SETUP; then
    log "Installiere Python-Abhängigkeiten..."
    su - "$MSM_USER" -c "
        cd $MSM_DIR/backend
        python3 -m venv venv
        source venv/bin/activate
        pip install --upgrade pip -q
        pip install -r requirements.txt -q
    " 2>&1 | tee -a "$LOG_FILE"
    ok "Python-Backend bereit"
fi

# ═══════════════════════════════════════════════════════════════
# 8. Datenbank initialisieren
# ═══════════════════════════════════════════════════════════════
RUN_DB_INIT=false
if ! $REINSTALL_MODE; then
    RUN_DB_INIT=true
elif $REINSTALL_MODE && ! $KEEP_SETTINGS && $CHANGED_DB; then
    RUN_DB_INIT=true
fi

if $RUN_DB_INIT; then
    log "Initialisiere Datenbank..."

    # Bei SQLite: alte DB entfernen, damit create_all ein sauberes Schema erzeugt
    # (außer Re-Install mit unveränderter SQLite-DB)
    if [[ "$DB_URL" == sqlite* ]]; then
        SHOULD_DELETE_DB=true
        if $REINSTALL_MODE && ! $CHANGED_DB; then
            SHOULD_DELETE_DB=false
        fi
        if $SHOULD_DELETE_DB; then
            rm -f "$MSM_DIR/backend/msm.db"
        fi
    fi

    su - "$MSM_USER" -c "
        cd $MSM_DIR/backend
        source venv/bin/activate
        python3 -c \"from database import engine, Base; from models import *; Base.metadata.create_all(engine)\"
    " 2>&1 | tee -a "$LOG_FILE"
    ok "Datenbank initialisiert"
fi

# ═══════════════════════════════════════════════════════════════
# 9. Frontend bauen
# ═══════════════════════════════════════════════════════════════
RUN_FRONTEND_BUILD=false
if ! $REINSTALL_MODE; then
    RUN_FRONTEND_BUILD=true
elif $KEEP_SETTINGS; then
    RUN_FRONTEND_BUILD=true
elif $REINSTALL_MODE && ! $KEEP_SETTINGS && $CODE_CHANGED; then
    RUN_FRONTEND_BUILD=true
fi

if $RUN_FRONTEND_BUILD; then
    log "Baue Frontend..."
    if ! su - "$MSM_USER" -c "
        set -e
        cd $MSM_DIR/frontend
        npm install -q
        npm run build
    " 2>&1 | tee -a "$LOG_FILE"; then
        err "Frontend-Build fehlgeschlagen. Prüfe npm-Log und package.json."
    fi
    ok "Frontend gebaut"
fi

# ═══════════════════════════════════════════════════════════════
# 10. Caddy konfigurieren
# ═══════════════════════════════════════════════════════════════
RUN_CADDY_SETUP=false
if ! $REINSTALL_MODE; then
    RUN_CADDY_SETUP=true
elif $KEEP_SETTINGS; then
    RUN_CADDY_SETUP=true
elif $REINSTALL_MODE && ! $KEEP_SETTINGS && ($CODE_CHANGED || $CHANGED_DOMAIN); then
    RUN_CADDY_SETUP=true
fi

if $RUN_CADDY_SETUP; then
    log "Konfiguriere Caddy..."

    CADDY_CONFIG="/etc/caddy/Caddyfile"
    CADDY_CONFD="/etc/caddy/conf.d"
    mkdir -p /etc/caddy "$CADDY_CONFD"

    # ── Extension erkennen: .caddy oder .conf? ──
    # Prüfe ob existierende Caddyfile bereits conf.d importiert
    if grep -qE "^import\s+${CADDY_CONFD}/\*\.[a-z]+" "$CADDY_CONFIG" 2>/dev/null; then
        # Bestehende Extension auslesen (z.B. *.caddy → caddy, *.conf → conf)
        CADDY_EXT=$(grep -E "^import\s+${CADDY_CONFD}/\*\.[a-z]+" "$CADDY_CONFIG" | head -1 | sed -E 's/.*\*\.([a-z]+).*/\1/')
    else
        CADDY_EXT="conf"  # Default
    fi
    MSM_CADDY_FILE="$CADDY_CONFD/msm.$CADDY_EXT"

    # ═══════════════════════════════════════════════════════════════
    # Caddyfile: import-Zeile nur hinzufügen, wenn noch kein conf.d-Import existiert
    # ═══════════════════════════════════════════════════════════════
    if ! grep -qE "^import\s+${CADDY_CONFD}/\*\.[a-z]+" "$CADDY_CONFIG" 2>/dev/null; then
        if [[ ! -s "$CADDY_CONFIG" ]]; then
            cat > "$CADDY_CONFIG" <<EOF
# Caddyfile
# Weitere Sites können hier direkt oder unter $CADDY_CONFD/ konfiguriert werden.

import $CADDY_CONFD/*.${CADDY_EXT}
EOF
        else
            echo "" >> "$CADDY_CONFIG"
            echo "# MSM Panel — additional site configurations" >> "$CADDY_CONFIG"
            echo "import $CADDY_CONFD/*.${CADDY_EXT}" >> "$CADDY_CONFIG"
        fi
    fi

    # ═══════════════════════════════════════════════════════════════
    # Domain-Conflict-Check: scannt ALLE Dateien in conf.d
    # (egal ob .caddy, .conf, .txt — Caddy lädt alles via import *)
    # ═══════════════════════════════════════════════════════════════
    DOMAIN_CONFLICT=false
    if [[ -n "$DOMAIN" ]]; then
        for conf_file in "$CADDY_CONFD"/*; do
            [[ -f "$conf_file" ]] || continue
            # MSM-eigene Datei darf überschrieben werden
            [[ "$(basename "$conf_file")" == "msm.$CADDY_EXT" ]] && continue
            if grep -qE "(^|\s)${DOMAIN}(\s|\{)" "$conf_file" 2>/dev/null; then
                warn "Domain '$DOMAIN' wird bereits in $(basename "$conf_file") verwendet!"
                warn "Bitte eine andere Domain wählen oder die bestehende Config bereinigen."
                DOMAIN_CONFLICT=true
                break
            fi
        done
        # Auch die Haupt-Caddyfile prüfen (ohne import-/Kommentarzeilen)
        if ! $DOMAIN_CONFLICT && [[ -f "$CADDY_CONFIG" ]]; then
            if grep -vE '^\s*(import|#|$)' "$CADDY_CONFIG" | grep -qE "(^|\s)${DOMAIN}(\s|\{)" 2>/dev/null; then
                warn "Domain '$DOMAIN' wird bereits in der Haupt-Caddyfile verwendet!"
                warn "Bitte eine andere Domain wählen oder die bestehende Config bereinigen."
                DOMAIN_CONFLICT=true
            fi
        fi
    fi

    # MSM-Config nur schreiben, wenn kein Domain-Conflict besteht
    if $DOMAIN_CONFLICT; then
        warn "MSM-Caddy-Config wurde NICHT geschrieben (Domain-Konflikt)."
    elif [[ -n "$DOMAIN" ]]; then
        cat > "$MSM_CADDY_FILE" <<EOF
# MSM Panel — managed by install.sh
# Nicht manuell bearbeiten. Änderungen via install.sh vornehmen.
$DOMAIN {
    root * /opt/msm/frontend/dist

    encode gzip

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
        Permissions-Policy "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    }

    handle /api/* {
        reverse_proxy localhost:8000
    }

    handle /ws/* {
        reverse_proxy localhost:8000
    }

    handle {
        try_files {path} /index.html
        file_server
    }
}
EOF
    else
        cat > "$MSM_CADDY_FILE" <<EOF
# MSM Panel — managed by install.sh
# Nicht manuell bearbeiten. Änderungen via install.sh vornehmen.
# HINWEIS: :80 ist ein Catch-All. Falls andere :80-Sites existieren,
#          sollte MSM mit einer Domain konfiguriert werden.
:80 {
    root * /opt/msm/frontend/dist

    encode gzip

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
    }

    handle /api/* {
        reverse_proxy localhost:8000
    }

    handle /ws/* {
        reverse_proxy localhost:8000
    }

    handle {
        try_files {path} /index.html
        file_server
    }
}
EOF
        if ! $REINSTALL_MODE; then
            warn "Keine Domain angegeben — Panel läuft über HTTP (nicht empfohlen für Produktion)."
            warn "Falls andere :80-Sites in Caddy konfiguriert sind, konfiguriere MSM mit einer Domain."
        fi
    fi

    if ! $DOMAIN_CONFLICT; then
        if $SYSTEMD_AVAILABLE; then
            systemctl reload caddy 2>/dev/null || systemctl restart caddy 2>/dev/null || true
        else
            service caddy restart 2>/dev/null || caddy reload --config "$CADDY_CONFIG" 2>/dev/null || true
        fi
    else
        warn "Caddy wurde NICHT neugestartet (Domain-Konflikt). Bitte Konflikt lösen und install.sh erneut ausführen."
    fi
    ok "Caddy konfiguriert"
fi

# ═══════════════════════════════════════════════════════════════
# 11. systemd Service
# ═══════════════════════════════════════════════════════════════
RUN_SYSTEMD_SETUP=false
if ! $REINSTALL_MODE; then
    RUN_SYSTEMD_SETUP=true
elif $KEEP_SETTINGS; then
    RUN_SYSTEMD_SETUP=true
elif $REINSTALL_MODE && ! $KEEP_SETTINGS && ($CODE_CHANGED || $CHANGED_DOMAIN || $CHANGED_EMAIL || $CHANGED_REDIS); then
    RUN_SYSTEMD_SETUP=true
fi

if $RUN_SYSTEMD_SETUP; then
    log "Registriere systemd Service..."
    cat > /etc/systemd/system/msm-panel.service <<EOF
[Unit]
Description=Maunting Server Manager Panel
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=simple
User=msm
Group=msm
WorkingDirectory=/opt/msm/backend
# Systemd-Units erben kein PATH vom Login-Shell. venv zuerst, danach System-Pfade.
Environment="PATH=/opt/msm/backend/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="DOCKER_HOST=$MSM_DOCKER_HOST"
ExecStart=/opt/msm/backend/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

# Security Hardening
# NoNewPrivileges bleibt aus, weil UFW/iptables weiterhin ueber enge sudo-Gates
# laufen. Container-Lifecycle selbst benoetigt kein sudo mehr.
PrivateTmp=true
ProtectSystem=strict
ProtectHome=false
# /opt/msm existiert immer (Home des msm-Users) → kein NAMESPACE-Crash
# UFW-Pfade fuer firewall_service.py (Port-Manager). ProtectSystem=strict
# macht /run, /etc, /var/lib read-only im Namespace; ohne diese Pfade
# scheitert ``sudo ufw ...`` aus dem Backend. ``-``-Praefix => systemd
# ueberspringt nicht existierende Pfade (z.B. ``/run/ufw.lock`` vor dem
# ersten ufw-Aufruf) statt mit ``status=226/NAMESPACE`` zu crashen.
ReadWritePaths=/opt/msm -/etc/ufw -/var/lib/ufw -/run/ufw -/run/ufw.lock -/run/user

[Install]
WantedBy=multi-user.target
EOF

    if $SYSTEMD_AVAILABLE; then
        systemctl daemon-reload
        systemctl enable msm-panel.service

        # Update-Timer (optional — deaktiviert per Default)
        cp "$SCRIPT_DIR/msm-update.service" /etc/systemd/system/msm-update.service 2>/dev/null || true
        cp "$SCRIPT_DIR/msm-update.timer" /etc/systemd/system/msm-update.timer 2>/dev/null || true
        systemctl daemon-reload
        systemctl enable msm-update.timer 2>/dev/null || true
        # Timer starten nur, wenn AUTO_UPDATE=true
        if [[ "${MSM_AUTO_UPDATE:-false}" == "true" ]]; then
            systemctl start msm-update.timer 2>/dev/null || true
            ok "Auto-Update Timer aktiviert (24h Intervall)"
        else
            systemctl stop msm-update.timer 2>/dev/null || true
            systemctl disable msm-update.timer 2>/dev/null || true
            ok "Auto-Update Timer registriert (deaktiviert — setze MSM_AUTO_UPDATE=true)"
        fi
        ok "Service registriert"

        # ── sudoers + iptables-Wrapper direkt als root schreiben (Sicherheitsfix) ──
        # Die privileged Artifakte (Wrapper-Gate + Policy) dürfen NIEMALS aus dem
        # msm-owned Baum ($MSM_DIR/backend/scripts) gelesen werden (chown -R msm
        # passiert früher). Deshalb: direkte Heredocs als root (wie ursprünglich),
        # aber mit dem korrekten Wrapper (variable Arg-Listen für DOCKER-USER)
        # und ohne Over-Privilege. SSOT-Dateien bleiben im Repo als Review-Master.
        if [[ -d /etc/sudoers.d ]]; then
            mkdir -p /usr/local/sbin
            # Wrapper (direkt als root geschrieben — kein cp aus msm-writable Source)
            cat > /usr/local/sbin/msm-iptables <<'WRAPEOF'
#!/bin/sh
# MSM iptables wrapper — thin, root-owned privilege gate for DOCKER-USER defense-in-depth.
# (Content authoritative in this heredoc + backend/scripts/msm-iptables for audit.)
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

            # Policy (direkt als root; nur Firewall-Gates, kein Container-systemctl)
            cat > /etc/sudoers.d/msm-panel <<'SUDOEOF'
# MSM Panel — Firewall (UFW/iptables) only
# Deployed via root heredoc in install.sh/update.sh (never read from msm-writable tree).

# UFW (exact from firewall_service.py; delete tightened to match allow glob)
msm ALL=(root) NOPASSWD: /usr/sbin/ufw --version
msm ALL=(root) NOPASSWD: /usr/sbin/ufw allow [0-9]*/[a-z]* comment *
msm ALL=(root) NOPASSWD: /usr/sbin/ufw delete allow [0-9]*/[a-z]*
msm ALL=(root) NOPASSWD: /usr/sbin/ufw status numbered

# iptables ONLY via vetted wrapper (enforces DOCKER-USER only for variable long args)
msm ALL=(root) NOPASSWD: /usr/local/sbin/msm-iptables
SUDOEOF
            chmod 440 /etc/sudoers.d/msm-panel
            ok "sudoers + iptables-Wrapper für Firewall-Regeln eingerichtet (direkt als root, ohne Container-systemctl)"
        fi

    else
        warn "systemd nicht verfügbar (typisch für WSL). msm-panel.service wird geschrieben, aber nicht aktiviert."
        warn "Starte manuell mit: cd /opt/msm/backend && source venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 8000"
    fi
fi

# Minimal-Apply: nur Auto-Update wurde geändert
if $REINSTALL_MODE && ! $KEEP_SETTINGS && $CHANGED_AUTO_UPDATE && ! $RUN_SYSTEMD_SETUP; then
    if $SYSTEMD_AVAILABLE; then
        log "Aktualisiere Auto-Update Timer..."
        cp "$SCRIPT_DIR/msm-update.service" /etc/systemd/system/msm-update.service 2>/dev/null || true
        cp "$SCRIPT_DIR/msm-update.timer" /etc/systemd/system/msm-update.timer 2>/dev/null || true
        systemctl daemon-reload
        if [[ "$MSM_AUTO_UPDATE" == "true" ]]; then
            systemctl enable msm-update.timer 2>/dev/null || true
            systemctl start msm-update.timer 2>/dev/null || true
            ok "Auto-Update Timer aktiviert"
        else
            systemctl stop msm-update.timer 2>/dev/null || true
            systemctl disable msm-update.timer 2>/dev/null || true
            ok "Auto-Update Timer deaktiviert"
        fi
    fi
fi

# ═══════════════════════════════════════════════════════════════
# 12. Firewall (UFW)
# ═══════════════════════════════════════════════════════════════
log "Konfiguriere Firewall..."
if command -v ufw &>/dev/null; then
    ufw default deny incoming 2>/dev/null || true
    ufw default allow outgoing 2>/dev/null || true
    ufw allow 22/tcp comment 'SSH' 2>/dev/null || true
    ufw allow 80/tcp comment 'HTTP' 2>/dev/null || true
    ufw allow 443/tcp comment 'HTTPS' 2>/dev/null || true
    # Spiel-Ports werden ab Phase 2 NICHT mehr als Range freigegeben.
    # Der Port-Manager des Panels öffnet je Server nur die konkret
    # zugewiesenen Einzelports (game/udp, query/udp, rcon/tcp) und schließt
    # sie beim Stop wieder. Siehe backend/services/firewall_service.py.
    ufw --force enable 2>/dev/null || true
    ok "Firewall aktiviert (UFW) — Ports 22, 80, 443 offen. Spiel-Ports werden zur Laufzeit vom Panel verwaltet."
else
    warn "UFW nicht verfügbar. Firewall manuell konfigurieren."
fi

# ═══════════════════════════════════════════════════════════════
# 13. Fail2ban (Brute-Force-Schutz)
# ═══════════════════════════════════════════════════════════════
if ! command -v fail2ban-server &>/dev/null; then
    log "Installiere Fail2ban..."
    apt-get install -y -qq fail2ban 2>&1 | tee -a "$LOG_FILE"
else
    log "Fail2ban ist bereits installiert. Konfiguration wird aktualisiert..."
fi

# Eigene Filterregel für das Panel-Auth
mkdir -p /etc/fail2ban/filter.d
cat > /etc/fail2ban/filter.d/msm-panel.conf <<'EOF'
[Definition]
failregex = ^.*Forbidden login attempt from <HOST>.*$
            ^.*Invalid credentials from <HOST>.*$
            ^.*Too many failed attempts from <HOST>.*$
journalmatch = _SYSTEMD_UNIT=msm-panel.service
EOF

cat > /etc/fail2ban/jail.local <<EOF
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log

[msm-panel]
enabled = true
port = 80,443
filter = msm-panel
logpath = /var/log/syslog
backend = systemd
EOF

if $SYSTEMD_AVAILABLE; then
    systemctl enable fail2ban >/dev/null 2>&1 || true
    systemctl restart fail2ban >/dev/null 2>&1 || true
    ok "Fail2ban aktiviert (SSH + Panel Brute-Force-Schutz)"
else
    warn "systemd nicht verfügbar — Fail2ban-Konfiguration geschrieben, aber nicht gestartet."
fi

# ═══════════════════════════════════════════════════════════════
# 14. Service starten / neustarten
# ═══════════════════════════════════════════════════════════════
log "Starte Panel-Service..."
if $SYSTEMD_AVAILABLE; then
    systemctl restart msm-panel.service 2>/dev/null || systemctl start msm-panel.service 2>/dev/null || true
    sleep 2
    if systemctl is-active --quiet msm-panel.service; then
        ok "Panel-Service läuft"
    else
        warn "Panel-Service startet nicht automatisch. Prüfe: journalctl -u msm-panel -n 50"
    fi
else
    warn "systemd nicht verfügbar — Service muss manuell gestartet werden."
    warn "Starte manuell mit: cd /opt/msm/backend && source venv/bin/activate && uvicorn main:app --host 127.0.0.1 --port 8000"
fi

# ═══════════════════════════════════════════════════════════════
# 15. Fertig — Zusammenfassung
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
if $REINSTALL_MODE; then
    echo -e "${GREEN}  Maunting Server Manager erfolgreich aktualisiert!${NC}"
else
    echo -e "${GREEN}  Maunting Server Manager erfolgreich installiert!${NC}"
fi
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"

# Transparenz: Was wurde geändert?
if $REINSTALL_MODE; then
    echo ""
    echo -e "  ${BOLD}Durchgeführte Änderungen:${NC}"
    if $KEEP_SETTINGS; then
        echo -e "    ${GREEN}•${NC} Quellcode aktualisiert"
        echo -e "    ${GREEN}•${NC} Frontend neu gebaut"
        echo -e "    ${GREEN}•${NC} Services neugestartet"
        echo -e "    ${CYAN}•${NC} Konfiguration unverändert"
    else
        if $CHANGED_DOMAIN; then     echo -e "    ${YELLOW}•${NC} Domain geändert";       else echo -e "    ${CYAN}•${NC} Domain unverändert"; fi
        if $CHANGED_EMAIL; then       echo -e "    ${YELLOW}•${NC} Email geändert";         else echo -e "    ${CYAN}•${NC} Email unverändert"; fi
        if $CHANGED_DB; then         echo -e "    ${YELLOW}•${NC} Datenbank geändert";     else echo -e "    ${CYAN}•${NC} Datenbank unverändert"; fi
        if $CHANGED_REDIS; then      echo -e "    ${YELLOW}•${NC} Redis geändert";          else echo -e "    ${CYAN}•${NC} Redis unverändert"; fi
        if $CHANGED_AUTO_UPDATE; then echo -e "    ${YELLOW}•${NC} Auto-Update geändert";   else echo -e "    ${CYAN}•${NC} Auto-Update unverändert"; fi
        if $CODE_CHANGED; then       echo -e "    ${GREEN}•${NC} Quellcode aktualisiert";  else echo -e "    ${CYAN}•${NC} Quellcode unverändert"; fi
    fi
fi
echo ""

if [[ -n "$DOMAIN" ]]; then
    echo -e "  ${BOLD}Panel-URL:${NC}     ${CYAN}https://$DOMAIN${NC}"
else
    PUBLIC_IP=$(curl -s -4 ifconfig.me 2>/dev/null || echo "<DEINE-IP>")
    echo -e "  ${BOLD}Panel-URL:${NC}     ${CYAN}http://$PUBLIC_IP${NC}"
    if ! $REINSTALL_MODE; then
        echo -e "  ${YELLOW}Hinweis:${NC}      Für HTTPS eine Domain einrichten und install.sh erneut laufen lassen."
    fi
fi

echo ""
echo -e "  ${BOLD}Installationspfad:${NC}  $MSM_DIR"
echo -e "  ${BOLD}Log-Datei:${NC}          $LOG_FILE"
echo -e "  ${BOLD}Konfiguration:${NC}      $ENV_FILE"
echo ""

if $USE_POSTGRES; then
    echo -e "  ${GREEN}Datenbank:${NC}         PostgreSQL (DB: msm, User: msm)"
else
    echo -e "  ${YELLOW}Datenbank:${NC}         SQLite (empfohlen: PostgreSQL für Produktion)"
fi

if [[ "$EMAIL_PROVIDER" == "resend" && -n "$RESEND_API_KEY" ]]; then
    echo -e "  ${GREEN}Email:${NC}             Resend (API-Key konfiguriert)"
elif [[ -n "$SMTP_HOST" ]]; then
    echo -e "  ${GREEN}Email:${NC}             SMTP $SMTP_HOST:$SMTP_PORT"
else
    echo -e "  ${YELLOW}Email nicht konfiguriert.${NC}"
    echo -e "         Setze MSM_RESEND_API_KEY oder MSM_SMTP_* in $ENV_FILE."
fi

if $INSTALL_REDIS; then
    echo -e "  ${GREEN}Redis aktiv:${NC}       Verteiltes Rate-Limiting einsatzbereit"
else
    echo -e "  ${YELLOW}Redis nicht aktiv:${NC}  Rate-Limiting nutzt in-memory (verliert State bei Neustart)"
fi

if [[ "$MSM_AUTO_UPDATE" == "true" ]]; then
    echo -e "  ${GREEN}Auto-Update:${NC}      Aktiviert (24h Intervall)"
else
    echo -e "  ${YELLOW}Auto-Update:${NC}      Deaktiviert"
fi

echo ""
echo -e "  ${BOLD}Nächste Schritte:${NC}"
echo -e "    1. Panel im Browser öffnen"
echo -e "    2. Setup-Wizard durchlaufen (erfordert gültige Email-Adresse)"
echo -e "    3. Ersten Owner-Account erstellen"
echo -e "    4. Game-Server erstellen — jeder Server läuft isoliert mit eigenem Linux-User"
echo ""
echo -e "  ${BOLD}Wichtige Befehle:${NC}"
echo -e "    ${CYAN}sudo systemctl status msm-panel${NC}    — Service-Status prüfen"
echo -e "    ${CYAN}sudo journalctl -u msm-panel -f${NC}  — Live-Logs"
echo -e "    ${CYAN}sudo systemctl restart msm-panel${NC}  — Service neustarten"
echo -e "    ${CYAN}sudo systemctl restart caddy${NC}      — Caddy neustarten"
echo ""
echo -e "  ${BOLD}Sicherheit:${NC}"
echo -e "    - Firewall (UFW) aktiv, Ports 22/80/443 offen"
echo -e "    - Fail2ban schützt SSH und Panel vor Brute-Force"
echo -e "    - SECRET_KEY automatisch generiert (256-bit)"
echo -e "    - .env mit chmod 600 geschützt"
echo -e "    - Game-Server isoliert: eigener User, eigene systemd-Unit, Resource-Limits"
echo ""
