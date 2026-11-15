#!/usr/bin/env bash
# Installs and starts the CWOP publisher systemd unit on the Docker host.
# Skips (doesn't fail) until CWOP_ID is set in .env, so it's safe to leave in
# scripts/deploy.sh. Stdlib-only script - no venv needed.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${REPO_DIR}/scripts/cwop-publisher.service.template"
UNIT_DEST="/etc/systemd/system/cwop-publisher.service"
SERVICE_USER="${SUDO_USER:-${USER}}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "systemd services only apply on Linux hosts; skipping on $(uname -s)." >&2
  exit 0
fi

if [[ ! -f "${REPO_DIR}/.env" ]]; then
  echo "Missing ${REPO_DIR}/.env - run scripts/deploy.sh first." >&2
  exit 1
fi

if ! grep -q "^CWOP_ID=." "${REPO_DIR}/.env" 2>/dev/null; then
  echo "CWOP_ID is not set in ${REPO_DIR}/.env - skipping cwop-publisher install."
  echo "Add e.g. 'CWOP_ID=CW1234' (your NOAA-assigned ID) to .env and re-run this script."
  exit 0
fi

if [[ $EUID -ne 0 ]]; then
  echo "Re-running with sudo (writes to /etc/systemd/system)..." >&2
  exec sudo --preserve-env=SUDO_USER "$0" "$@"
fi

sed \
  -e "s#__REPO_DIR__#${REPO_DIR}#g" \
  -e "s#__SERVICE_USER__#${SERVICE_USER}#g" \
  "$TEMPLATE" > "$UNIT_DEST"

systemctl daemon-reload
systemctl enable --now cwop-publisher.service

echo "cwop-publisher.service installed and started (user=${SERVICE_USER}, dir=${REPO_DIR})"
systemctl --no-pager status cwop-publisher.service || true
