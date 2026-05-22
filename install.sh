#!/usr/bin/env bash
set -euo pipefail

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
#  Du musst nur 3 Fragen beantworten:
#    1. Domain (oder IP-Modus)
#    2. SMTP-Daten (oder "später")
#    3. Redis (ja/nein)
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

log "=== Maunting Server Manager Installation ==="
log "Log: $LOG_FILE"
log ""

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
if ! command -v caddy &>/dev/null; then
    log "Installiere Caddy..."
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https 2>&1 | tee -a "$LOG_FILE"
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' 2>/dev/null | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>&1 | tee -a "$LOG_FILE"
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' 2>/dev/null | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -qq | tee -a "$LOG_FILE"
    apt-get install -y -qq caddy 2>&1 | tee -a "$LOG_FILE"
fi

# ── steamcmd ──
if ! command -v steamcmd &>/dev/null; then
    log "Installiere steamcmd..."
    dpkg --add-architecture i386 2>/dev/null || true
    apt-get update -qq | tee -a "$LOG_FILE"
    apt-get install -y -qq steamcmd 2>&1 | tee -a "$LOG_FILE" || {
        warn "steamcmd nicht über apt verfügbar. Versuche manuelle Installation..."
        mkdir -p /usr/games
        wget -q https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz -O /tmp/steamcmd.tar.gz
        tar -xzf /tmp/steamcmd.tar.gz -C /usr/games/
        chmod +x /usr/games/steamcmd.sh
        ln -sf /usr/games/steamcmd.sh /usr/local/bin/steamcmd 2>/dev/null || true
    }
fi

ok "System-Abhängigkeiten installiert"

# ═══════════════════════════════════════════════════════════════
# 2. Redis (optional, empfohlen für Rate-Limiting)
# ═══════════════════════════════════════════════════════════════
INSTALL_REDIS=false
if ask_yesno "Redis für verteiltes Rate-Limiting installieren? (empfohlen für Produktion)"; then
    log "Installiere Redis..."
    apt-get install -y -qq redis-server 2>&1 | tee -a "$LOG_FILE"
    systemctl enable redis-server >/dev/null 2>&1 || true
    systemctl start redis-server >/dev/null 2>&1 || true
    INSTALL_REDIS=true
    ok "Redis installiert"
else
    log "Redis wird übersprungen (Rate-Limiting nutzt in-memory Fallback)."
fi

# ═══════════════════════════════════════════════════════════════
# 3. MSM System-User
# ═══════════════════════════════════════════════════════════════
if ! id "$MSM_USER" &>/dev/null; then
    log "Erstelle System-User '$MSM_USER'..."
    useradd -r -m -s /bin/bash -d "$MSM_DIR" "$MSM_USER"
fi
ok "User '$MSM_USER' bereit"

# ═══════════════════════════════════════════════════════════════
# 4. Dateien kopieren
# ═══════════════════════════════════════════════════════════════
log "Kopiere Panel-Dateien nach $MSM_DIR..."

if [[ "$SCRIPT_DIR" == "$MSM_DIR" ]]; then
    # Install.sh läuft direkt im Zielverzeichnis → nicht löschen!
    # Nur alte Build-Artefakte und Konfigurationen aufräumen
    rm -rf "$MSM_DIR/frontend/dist" 2>/dev/null || true
    rm -rf "$MSM_DIR/frontend/node_modules" 2>/dev/null || true
    rm -rf "$MSM_DIR/backend/venv" 2>/dev/null || true
else
    # Install.sh läuft außerhalb des Zielverzeichnisses → sauberes Verzeichnis anlegen
    rm -rf "$MSM_DIR" 2>/dev/null || true
    mkdir -p "$MSM_DIR"
fi

cp -r "$SCRIPT_DIR/backend" "$MSM_DIR/"
cp -r "$SCRIPT_DIR/frontend" "$MSM_DIR/"
cp -r "$SCRIPT_DIR/docs" "$MSM_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/Caddyfile.template" "$MSM_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/msm.service.template" "$MSM_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/update.sh" "$MSM_DIR/" 2>/dev/null || true
chmod +x "$MSM_DIR/update.sh" 2>/dev/null || true
chown -R "$MSM_USER:$MSM_USER" "$MSM_DIR"
ok "Dateien kopiert"

# ═══════════════════════════════════════════════════════════════
# 5. Interaktive Konfiguration (nur 3 Fragen!)
# ═══════════════════════════════════════════════════════════════

# ── 5a. Domain ──
echo ""
echo -e "${BOLD}Schritt 1/3: Domain${NC}"
echo "  Gib eine Domain an, damit Caddy automatisch ein SSL-Zertifikat erstellt."
echo "  Ohne Domain wird das Panel über HTTP (nur IP) erreichbar — nicht empfohlen."
echo ""
ask "Domain (z.B. panel.deinserver.de) oder leer lassen für IP-Modus: " DOMAIN
DOMAIN="${DOMAIN:-}"

# ── 5b. Email (SMTP oder Resend) ──
echo ""
echo -e "${BOLD}Schritt 2/3: Email-Versand${NC}"
echo "  Wird für Setup-Verifikation, 2FA-Setup und Backup-Codes benötigt."
echo "  Optionen:"
echo "    1. Resend (resend.com) — API-Key, kein SMTP nötig, empfohlen"
echo "    2. SMTP (eigener Server, Strato, Gmail, etc.)"
echo "  Du kannst das später in /opt/msm/backend/.env nachholen."
echo ""

EMAIL_PROVIDER="smtp"
SMTP_HOST=""
SMTP_PORT=587
SMTP_USER=""
SMTP_PASS=""
SMTP_FROM=""
RESEND_API_KEY=""

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

# ── 5c. Redis Confirmation ──
REDIS_URL=""
if $INSTALL_REDIS; then
    REDIS_URL="redis://localhost:6379"
fi

# ── 5d. PostgreSQL (empfohlen für Produktion) ──
USE_POSTGRES=false
PG_PASSWORD=""
if ask_yesno "PostgreSQL für die Datenbank nutzen? (empfohlen für Produktion, sonst SQLite)"; then
    log "Installiere PostgreSQL..."
    apt-get install -y -qq postgresql postgresql-contrib libpq-dev python3-dev 2>&1 | tee -a "$LOG_FILE"

    PG_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    su - postgres -c "psql -c \"CREATE USER msm WITH PASSWORD '$PG_PASSWORD';\"" 2>&1 | tee -a "$LOG_FILE" || true
    su - postgres -c "psql -c \"CREATE DATABASE msm OWNER msm;\"" 2>&1 | tee -a "$LOG_FILE" || true
    su - postgres -c "psql -c \"GRANT ALL PRIVILEGES ON DATABASE msm TO msm;\"" 2>&1 | tee -a "$LOG_FILE" || true

    # pg_hba.conf: msm-User darf lokal mit Passwort verbinden
    sed -i 's/^local\s\+all\s\+all\s\+peer/local   all   all   scram-sha-256/' /etc/postgresql/*/main/pg_hba.conf 2>/dev/null || true
    systemctl restart postgresql 2>/dev/null || true

    USE_POSTGRES=true
    ok "PostgreSQL installiert (DB: msm, User: msm)"
else
    log "SQLite wird als Datenbank genutzt (einfach, aber nicht für hohe Last)."
fi

# ═══════════════════════════════════════════════════════════════
# 6. SECRET_KEY generieren & .env schreiben
# ═══════════════════════════════════════════════════════════════
log "Generiere kryptographischen Secret-Key..."
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")

PANEL_URL="http://localhost"
if [[ -n "$DOMAIN" ]]; then
    PANEL_URL="https://$DOMAIN"
fi

ENV_FILE="$MSM_DIR/backend/.env"

# Datenbank-URL wählen
if $USE_POSTGRES; then
    DB_URL="postgresql+psycopg2://msm:$PG_PASSWORD@localhost:5432/msm"
    DB_URL_ASYNC="postgresql+asyncpg://msm:$PG_PASSWORD@localhost:5432/msm"
else
    DB_URL="sqlite:///./msm.db"
    DB_URL_ASYNC="sqlite+aiosqlite:///./msm.db"
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
MSM_STEAMCMD_PATH="/usr/games/steamcmd"
REDIS_URL="$REDIS_URL"

# Auto-Update (GitHub Releases)
MSM_GITHUB_OWNER="einmalmaik"
MSM_GITHUB_REPO="maunting-server-manager"
MSM_AUTO_UPDATE=false
MSM_AUTO_UPDATE_INTERVAL_HOURS=24
EOF

chmod 600 "$ENV_FILE"
chown "$MSM_USER:$MSM_USER" "$ENV_FILE"
ok ".env mit sicherem SECRET_KEY erstellt (chmod 600)"

# ═══════════════════════════════════════════════════════════════
# 7. Python-Backend einrichten
# ═══════════════════════════════════════════════════════════════
log "Installiere Python-Abhängigkeiten..."
su - "$MSM_USER" -c "
    cd $MSM_DIR/backend
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
" 2>&1 | tee -a "$LOG_FILE"
ok "Python-Backend bereit"

# ═══════════════════════════════════════════════════════════════
# 8. Datenbank initialisieren
# ═══════════════════════════════════════════════════════════════
log "Initialisiere Datenbank..."
su - "$MSM_USER" -c "
    cd $MSM_DIR/backend
    source venv/bin/activate
    alembic upgrade head 2>/dev/null || python3 -c \"from database import engine, Base; from models import *; Base.metadata.create_all(engine)\"
" 2>&1 | tee -a "$LOG_FILE"
ok "Datenbank initialisiert"

# ═══════════════════════════════════════════════════════════════
# 9. Frontend bauen
# ═══════════════════════════════════════════════════════════════
log "Baue Frontend..."
su - "$MSM_USER" -c "
    cd $MSM_DIR/frontend
    npm install -q
    npm run build
" 2>&1 | tee -a "$LOG_FILE"
ok "Frontend gebaut"

# ═══════════════════════════════════════════════════════════════
# 10. Caddy konfigurieren
# ═══════════════════════════════════════════════════════════════
log "Konfiguriere Caddy..."

CADDY_CONFIG="/etc/caddy/Caddyfile"
mkdir -p /etc/caddy

if [[ -n "$DOMAIN" ]]; then
    # Produktion: Domain + TLS
    cat > "$CADDY_CONFIG" <<EOF
$DOMAIN {
    root * /opt/msm/frontend/dist
    file_server
    try_files {path} /index.html

    encode gzip

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
        Permissions-Policy "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    }

    handle_path /api/* {
        reverse_proxy localhost:8000
    }

    handle_path /ws/* {
        reverse_proxy localhost:8000
    }
}
EOF
else
    # IP-Modus: HTTP only (nicht empfohlen für Produktion)
    cat > "$CADDY_CONFIG" <<EOF
:80 {
    root * /opt/msm/frontend/dist
    file_server
    try_files {path} /index.html

    encode gzip

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
    }

    handle_path /api/* {
        reverse_proxy localhost:8000
    }

    handle_path /ws/* {
        reverse_proxy localhost:8000
    }
}
EOF
    warn "Keine Domain angegeben — Panel läuft über HTTP (nicht empfohlen für Produktion)."
fi

systemctl reload caddy 2>/dev/null || systemctl restart caddy 2>/dev/null || true
ok "Caddy konfiguriert"

# ═══════════════════════════════════════════════════════════════
# 11. systemd Service
# ═══════════════════════════════════════════════════════════════
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
Environment="PATH=/opt/msm/backend/venv/bin"
ExecStart=/opt/msm/backend/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

# Security Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/msm/backend

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable msm-panel.service

# Update-Timer (optional — deaktiviert per Default)
cp "$SCRIPT_DIR/msm-update.service" /etc/systemd/system/msm-update.service 2>/dev/null || true
cp "$SCRIPT_DIR/msm-update.timer" /etc/systemd/system/msm-update.timer 2>/dev/null || true
systemctl daemon-reload
systemctl enable msm-update.timer 2>/dev/null || true
# Timer starten nur, wenn AUTO_UPDATE=true
if [[ "$MSM_AUTO_UPDATE" == "true" ]]; then
    systemctl start msm-update.timer 2>/dev/null || true
    ok "Auto-Update Timer aktiviert (24h Intervall)"
else
    ok "Auto-Update Timer registriert (deaktiviert — setze MSM_AUTO_UPDATE=true)"
fi
ok "Service registriert"

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
    # Game-Server Port-Range (UDP für Game + Query, TCP für RCon)
    ufw allow 27015:27999/udp comment 'MSM Game-Server UDP' 2>/dev/null || true
    ufw allow 27015:27999/tcp comment 'MSM Game-Server TCP (RCon)' 2>/dev/null || true
    ufw --force enable 2>/dev/null || true
    ok "Firewall aktiviert (UFW) — Ports 22, 80, 443 + Game-Range 27015-27999 offen"
else
    warn "UFW nicht verfügbar. Firewall manuell konfigurieren."
fi

# ═══════════════════════════════════════════════════════════════
# 13. Fail2ban (Brute-Force-Schutz)
# ═══════════════════════════════════════════════════════════════
log "Installiere Fail2ban..."
apt-get install -y -qq fail2ban 2>&1 | tee -a "$LOG_FILE"

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

systemctl enable fail2ban >/dev/null 2>&1 || true
systemctl restart fail2ban >/dev/null 2>&1 || true
ok "Fail2ban aktiviert (SSH + Panel Brute-Force-Schutz)"

# ═══════════════════════════════════════════════════════════════
# 14. Service starten
# ═══════════════════════════════════════════════════════════════
log "Starte Panel-Service..."
systemctl start msm-panel.service
sleep 2

if systemctl is-active --quiet msm-panel.service; then
    ok "Panel-Service läuft"
else
    warn "Panel-Service startet nicht automatisch. Prüfe: journalctl -u msm-panel -n 50"
fi

# ═══════════════════════════════════════════════════════════════
# 15. Fertig — Zusammenfassung
# ═══════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Maunting Server Manager erfolgreich installiert!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""

if [[ -n "$DOMAIN" ]]; then
    echo -e "  ${BOLD}Panel-URL:${NC}     ${CYAN}https://$DOMAIN${NC}"
else
    PUBLIC_IP=$(curl -s -4 ifconfig.me 2>/dev/null || echo "<DEINE-IP>")
    echo -e "  ${BOLD}Panel-URL:${NC}     ${CYAN}http://$PUBLIC_IP${NC}"
    echo -e "  ${YELLOW}Hinweis:${NC}      Für HTTPS eine Domain einrichten und install.sh erneut laufen lassen."
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
