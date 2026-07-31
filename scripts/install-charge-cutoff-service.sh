#!/usr/bin/env bash
# Installs and starts the charge-cutoff systemd unit on the Docker host.
# Docker-aware: warns if the influxdb container isn't up yet, since the
# charge-cutoff script queries it directly over the host network.
#
# Requires CHARGE_CUTOFF_GPIO_PIN to be set in .env, since that's site-wiring
# specific and there's no safe default. If it's not set yet, this skips
# (rather than failing) so it's safe to leave in scripts/deploy.sh before the
# relay hardware is built - re-run this script (or deploy.sh) once it is.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${REPO_DIR}/scripts/charge-cutoff.service.template"
UNIT_DEST="/etc/systemd/system/charge-cutoff.service"
SERVICE_USER="${SUDO_USER:-${USER}}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "systemd services only apply on Linux hosts; skipping on $(uname -s)." >&2
  exit 0
fi

if [[ ! -f "${REPO_DIR}/.env" ]]; then
  echo "Missing ${REPO_DIR}/.env - run scripts/deploy.sh first (or copy .env.example) so the service has InfluxDB credentials." >&2
  exit 1
fi

if ! grep -q "^CHARGE_CUTOFF_GPIO_PIN=" "${REPO_DIR}/.env" 2>/dev/null; then
  echo "CHARGE_CUTOFF_GPIO_PIN is not set in ${REPO_DIR}/.env - skipping charge-cutoff service install."
  echo "Once the relay hardware is wired, add e.g. 'CHARGE_CUTOFF_GPIO_PIN=27' (BCM numbering, matching your wiring) to .env and re-run this script."
  exit 0
fi

if [[ ! -x "${REPO_DIR}/.venv-charge-cutoff/bin/python3" ]]; then
  echo "Missing ${REPO_DIR}/.venv-charge-cutoff - run scripts/install-charge-cutoff-deps.sh first so the service has its Python deps." >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "Re-running with sudo (writes to /etc/systemd/system)..." >&2
  exec sudo --preserve-env=SUDO_USER "$0" "$@"
fi

if command -v docker >/dev/null 2>&1; then
  if ! docker compose -f "${REPO_DIR}/docker-compose.yml" --project-directory "${REPO_DIR}" ps --status running influxdb 2>/dev/null | grep -q influxdb; then
    echo "Warning: influxdb container is not running. Start the stack with 'docker compose up -d' (or scripts/deploy.sh) before relying on the charge-cutoff service." >&2
  fi
fi

sed \
  -e "s#__REPO_DIR__#${REPO_DIR}#g" \
  -e "s#__SERVICE_USER__#${SERVICE_USER}#g" \
  "$TEMPLATE" > "$UNIT_DEST"

systemctl daemon-reload
systemctl enable --now charge-cutoff.service

echo "charge-cutoff.service installed and started (user=${SERVICE_USER}, dir=${REPO_DIR})"
systemctl --no-pager status charge-cutoff.service || true
