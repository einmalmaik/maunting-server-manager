#!/usr/bin/env bash
set -euo pipefail

# Build the three public deployment units from the monorepo. Only tracked files
# are copied into runtime archives; local .env files, caches and server data can
# therefore never leak into a release artifact.

VERSION="${1:-}"
OUTPUT_DIR="${2:-dist-release}"

if [[ ! "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "Version fehlt oder enthält ungültige Zeichen." >&2
    exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$(mkdir -p "$OUTPUT_DIR" && cd "$OUTPUT_DIR" && pwd)"
WORK_DIR="$(mktemp -d /tmp/msm-release.XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

cd "$ROOT_DIR"
[[ -f frontend/dist/index.html ]] || {
    echo "frontend/dist fehlt. Zuerst 'npm run build' in frontend/ ausführen." >&2
    exit 1
}

archive_tracked() {
    local destination="$1"
    shift
    mkdir -p "$destination"
    git archive --format=tar HEAD -- "$@" | tar -xf - -C "$destination"
}

PANEL_ROOT="$WORK_DIR/mauntingservermanager-$VERSION"
archive_tracked "$PANEL_ROOT" \
    README.md Caddyfile.template install.sh update.sh \
    msm-update.service msm-update.timer msm.service.template \
    backend blueprints dis-sidecar docs frontend msm-agent scripts
rm -rf "$PANEL_ROOT/backend/tests" "$PANEL_ROOT/msm-agent/tests"
rm -f "$PANEL_ROOT/dis-sidecar/test-backup-endpoints.mjs" \
    "$PANEL_ROOT/dis-sidecar/_isolated_test_deferred.mjs"
rm -rf "$PANEL_ROOT/frontend/dist"
mkdir -p "$PANEL_ROOT/frontend/dist"
cp -a frontend/dist/. "$PANEL_ROOT/frontend/dist/"

FRONTEND_ROOT="$WORK_DIR/msm-frontend-$VERSION"
mkdir -p "$FRONTEND_ROOT/dist"
cp -a frontend/dist/. "$FRONTEND_ROOT/dist/"
cp frontend/.env.example "$FRONTEND_ROOT/.env.example"

AGENT_ROOT="$WORK_DIR/msm-agent-$VERSION"
archive_tracked "$AGENT_ROOT" msm-agent scripts/install-agent.sh
rm -rf "$AGENT_ROOT/msm-agent/tests"

tar -czf "$OUTPUT_DIR/msm-panel-$VERSION.tar.gz" -C "$WORK_DIR" "mauntingservermanager-$VERSION"
tar -czf "$OUTPUT_DIR/msm-frontend-$VERSION.tar.gz" -C "$WORK_DIR" "msm-frontend-$VERSION"
tar -czf "$OUTPUT_DIR/msm-agent-$VERSION.tar.gz" -C "$WORK_DIR" "msm-agent-$VERSION"

(
    cd "$OUTPUT_DIR"
    sha256sum \
        "msm-panel-$VERSION.tar.gz" \
        "msm-frontend-$VERSION.tar.gz" \
        "msm-agent-$VERSION.tar.gz" \
        > SHA256SUMS
)

echo "Release-Artefakte erstellt: $OUTPUT_DIR"
