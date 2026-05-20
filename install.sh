#!/bin/bash
# Maunting Server Manager — One-Click Installer Wrapper
# Führt panel/install.py aus mit Python 3

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}Maunting Server Manager Installer${NC}"
echo "================================"

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Warnung: Nicht als root ausgeführt.${NC}"
  echo "Für MariaDB- und Systemd-Installation wird root empfohlen."
  echo ""
fi

PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &> /dev/null; then
    PYTHON="$cmd"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo -e "${RED}Fehler: Python 3 nicht gefunden.${NC}"
  echo "Installiere Python 3.11+ (z.B. apt install python3 python3-venv python3-pip)"
  exit 1
fi

PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "${GREEN}✓${NC} Python $PY_VER gefunden"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

$PYTHON "$SCRIPT_DIR/panel/install.py" "$@"
