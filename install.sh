#!/usr/bin/env bash
set -euo pipefail

# Maunting Server Manager — One-Script Installation
# Supports: Ubuntu 22.04+, Debian 12+, WSL2

MSM_USER="msm"
MSM_DIR="/opt/msm"
LOG_FILE="/tmp/msm-install.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

log() { echo -e "${CYAN}[MSM]${NC} $1" | tee -a "$LOG_FILE"; }
ok()  { echo -e "${GREEN}[OK]${NC}  $1" | tee -a "$LOG_FILE"; }
err() { echo -e "${RED}[ERR]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }

# ── Prüfungen ──────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then err "Bitte als root ausführen: sudo bash install.sh"; fi

if ! grep -qEi 'ubuntu|debian' /etc/os-release 2>/dev/null; then
    log "Warnung: Nicht Ubuntu/Debian erkannt. Installation wird fortgesetzt, kann aber fehlschlagen."
fi

log "=== Maunting Server Manager Installation ==="
log "Log: $LOG_FILE"

# ── Abhängigkeiten ─────────────────────────────────────────────
log "Installiere System-Abhängigkeiten..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    nodejs npm \
    caddy \
    sqlite3 \
    curl wget git \
    systemd systemd-sysv \
    libc6-i386 lib32stdc++6 lib32gcc-s1 \
    2>&1 | tee -a "$LOG_FILE"

# steamcmd
if ! command -v steamcmd &>/dev/null; then
    log "Installiere steamcmd..."
    apt-get install -y -qq software-properties-common 2>&1 | tee -a "$LOG_FILE"
    add-apt-repository -y multiverse 2>&1 | tee -a "$LOG_FILE"
    dpkg --add-architecture i386 2>/dev/null || true
    apt-get update -qq
    apt-get install -y -qq steamcmd 2>&1 | tee -a "$LOG_FILE"
fi
ok "System-Abhängigkeiten installiert"

# ── MSM User ───────────────────────────────────────────────────
if ! id "$MSM_USER" &>/dev/null; then
    log "Erstelle Linux-User '$MSM_USER'..."
    useradd -r -m -s /bin/bash -d "$MSM_DIR" "$MSM_USER"
fi
ok "User '$MSM_USER' bereit"

# ── Dateien kopieren ────────────────────────────────────────────
log "Kopiere Panel-Dateien nach $MSM_DIR..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Falls wir aus einem Git-Repo oder entpackten Archiv laufen
if [[ -d "$SCRIPT_DIR/backend" && -d "$SCRIPT_DIR/frontend" ]]; then
    cp -r "$SCRIPT_DIR/backend" "$MSM_DIR/"
    cp -r "$SCRIPT_DIR/frontend" "$MSM_DIR/"
    cp -r "$SCRIPT_DIR/docs" "$MSM_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR/Caddyfile.template" "$MSM_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR/msm.service.template" "$MSM_DIR/" 2>/dev/null || true
else
    err "Backend/Frontend-Ordner nicht gefunden. Bitte aus dem Repository-Root ausführen."
fi

chown -R "$MSM_USER:$MSM_USER" "$MSM_DIR"
ok "Dateien kopiert"

# ── Python Backend ─────────────────────────────────────────────
log "Richte Python-Backend ein..."
su - "$MSM_USER" -c "
    cd $MSM_DIR/backend
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
" 2>&1 | tee -a "$LOG_FILE"
ok "Python-Backend bereit"

# ── Frontend builden ──────────────────────────────────────────
log "Baue Frontend..."
su - "$MSM_USER" -c "
    cd $MSM_DIR/frontend
    npm install -q
    npm run build
" 2>&1 | tee -a "$LOG_FILE"
ok "Frontend gebaut"

# ── Datenbank initialisieren ──────────────────────────────────
log "Initialisiere Datenbank..."
su - "$MSM_USER" -c "
    cd $MSM_DIR/backend
    source venv/bin/activate
    alembic upgrade head 2>/dev/null || python3 -c \"from database import engine, Base; from models import *; Base.metadata.create_all(engine)\"
" 2>&1 | tee -a "$LOG_FILE"
ok "Datenbank initialisiert"

# ── Caddy einrichten ────────────────────────────────────────────
log "Richte Caddy ein..."
read -rp "Domain eingeben (oder leer lassen für IP-Zugriff): " DOMAIN

if [[ -n "$DOMAIN" ]]; then
    sed "s/{{DOMAIN}}/$DOMAIN/g" "$MSM_DIR/Caddyfile.template" > /etc/caddy/Caddyfile
else
    # IP-Modus: nur HTTP auf Port 80
    sed 's/{{DOMAIN}}/localhost/g; s/:443/:80/g; s/tls internal/# tls internal/' "$MSM_DIR/Caddyfile.template" > /etc/caddy/Caddyfile
fi

systemctl reload caddy 2>/dev/null || systemctl start caddy
ok "Caddy konfiguriert"

# ── systemd Service ─────────────────────────────────────────────
log "Registriere systemd Service..."
sed "s|{{MSM_DIR}}|$MSM_DIR|g" "$MSM_DIR/msm.service.template" > /etc/systemd/system/msm-panel.service
systemctl daemon-reload
systemctl enable msm-panel.service
ok "Service registriert"

# ── Firewall ────────────────────────────────────────────────────
if command -v ufw &>/dev/null; then
    ufw allow 80/tcp &>/dev/null || true
    ufw allow 443/tcp &>/dev/null || true
fi

# ── Fertig ──────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Maunting Server Manager erfolgreich installiert!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
if [[ -n "$DOMAIN" ]]; then
    echo -e "  Panel-URL: ${CYAN}https://$DOMAIN${NC}"
else
    echo -e "  Panel-URL: ${CYAN}http://<SERVER-IP>${NC}"
fi
echo "  Installationspfad: $MSM_DIR"
echo "  Log: $LOG_FILE"
echo ""
echo -e "  ${CYAN}Starte nun den Service:${NC}"
echo -e "  sudo systemctl start msm-panel"
echo ""
echo -e "  ${CYAN}Öffne das Panel und erstelle den ersten Owner.${NC}"
echo ""
