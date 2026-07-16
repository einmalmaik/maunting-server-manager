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
apt-get install -y -qq ca-certificates curl git python3 >/dev/null

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

# Prefer the tested control-plane artifact. Git remains a compatibility
# fallback for repositories that do not have a normal v* release yet.
release_json=$(curl -fsSL \
    https://api.github.com/repos/einmalmaik/maunting-server-manager/releases/latest \
    2>/dev/null || true)
release_tag=""
panel_url=""
checksum_url=""
if [[ -n "$release_json" ]]; then
    readarray -t release_info < <(printf '%s' "$release_json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
tag = data.get("tag_name", "")
expected = f"msm-panel-{tag}.tar.gz"
url = next((asset.get("browser_download_url", "") for asset in data.get("assets", []) if asset.get("name") == expected), "")
checksum_url = next((asset.get("browser_download_url", "") for asset in data.get("assets", []) if asset.get("name") == "SHA256SUMS"), "")
print(tag)
print(url)
print(checksum_url)
' 2>/dev/null || true)
    release_tag="${release_info[0]:-}"
    panel_url="${release_info[1]:-}"
    checksum_url="${release_info[2]:-}"
fi

if [[ "$release_tag" =~ ^v[A-Za-z0-9._-]+$ && -n "$panel_url" && -n "$checksum_url" ]]; then
    echo "[MSM] Lade getestetes Panel-Artefakt $release_tag..."
    panel_asset="msm-panel-$release_tag.tar.gz"
    curl -fsSL "$panel_url" -o "$tmp_dir/$panel_asset"
    curl -fsSL "$checksum_url" -o "$tmp_dir/SHA256SUMS"
    awk -v expected="$panel_asset" \
        '$2 == expected && $1 ~ /^[[:xdigit:]]{64}$/ { print; found=1 } END { exit found ? 0 : 1 }' \
        "$tmp_dir/SHA256SUMS" > "$tmp_dir/panel.sha256" \
        || { echo "Prüfsumme für Panel-Artefakt fehlt oder ist ungültig." >&2; exit 1; }
    (cd "$tmp_dir" && sha256sum --check panel.sha256)
    tar -xzf "$tmp_dir/$panel_asset" -C "$tmp_dir"
    repo_dir="$tmp_dir/mauntingservermanager-$release_tag"
    [[ -f "$repo_dir/install.sh" && -d "$repo_dir/backend" && -d "$repo_dir/frontend" ]] \
        || { echo "Ungültiges Panel-Artefakt." >&2; exit 1; }
else
    echo "[MSM] Noch kein Panel-Release vorhanden; verwende flachen Git-Checkout..."
    repo_dir="$tmp_dir/repo"
    git clone --depth 1 https://github.com/einmalmaik/maunting-server-manager.git "$repo_dir" >/dev/null
fi

exec bash "$repo_dir/install.sh" --simple --domain "$DOMAIN"
