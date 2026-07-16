#!/usr/bin/env bash
set -euo pipefail
umask 077

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

PANEL_URL=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --panel)
            PANEL_URL="${2:-}"
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
PANEL_URL="${PANEL_URL%/}"
if [[ ! "$PANEL_URL" =~ ^https:// ]] && [[ ! "$PANEL_URL" =~ ^http://(localhost|127\.0\.0\.1)(:|$) ]]; then
    echo "Die Panel-URL muss HTTPS verwenden." >&2
    exit 1
fi

echo "[MSM] Node wird automatisch eingerichtet..."
apt-get update -qq
apt-get install -y -qq curl jq ca-certificates tar >/dev/null

TMP_DIR=$(mktemp -d /tmp/msm-node-install.XXXXXX)
chmod 700 "$TMP_DIR"
trap 'rm -rf "$TMP_DIR"' EXIT

curl -fsSL "$PANEL_URL/api/nodes/agent-package" -o "$TMP_DIR/agent.tar.gz"
tar -xzf "$TMP_DIR/agent.tar.gz" -C "$TMP_DIR"

MSM_AGENT_ENROLLMENT=true bash "$TMP_DIR/scripts/install-agent.sh"

AGENT_ENV="/opt/msm-agent/.env"
CERT_FILE="/opt/msm-agent/certs/agent.crt"
[[ -f "$AGENT_ENV" && -f "$CERT_FILE" ]] || {
    echo "Agent-Konfiguration wurde nicht vollständig erstellt." >&2
    exit 1
}

TOKEN=$(sed -n 's/^MSM_AGENT_TOKEN=//p' "$AGENT_ENV" | head -1 | sed 's/^"//;s/"$//')
[[ ${#TOKEN} -ge 32 ]] || {
    echo "Agent-Token fehlt." >&2
    exit 1
}
FINGERPRINT=$(openssl x509 -in "$CERT_FILE" -outform DER 2>/dev/null | sha256sum | awk '{print $1}')
NODE_NAME=$(hostname -s 2>/dev/null | tr -cd 'A-Za-z0-9_.-' | cut -c1-100)
NODE_NAME="${NODE_NAME:-MSM-Node}"

TOKEN_FILE="$TMP_DIR/token"
PAYLOAD_FILE="$TMP_DIR/enrollment.json"
BEGIN_RESPONSE="$TMP_DIR/begin.json"
printf '%s' "$TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"
jq -n \
    --rawfile token "$TOKEN_FILE" \
    --arg name "$NODE_NAME" \
    --arg fingerprint "$FINGERPRINT" \
    '{name:$name, agent_token:$token, tls_fingerprint:$fingerprint, port:9000}' \
    > "$PAYLOAD_FILE"
chmod 600 "$PAYLOAD_FILE"

curl -fsS \
    -H 'Content-Type: application/json' \
    --data-binary "@$PAYLOAD_FILE" \
    "$PANEL_URL/api/nodes/enrollments/begin" \
    -o "$BEGIN_RESPONSE"

CLAIM=$(jq -r '.claim_secret // empty' "$BEGIN_RESPONSE")
DISPLAY_CODE=$(jq -r '.display_code // empty' "$BEGIN_RESPONSE")
[[ ${#CLAIM} -ge 32 ]] || {
    echo "Das Panel hat keine gültige Enrollment-Antwort geliefert." >&2
    exit 1
}

CLAIM_CONFIG="$TMP_DIR/claim.curl"
printf 'header = "Authorization: Bearer %s"\n' "$CLAIM" > "$CLAIM_CONFIG"
chmod 600 "$CLAIM_CONFIG"
unset TOKEN CLAIM
rm -f "$TOKEN_FILE" "$PAYLOAD_FILE" "$BEGIN_RESPONSE"

echo "[MSM] Node erkannt (${DISPLAY_CODE}). Bitte im Panel einmal bestätigen."
POLL_RESPONSE="$TMP_DIR/poll.json"
for _attempt in $(seq 1 180); do
    if curl -fsS --config "$CLAIM_CONFIG" \
        -X POST "$PANEL_URL/api/nodes/enrollments/poll" \
        -o "$POLL_RESPONSE" 2>/dev/null; then
        STATUS=$(jq -r '.status // empty' "$POLL_RESPONSE")
        if [[ "$STATUS" == "approved" || "$STATUS" == "claimed" ]]; then
            curl -fsS --max-time 3 https://127.0.0.1:9000/health \
                --insecure >/dev/null 2>&1 \
                || systemctl restart msm-agent.service
            echo "[OK] Node ist eingerichtet und mit dem Panel verbunden."
            exit 0
        fi
    fi
    sleep 5
done

echo "Die Bestätigung ist abgelaufen. Starte den Installationsbefehl erneut." >&2
exit 1
