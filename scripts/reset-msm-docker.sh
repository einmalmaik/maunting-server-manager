#!/usr/bin/env bash
set -euo pipefail

# reset-msm-docker.sh
# Safe recovery script for rootless Docker corruption on MSM prod deploys.
# 
# Typical cause: accidental `git clean -fd` in /opt/msm (the git root on many prod servers)
# which deletes untracked files inside $HOME/.local/share/docker for the msm user.
# Symptom: "failed to lease content: ... blob not found" when pulling SteamCMD images
# for server install/reinstall.
#
# Usage (as root):
#   sudo bash scripts/reset-msm-docker.sh
#
# This will:
# - Stop the rootless docker for msm
# - Nuke the (corrupted) docker data dir for msm
# - Restart the user service (re-inits if needed)
# - Re-pull the recommended base images as the msm user
#
# After this, MSM should be able to pull/use images again for new servers and reinstalls.
# Game data in /opt/msm/servers/* is untouched (bind mounts).
#
# Run `git pull` first to have latest code + .gitignore protections.
#
# This script is the "official" version of what saved the day at 2am.
# Never run git clean -fd on prod again. Use --dry-run. xD

MSM_USER="msm"
MSM_DIR="/opt/msm"

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo bash $0)"
  exit 1
fi

MSM_UID=$(id -u "$MSM_USER" 2>/dev/null || echo "994")
MSM_DOCKER_HOST="unix:///run/user/${MSM_UID}/docker.sock"
XDG_RUNTIME_DIR="/run/user/${MSM_UID}"

echo "=== MSM Rootless Docker Reset for user ${MSM_USER} (uid ${MSM_UID}) ==="
echo "MSM_DIR=${MSM_DIR}"
echo "This will WIPE all Docker images/containers for the msm user (game data is safe in bind mounts)."
read -p "Continue? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 1
fi

echo "[1/5] Stopping rootless docker for ${MSM_USER}..."
sudo -u "$MSM_USER" bash -c "
  export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}
  systemctl --user stop docker || true
" || true

pkill -u "${MSM_UID}" dockerd 2>/dev/null || true
sleep 2
echo "Stopped."

echo "[2/5] Removing corrupted Docker data dir (/opt/msm/.local/share/docker)..."
rm -rf "${MSM_DIR}/.local/share/docker"
echo "Wiped."

echo "[3/5] Restarting user docker service (will re-init clean)..."
sudo -u "$MSM_USER" bash -c "
  export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}
  systemctl --user start docker || {
    echo '  Service start failed, re-running rootless setup...'
    dockerd-rootless-setuptool.sh install --skip-iptables || true
    systemctl --user enable --now docker || true
  }
  sleep 5
" || true
echo "Service (re)started."

echo "[4/5] Pulling base images as ${MSM_USER} with correct DOCKER_HOST..."
sudo -u "$MSM_USER" bash -c "
  export DOCKER_HOST=${MSM_DOCKER_HOST}
  echo '  Pulling ghcr.io/parkervcp/steamcmd:debian ...'
  docker pull ghcr.io/parkervcp/steamcmd:debian || echo '  (may be retried by MSM later)'
  echo '  Pulling cm2network/steamcmd:root (legacy fallback) ...'
  docker pull cm2network/steamcmd:root || echo '  (legacy, may fail if deprecated)'
" || true
echo "Pulls attempted."

echo "[5/5] Verification (as ${MSM_USER}):"
sudo -u "$MSM_USER" bash -c "
  export DOCKER_HOST=${MSM_DOCKER_HOST}
  echo '=== Current images for msm ==='
  docker images | cat || true
"

echo ""
echo "=== Done ==="
echo "Now run on the server:"
echo "  cd /opt/msm && git pull"
echo "  sudo systemctl restart msm-panel"
echo ""
echo "New server installs and reinstalls should work again."
echo "If you still see blob errors in MSM logs, the improved error message now contains these exact steps."
echo ""
echo "Lesson learned: NEVER git clean -fd on a prod deploy dir. --dry-run first. Always."
echo "(This exact scenario once happened to the maintainer at 2am. You're not alone. xD)"
