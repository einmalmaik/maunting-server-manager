#!/usr/bin/env bash
set -euo pipefail

# Public one-line entrypoint. Fresh installs need only a domain; every package,
# PostgreSQL, Redis, rootless Docker, agent, service and TLS step is delegated
# to the repository's reviewed installer.

DOMAIN=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)
            DOMAIN="${2:-}"
            shift 2
            ;;
        *)
            echo "Unbekannte Option: $1" >&2
            exit 2
            ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "Bitte mit sudo ausführen." >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git >/dev/null

if [[ -f /opt/msm/backend/.env ]]; then
    echo "[MSM] Bestehende Installation erkannt. Sicherer Updater wird gestartet..."
    updater=$(mktemp /tmp/msm-bootstrap-update.XXXXXX.sh)
    trap 'rm -f "$updater"' EXIT
    curl -fsSL \
        https://raw.githubusercontent.com/einmalmaik/maunting-server-manager/main/update.sh \
        -o "$updater"
    chmod 700 "$updater"
    bash -n "$updater"
    exec bash "$updater" --force
fi

if [[ -z "$DOMAIN" ]]; then
    echo "Domain fehlt. Beispiel:" >&2
    echo "curl -fsSL https://raw.githubusercontent.com/einmalmaik/maunting-server-manager/main/scripts/bootstrap.sh | sudo bash -s -- --domain panel.example.com" >&2
    exit 2
fi

tmp_dir=$(mktemp -d /tmp/msm-install.XXXXXX)
trap 'rm -rf "$tmp_dir"' EXIT
git clone --depth 1 https://github.com/einmalmaik/maunting-server-manager.git "$tmp_dir/repo" >/dev/null
exec bash "$tmp_dir/repo/install.sh" --simple --domain "$DOMAIN"
