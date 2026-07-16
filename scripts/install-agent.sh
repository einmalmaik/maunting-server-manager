#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  MSM Agent Installer (Phase 5) — remote node runtime
#  Usage:  sudo bash scripts/install-agent.sh
#          sudo bash install-agent.sh   (when run from repo root)
#  Requires: Ubuntu 22.04+ / Debian 12+, root, Python 3.11+
#  Sets up: msm user, rootless Docker, TLS cert, systemd unit
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
umask 077

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

MSM_USER="msm"
AGENT_DIR="/opt/msm-agent"
SERVERS_DIR="/opt/msm/servers"
CERT_DIR="${AGENT_DIR}/certs"
LOG_FILE="/tmp/msm-agent-install.log"
AGENT_PORT="${MSM_AGENT_PORT:-9000}"
AGENT_HOST="${MSM_AGENT_HOST:-0.0.0.0}"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${CYAN}[MSM-Agent]${NC} $1" | tee -a "$LOG_FILE"; }
ok()   { echo -e "${GREEN}[OK]${NC}   $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }
err()  { echo -e "${RED}[ERR]${NC}  $1" | tee -a "$LOG_FILE"; exit 1; }

# ── 1. System checks ──────────────────────────────────────────
if [[ "$(id -u)" -ne 0 ]]; then
  err "Bitte als root ausfuehren: sudo bash $0"
fi

if [[ -f /etc/os-release ]]; then
  # shellcheck source=/dev/null
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ok "OS: ${PRETTY_NAME:-$ID}" ;;
    *) warn "OS ${ID:-unknown} — getestet auf Ubuntu/Debian; fortfahren..." ;;
  esac
else
  warn "Kein /etc/os-release — fortfahren auf eigene Gefahr"
fi

if ! command -v python3 >/dev/null 2>&1; then
  err "python3 fehlt"
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)')
if [[ "$PY_OK" != "1" ]]; then
  err "Python 3.11+ erforderlich (gefunden: $PY_VER)"
fi
ok "Python $PY_VER"

# ── 2. Locate agent sources ───────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_AGENT=""
if [[ -d "$REPO_ROOT/msm-agent" && -f "$REPO_ROOT/msm-agent/main.py" ]]; then
  SRC_AGENT="$REPO_ROOT/msm-agent"
elif [[ -d "./msm-agent" && -f "./msm-agent/main.py" ]]; then
  SRC_AGENT="$(cd ./msm-agent && pwd)"
else
  err "msm-agent/ Quellverzeichnis nicht gefunden (Repo-Root neben scripts/ erwarten)"
fi
ok "Agent-Quellen: $SRC_AGENT"

# ── 3. System packages ────────────────────────────────────────
log "Installiere System-Pakete..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >>"$LOG_FILE" 2>&1 || warn "apt-get update hatte Warnungen"
apt-get install -y -qq \
  python3 python3-venv python3-pip \
  curl ca-certificates gnupg openssl sudo ufw rsync \
  uidmap dbus-user-session slirp4netns \
  >>"$LOG_FILE" 2>&1 || err "apt-get install fehlgeschlagen (siehe $LOG_FILE)"
ok "System-Pakete"

# ── 4. msm system user ────────────────────────────────────────
if ! id -u "$MSM_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/msm --shell /bin/bash "$MSM_USER"
  ok "User $MSM_USER angelegt"
else
  ok "User $MSM_USER existiert"
fi

# ── 5. Rootless Docker ────────────────────────────────────────
install_rootless_docker() {
  local msm_home docker_bin
  msm_home="$(getent passwd "$MSM_USER" | cut -d: -f6)"
  # Ensure linger so user services survive logout
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$MSM_USER" >>"$LOG_FILE" 2>&1 || true
  fi

  # Docker engine packages if missing
  if ! command -v dockerd-rootless-setuptool.sh >/dev/null 2>&1 && ! command -v docker >/dev/null 2>&1; then
    log "Installiere Docker CE (static packages)..."
    if [[ -f /etc/debian_version ]]; then
      install -m 0755 -d /etc/apt/keyrings
      if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
        curl -fsSL https://download.docker.com/linux/${ID}/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg
      fi
      echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME:-stable} stable" \
        > /etc/apt/sources.list.d/docker.list
      apt-get update -qq >>"$LOG_FILE" 2>&1
      apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin \
        >>"$LOG_FILE" 2>&1 || warn "docker-ce install unvollstaendig"
    fi
  fi

  # Rootless setup for msm user
  if [[ ! -S "/run/user/$(id -u "$MSM_USER")/docker.sock" ]] && \
     [[ ! -S "${msm_home}/.docker/run/docker.sock" ]]; then
    log "Richte rootless Docker fuer $MSM_USER ein..."
    # Prefer official rootless installer as msm
    if command -v dockerd-rootless-setuptool.sh >/dev/null 2>&1; then
      sudo -u "$MSM_USER" -H bash -c 'dockerd-rootless-setuptool.sh install' >>"$LOG_FILE" 2>&1 \
        || warn "dockerd-rootless-setuptool.sh meldete Fehler — manuell pruefen"
    else
      # Fallback: rootless-extras package path
      apt-get install -y -qq docker-ce-rootless-extras >>"$LOG_FILE" 2>&1 || true
      if command -v dockerd-rootless-setuptool.sh >/dev/null 2>&1; then
        sudo -u "$MSM_USER" -H bash -c 'dockerd-rootless-setuptool.sh install' >>"$LOG_FILE" 2>&1 \
          || warn "rootless Docker Setup unvollstaendig"
      else
        warn "Rootless-Setup-Tool fehlt — Docker manuell als rootless fuer $MSM_USER konfigurieren"
      fi
    fi
  else
    ok "Rootless Docker Socket bereits vorhanden"
  fi

  # sysctl for rootless networking
  if [[ ! -f /etc/sysctl.d/99-msm-rootless-docker.conf ]]; then
    cat > /etc/sysctl.d/99-msm-rootless-docker.conf <<'EOF'
kernel.unprivileged_userns_clone=1
net.ipv4.ip_unprivileged_port_start=0
EOF
    sysctl --system >>"$LOG_FILE" 2>&1 || true
  fi
}
install_rootless_docker
ok "Rootless Docker Schritt abgeschlossen"

MSM_UID="$(id -u "$MSM_USER")"
DOCKER_HOST_DEFAULT="unix:///run/user/${MSM_UID}/docker.sock"

# ── 6. Deploy agent files ─────────────────────────────────────
log "Deploy nach $AGENT_DIR ..."
mkdir -p "$AGENT_DIR" "$SERVERS_DIR" "$CERT_DIR"
# Copy agent tree excluding venv / .env / servers
rsync -a --delete \
  --exclude 'venv/' \
  --exclude '.env' \
  --exclude 'servers/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache/' \
  "$SRC_AGENT/" "$AGENT_DIR/" 2>>"$LOG_FILE" \
  || err "Gefiltertes Agent-Deployment fehlgeschlagen"
chown -R "$MSM_USER:$MSM_USER" "$AGENT_DIR" "$SERVERS_DIR"
ok "Agent-Dateien deployed"

# ── 7. venv + deps ────────────────────────────────────────────
log "Python venv..."
if [[ ! -d "$AGENT_DIR/venv" ]]; then
  sudo -u "$MSM_USER" python3 -m venv "$AGENT_DIR/venv"
fi
sudo -u "$MSM_USER" "$AGENT_DIR/venv/bin/pip" install --upgrade pip -q >>"$LOG_FILE" 2>&1
sudo -u "$MSM_USER" "$AGENT_DIR/venv/bin/pip" install -r "$AGENT_DIR/requirements.txt" -q >>"$LOG_FILE" 2>&1 \
  || err "pip install requirements fehlgeschlagen"
ok "Dependencies installiert"

# ── 8. TLS certificate (self-signed RSA 4096) ─────────────────
CERT_FILE="$CERT_DIR/agent.crt"
KEY_FILE="$CERT_DIR/agent.key"
if [[ ! -f "$CERT_FILE" || ! -f "$KEY_FILE" ]]; then
  log "Generiere Self-signed TLS Zertifikat (RSA 4096)..."
  openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
    -keyout "$KEY_FILE" -out "$CERT_FILE" \
    -subj "/CN=msm-agent/O=MauntingStudios/OU=MSM-Agent" \
    >>"$LOG_FILE" 2>&1 || err "openssl cert generation failed"
  chmod 600 "$KEY_FILE"
  chmod 644 "$CERT_FILE"
  chown "$MSM_USER:$MSM_USER" "$CERT_FILE" "$KEY_FILE"
  ok "Zertifikat erzeugt"
else
  ok "Bestehendes Zertifikat behalten"
fi

FINGERPRINT=$(openssl x509 -in "$CERT_FILE" -outform DER 2>/dev/null | sha256sum | awk '{print $1}')
if [[ -z "$FINGERPRINT" ]]; then
  # mac/busybox fallback
  FINGERPRINT=$(openssl x509 -in "$CERT_FILE" -noout -fingerprint -sha256 | sed 's/.*=//' | tr -d ':' | tr 'A-F' 'a-f')
fi
ok "Fingerprint berechnet"

# ── 9. Token + .env ───────────────────────────────────────────
ENV_FILE="$AGENT_DIR/.env"
if [[ -f "$ENV_FILE" ]] && grep -q '^MSM_AGENT_TOKEN=.\+' "$ENV_FILE" 2>/dev/null; then
  # Keep existing token
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
  AGENT_TOKEN="${MSM_AGENT_TOKEN}"
  ok "Bestehenden MSM_AGENT_TOKEN behalten"
else
  AGENT_TOKEN="$(openssl rand -base64 48 | tr -d '\n=/+' | head -c 48)"
  ok "Neuen MSM_AGENT_TOKEN generiert"
fi

cat > "$ENV_FILE" <<EOF
# Generated by install-agent.sh — do not commit
MSM_AGENT_TOKEN=${AGENT_TOKEN}
MSM_AGENT_HOST=${AGENT_HOST}
MSM_AGENT_PORT=${AGENT_PORT}
MSM_SERVERS_DIR=${SERVERS_DIR}
MSM_DOCKER_HOST=${DOCKER_HOST_DEFAULT}
MSM_AGENT_LOG_LEVEL=INFO
MSM_TLS_CERTFILE=${CERT_FILE}
MSM_TLS_KEYFILE=${KEY_FILE}
EOF
chown "$MSM_USER:$MSM_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"
ok ".env geschrieben"

# Narrow root boundary for node-local UFW changes. The agent may only open or
# close one validated TCP/UDP port per invocation; arbitrary UFW arguments are
# impossible through this wrapper.
FIREWALL_WRAPPER="/usr/local/sbin/msm-agent-firewall"
cat > "$FIREWALL_WRAPPER" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
action="${1:-}"
port="${2:-}"
protocol="${3:-}"
server_name="${4:-server}"
role="${5:-game}"
[[ "$action" =~ ^(open|close)$ ]] || exit 2
[[ "$port" =~ ^[0-9]{1,5}$ ]] && (( port >= 1 && port <= 65535 )) || exit 2
[[ "$protocol" =~ ^(tcp|udp)$ ]] || exit 2
[[ "$server_name" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || exit 2
[[ "$role" =~ ^[A-Za-z0-9_.-]{1,32}$ ]] || exit 2
if [[ "$action" == "open" ]]; then
  exec /usr/sbin/ufw allow "${port}/${protocol}" comment "MSM ${server_name:0:24} ${role}"
fi
exec /usr/sbin/ufw delete allow "${port}/${protocol}"
EOF
chown root:root "$FIREWALL_WRAPPER"
chmod 0755 "$FIREWALL_WRAPPER"
cat > /etc/sudoers.d/msm-agent-firewall <<EOF
${MSM_USER} ALL=(root) NOPASSWD: ${FIREWALL_WRAPPER} *
EOF
chmod 0440 /etc/sudoers.d/msm-agent-firewall
visudo -cf /etc/sudoers.d/msm-agent-firewall >/dev/null || err "Ungueltige Firewall-sudoers-Regel"
ok "Node-Firewall-Wrapper installiert"

# ── 10. systemd unit ──────────────────────────────────────────
UNIT_PATH="/etc/systemd/system/msm-agent.service"
cat > "$UNIT_PATH" <<EOF
[Unit]
Description=MSM Agent (Node Runtime)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${MSM_USER}
Group=${MSM_USER}
WorkingDirectory=${AGENT_DIR}
Environment="PATH=${AGENT_DIR}/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="DOCKER_HOST=${DOCKER_HOST_DEFAULT}"
Environment="XDG_RUNTIME_DIR=/run/user/${MSM_UID}"
EnvironmentFile=-${ENV_FILE}
ExecStart=${AGENT_DIR}/venv/bin/python main.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

# Least privilege
PrivateTmp=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=${AGENT_DIR} ${SERVERS_DIR} /run/user/${MSM_UID} /home/${MSM_USER}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable msm-agent.service >>"$LOG_FILE" 2>&1
systemctl restart msm-agent.service >>"$LOG_FILE" 2>&1 || warn "Service-Start fehlgeschlagen — journalctl -u msm-agent"
sleep 1
if systemctl is-active --quiet msm-agent.service; then
  ok "msm-agent.service aktiv"
else
  if [[ "${MSM_AGENT_ENROLLMENT:-false}" == "true" ]]; then
    err "msm-agent.service ist nicht aktiv — siehe: journalctl -u msm-agent -n 50"
  fi
  warn "msm-agent.service nicht aktiv — siehe: journalctl -u msm-agent -n 50"
fi

# Open only the configured agent port when UFW is already active. Cloud-provider
# firewalls remain outside the server and must still be configured there.
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
  ufw allow "${AGENT_PORT}/tcp" comment 'MSM Agent' >>"$LOG_FILE" 2>&1 \
    || err "UFW-Regel fuer Agent-Port ${AGENT_PORT} konnte nicht gesetzt werden"
  ok "UFW-Port ${AGENT_PORT}/tcp freigegeben"
fi

# ── 11. Detect public IP (best-effort) ────────────────────────
PUBLIC_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
PUBLIC_IP="${PUBLIC_IP:-127.0.0.1}"
AGENT_URL="https://${PUBLIC_IP}:${AGENT_PORT}"

# Enrollment installs never print the token. Manual installs retain the legacy
# copy flow until all supported panels provide enrollment.
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN} MSM Agent Installation abgeschlossen${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Agent-URL (Panel → Node host):"
echo -e "    ${CYAN}${AGENT_URL}${NC}"
echo ""
if [[ "${MSM_AGENT_ENROLLMENT:-false}" != "true" ]]; then
  echo -e "  MSM_AGENT_TOKEN (nur jetzt anzeigen — in Panel speichern):"
  echo -e "    ${YELLOW}${AGENT_TOKEN}${NC}"
  echo ""
fi
echo -e "  TLS Fingerprint SHA-256 (Panel → tls_fingerprint):"
echo -e "    ${GREEN}${FINGERPRINT}${NC}"
echo ""
echo -e "  Install-Dir:  ${AGENT_DIR}"
echo -e "  Servers-Dir:  ${SERVERS_DIR}"
echo -e "  Service:      systemctl status msm-agent"
echo -e "  Log:          ${LOG_FILE}"
echo ""
echo -e "  Firewall:     UFW automatisch konfiguriert, falls aktiv"
echo -e "                Cloud-Firewall muss Port ${AGENT_PORT}/tcp erlauben"
echo ""
if [[ "${MSM_AGENT_ENROLLMENT:-false}" != "true" ]]; then
  echo -e "${BOLD}Im Panel:${NC} Admin → Nodes → Node hinzufuegen mit URL, Token und Fingerprint."
else
  echo -e "${BOLD}Enrollment:${NC} Agent lokal bereit; warte auf Bestätigung im Panel."
fi
echo -e "${BOLD}Sicherheit:${NC} Token und Keyfile niemals committen oder loggen."
echo ""
