#!/usr/bin/env bash
# sync-venv.sh — synchronisiert das MSM-Backend-venv mit backend/requirements.txt.
#
# Idempotenter Wrapper um ``pip install -r requirements.txt``. Sinnvoll als
# Post-Deploy-Step, weil Code-Pushes neue Pakete einführen koennen, ohne dass
# ``update.sh`` (das nur bei vollen MSM-Updates laeuft) zwingend aufgerufen wird.
#
# Typischer Use-Case: nach ``git pull`` ein ``bash scripts/sync-venv.sh`` als root,
# und die venv hat alles, was der neue Code erwartet. Ohne diesen Step wirft
# der Service zur Laufzeit ``ModuleNotFoundError`` fuer neu hinzugefuegte
# Pakete (z.B. boto3 nach Backup-System M1).
#
# Usage:
#   sudo bash scripts/sync-venv.sh             # installiert alle Pakete (Default)
#   sudo bash scripts/sync-venv.sh --check     # nur pruefen, nichts installieren (dry-run)
#
# Exit-Codes:
#   0 = alles aktuell oder erfolgreich installiert
#   1 = venv fehlt (Hinweis: ``install.sh`` ausfuehren)
#   2 = pip-install fehlgeschlagen
#   3 = --check-only und Pakete sind veraltet
set -euo pipefail

MSM_USER="msm"
MSM_DIR="/opt/msm"
BACKEND_DIR="${MSM_DIR}/backend"
REQUIREMENTS="${BACKEND_DIR}/requirements.txt"
DEV_REQUIREMENTS="${BACKEND_DIR}/dev-requirements.txt"
VENV_DIR="${BACKEND_DIR}/venv"

# Farben (gleich wie install.sh fuer visuelles Consistency)
if [[ -t 1 ]]; then
    CYAN='\033[0;36m'
    YELLOW='\033[1;33m'
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    NC='\033[0m'
else
    CYAN=''; YELLOW=''; RED=''; GREEN=''; NC=''
fi

log()  { echo -e "${CYAN}[sync-venv]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERROR]${NC} $1" >&2; }
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }

# Argumente parsen
CHECK_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --check|--check-only|--dry-run) CHECK_ONLY=1 ;;
        -h|--help)
            sed -n '2,25p' "$0"
            exit 0
            ;;
        *)
            err "Unbekannte Option: $arg"
            sed -n '2,25p' "$0"
            exit 1
            ;;
    esac
done

# Root-Check: pip muss in die venv schreiben koennen. msm-User waere sicherer,
# aber root ist hier OK weil die venv ohnehin msm:msm-geowned ist und wir
# nichts am Owner drehen. Konsistent mit update.sh.
if [[ $EUID -ne 0 ]]; then
    err "Please run as root (sudo bash $0)"
    exit 1
fi

# Venv muss existieren (von install.sh angelegt)
if [[ ! -d "$VENV_DIR" ]]; then
    err "venv nicht gefunden: $VENV_DIR"
    err "Bitte zuerst install.sh ausfuehren."
    exit 1
fi

if [[ ! -f "$REQUIREMENTS" ]]; then
    err "requirements.txt nicht gefunden: $REQUIREMENTS"
    exit 1
fi

# Snapshot der aktuell installierten Pakete (Top-Level). Wird fuer den Diff
# benutzt, damit der Output lesbar bleibt ("installierte 2 neue Pakete: X, Y").
log "Lese aktuell installierte Pakete..."
INSTALLED_BEFORE=$(sudo -u "$MSM_USER" \
    "${VENV_DIR}/bin/pip" list --format=freeze 2>/dev/null \
    | grep -E '^[^# ].*==.*' || true)
INSTALLED_BEFORE_COUNT=$(echo "$INSTALLED_BEFORE" | grep -c '=' || echo 0)

log "Aktuell installiert: ${INSTALLED_BEFORE_COUNT} Pakete"

# Wenn --check-only: vergleiche ohne Installation
if [[ $CHECK_ONLY -eq 1 ]]; then
    log "Pruefe requirements.txt vs venv (kein Install)..."
    if sudo -u "$MSM_USER" \
        "${VENV_DIR}/bin/pip" check 2>&1 \
        | grep -qE "No broken requirements"; then
        ok "venv ist aktuell (pip check OK)"
        exit 0
    fi

    # pip check ist nicht perfekt fuer veraltete Versionen. Stattdessen
    # machen wir einen echten Dry-Run mit --dry-run und werten aus.
    if sudo -u "$MSM_USER" \
        "${VENV_DIR}/bin/pip" install \
            --requirement "$REQUIREMENTS" \
            --dry-run 2>&1 \
        | grep -qE "Would install|Installing"; then
        warn "venv ist NICHT aktuell. Bitte sync-venv.sh ohne --check ausfuehren."
        exit 3
    fi
    ok "venv ist aktuell"
    exit 0
fi

# Eigentliche Installation
log "Aktualisiere pip..."
sudo -u "$MSM_USER" "${VENV_DIR}/bin/pip" install --upgrade pip --quiet 2>&1 \
    || { err "pip self-update fehlgeschlagen"; exit 2; }

log "Installiere Pakete aus requirements.txt..."
# --upgrade: bestehende Pakete werden auf die in requirements.txt gepinnten
# Versionen aktualisiert. --quiet: nur Errors zeigen (wir loggen das Ergebnis
# danach separat).
set +e
INSTALL_OUTPUT=$(sudo -u "$MSM_USER" \
    "${VENV_DIR}/bin/pip" install \
        --requirement "$REQUIREMENTS" \
        --upgrade 2>&1)
INSTALL_EXIT=$?
set -e

if [[ $INSTALL_EXIT -ne 0 ]]; then
    err "pip install requirements.txt fehlgeschlagen (Exit $INSTALL_EXIT):"
    echo "$INSTALL_OUTPUT" | tail -20 | sed 's/^/  /' >&2
    exit 2
fi

# dev-requirements.txt NUR installieren wenn vorhanden — sie sind optional
# und nur fuer Tests noetig (moto[s3]).
if [[ -f "$DEV_REQUIREMENTS" ]]; then
    log "Installiere dev-requirements.txt (test dependencies)..."
    sudo -u "$MSM_USER" \
        "${VENV_DIR}/bin/pip" install \
            --requirement "$DEV_REQUIREMENTS" \
            --upgrade 2>&1 \
        || warn "dev-requirements.txt install teilweise fehlgeschlagen (Tests evtl. beeintraechtigt, Production-Runtime OK)"
fi

# Diff: was hat sich geaendert?
INSTALLED_AFTER=$(sudo -u "$MSM_USER" \
    "${VENV_DIR}/bin/pip" list --format=freeze 2>/dev/null \
    | grep -E '^[^# ].*==.*' || true)
INSTALLED_AFTER_COUNT=$(echo "$INSTALLED_AFTER" | grep -c '=' || echo 0)

ADDED=$(diff <(echo "$INSTALLED_BEFORE") <(echo "$INSTALLED_AFTER") \
    | grep '^> ' | sed 's/^> //' || true)
REMOVED=$(diff <(echo "$INSTALLED_BEFORE") <(echo "$INSTALLED_AFTER") \
    | grep '^< ' | sed 's/^< //' || true)

if [[ -n "$ADDED" ]]; then
    ok "Hinzugefuegt/aktualisiert ($(echo "$ADDED" | wc -l) Pakete):"
    echo "$ADDED" | head -20 | sed 's/^/  + /'
    if [[ $(echo "$ADDED" | wc -l) -gt 20 ]]; then
        echo "  ... und $(($(echo "$ADDED" | wc -l) - 20)) weitere"
    fi
fi

if [[ -n "$REMOVED" ]]; then
    warn "Entfernt ($(echo "$REMOVED" | wc -l) Pakete):"
    echo "$REMOVED" | head -10 | sed 's/^/  - /'
fi

if [[ -z "$ADDED" && -z "$REMOVED" ]]; then
    ok "venv bereits aktuell — keine Aenderungen."
fi

ok "Sync abgeschlossen. venv: ${INSTALLED_BEFORE_COUNT} -> ${INSTALLED_AFTER_COUNT} Pakete."