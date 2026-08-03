#!/usr/bin/env bash
# Installs host-side deps for scripts/charge_cutoff.py: a venv at
# .venv-charge-cutoff/ with requirements-charge-cutoff.txt, and gpio-group
# membership for the invoking user (needed for unprivileged GPIO access via
# lgpio). Safe to re-run.
#
# gpiozero's lgpio backend needs the native liblgpio C library. The `lgpio`
# PyPI package ships no prebuilt wheels - pip always compiles it from source,
# which needs that native library present to link against, and there's no
# predictable apt dev-package providing just the library/headers. Raspberry
# Pi OS instead ships a precompiled `python3-lgpio` (bindings + native lib
# together) via apt, so this installs that system package (plus
# python3-gpiozero) and creates the venv with --system-site-packages so
# gpiozero can see them, rather than fighting pip's source build.
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
  sudo apt-get install -y --no-install-recommends \
    python3-venv python3-pip python3-lgpio python3-gpiozero
fi

# Recreate the venv if it exists but wasn't created with --system-site-packages
# (needed to see the apt-installed lgpio/gpiozero above) - e.g. left over
# from before this script used that flag, or from the failed pip-compile
# attempt.
if [[ -d "$VENV_DIR" ]] && ! grep -q "^include-system-site-packages = true$" "${VENV_DIR}/pyvenv.cfg" 2>/dev/null; then
  echo "Recreating ${VENV_DIR} with --system-site-packages access to the apt-installed lgpio/gpiozero."
  rm -rf "$VENV_DIR"
fi
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv --system-site-packages "$VENV_DIR"
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
