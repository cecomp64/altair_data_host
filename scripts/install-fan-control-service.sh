#!/usr/bin/env bash
# Installs and starts the fan-control systemd unit on the Docker host.
# Docker-aware: warns if the influxdb container isn't up yet, since the
# fan-control script queries it directly over the host network.
#
# Requires FAN_CONTROL_GPIO_PIN to be set in .env, since that's site-wiring
# specific and there's no safe default. If it's not set yet, this skips
# (rather than failing) so it's safe to leave in scripts/deploy.sh before the
# fan hardware is wired - re-run this script (or deploy.sh) once it is.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${REPO_DIR}/scripts/fan-control.service.template"
UNIT_DEST="/etc/systemd/system/fan-control.service"
SERVICE_USER="${SUDO_USER:-${USER}}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "systemd services only apply on Linux hosts; skipping on $(uname -s)." >&2
  exit 0
fi

if [[ ! -f "${REPO_DIR}/.env" ]]; then
  echo "Missing ${REPO_DIR}/.env - run scripts/deploy.sh first (or copy .env.example) so the service has InfluxDB credentials." >&2
  exit 1
fi

if ! grep -q "^FAN_CONTROL_GPIO_PIN=" "${REPO_DIR}/.env" 2>/dev/null; then
  echo "FAN_CONTROL_GPIO_PIN is not set in ${REPO_DIR}/.env - skipping fan-control service install."
  echo "Once the fan/MOSFET is wired, add e.g. 'FAN_CONTROL_GPIO_PIN=26' (BCM numbering, matching your wiring) to .env and re-run this script."
  exit 0
fi

if [[ ! -x "${REPO_DIR}/.venv-fan-control/bin/python3" ]]; then
  echo "Missing ${REPO_DIR}/.venv-fan-control - run scripts/install-fan-control-deps.sh first so the service has its Python deps." >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "Re-running with sudo (writes to /etc/systemd/system)..." >&2
  exec sudo --preserve-env=SUDO_USER "$0" "$@"
fi

if command -v docker >/dev/null 2>&1; then
  if ! docker compose -f "${REPO_DIR}/docker-compose.yml" --project-directory "${REPO_DIR}" ps --status running influxdb 2>/dev/null | grep -q influxdb; then
    echo "Warning: influxdb container is not running. Start the stack with 'docker compose up -d' (or scripts/deploy.sh) before relying on the fan-control service." >&2
  fi
fi

sed \
  -e "s#__REPO_DIR__#${REPO_DIR}#g" \
  -e "s#__SERVICE_USER__#${SERVICE_USER}#g" \
  "$TEMPLATE" > "$UNIT_DEST"

systemctl daemon-reload
systemctl enable --now fan-control.service

echo "fan-control.service installed and started (user=${SERVICE_USER}, dir=${REPO_DIR})"
systemctl --no-pager status fan-control.service || true
