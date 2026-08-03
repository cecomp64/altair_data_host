#!/usr/bin/env bash
# Installs host-side deps for scripts/charge_cutoff.py: a venv at
# .venv-charge-cutoff/ with requirements-charge-cutoff.txt, and gpio-group
# membership for the invoking user (needed for unprivileged GPIO access via
# lgpio). Safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Charge-cutoff host deps only apply on Linux hosts; skipping on $(uname -s)." >&2
  exit 0
fi

VENV_DIR="${REPO_DIR}/.venv-charge-cutoff"
SERVICE_USER="${SUDO_USER:-${USER}}"

if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  # swig + build-essential + python3-dev: lgpio has no prebuilt arm64 wheel
  # on PyPI, so pip compiles its _lgpio C extension from source.
  sudo apt-get install -y --no-install-recommends \
    python3-venv python3-pip python3-dev build-essential swig
fi

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/scripts/requirements-charge-cutoff.txt"

if command -v getent >/dev/null 2>&1 && getent group gpio >/dev/null 2>&1; then
  if ! id -nG "$SERVICE_USER" | grep -qw gpio; then
    echo "Adding ${SERVICE_USER} to the gpio group (needed for unprivileged GPIO access)."
    sudo usermod -aG gpio "$SERVICE_USER"
    echo "Log out/in (or reboot) before starting the charge-cutoff service, for group membership to take effect."
  fi
fi

echo
echo "Charge-cutoff host dependencies installed into ${VENV_DIR}."
