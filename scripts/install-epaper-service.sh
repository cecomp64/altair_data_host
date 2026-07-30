#!/usr/bin/env bash
# Installs and starts the epaper-dashboard systemd unit on the Docker host.
# Docker-aware: warns if the influxdb container isn't up yet, since the
# e-paper script queries it directly over the host network.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${REPO_DIR}/scripts/epaper-dashboard.service.template"
UNIT_DEST="/etc/systemd/system/epaper-dashboard.service"
SERVICE_USER="${SUDO_USER:-${USER}}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "systemd services only apply on Linux hosts; skipping on $(uname -s)." >&2
  exit 0
fi

if [[ $EUID -ne 0 ]]; then
  echo "Re-running with sudo (writes to /etc/systemd/system)..." >&2
  exec sudo --preserve-env=SUDO_USER "$0" "$@"
fi

if [[ ! -f "${REPO_DIR}/.env" ]]; then
  echo "Missing ${REPO_DIR}/.env - run scripts/deploy.sh first (or copy .env.example) so the service has InfluxDB credentials." >&2
  exit 1
fi

if command -v docker >/dev/null 2>&1; then
  if ! docker compose -f "${REPO_DIR}/docker-compose.yml" --project-directory "${REPO_DIR}" ps --status running influxdb 2>/dev/null | grep -q influxdb; then
    echo "Warning: influxdb container is not running. Start the stack with 'docker compose up -d' (or scripts/deploy.sh) before relying on the e-paper display." >&2
  fi
fi

sed \
  -e "s#__REPO_DIR__#${REPO_DIR}#g" \
  -e "s#__SERVICE_USER__#${SERVICE_USER}#g" \
  "$TEMPLATE" > "$UNIT_DEST"

systemctl daemon-reload
systemctl enable --now epaper-dashboard.service

echo "epaper-dashboard.service installed and started (user=${SERVICE_USER}, dir=${REPO_DIR})"
systemctl --no-pager status epaper-dashboard.service || true
