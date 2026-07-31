#!/usr/bin/env bash
# Installs everything scripts/epaper_dashboard.py needs on the host: enables
# the SPI interface, creates a venv at .venv/ with requirements-epaper.txt,
# and vendors + installs Waveshare's waveshare_epd driver (not on PyPI) from
# https://github.com/waveshare/e-Paper into that venv. Safe to re-run.
#
# On a Raspberry Pi 5, the legacy RPi.GPIO library the Waveshare driver
# depends on doesn't work (Pi 5 replaced the old GPIO block with the RP1
# southbridge) - this script detects Pi 5 and swaps in rpi-lgpio, a drop-in
# replacement that exposes the same RPi.GPIO API on top of lgpio.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "E-paper host deps only apply on Linux hosts; skipping on $(uname -s)." >&2
  exit 0
fi

VENV_DIR="${REPO_DIR}/.venv"
VENDOR_DIR="${REPO_DIR}/vendor/waveshare-epaper"
WAVESHARE_PYTHON_DIR="${VENDOR_DIR}/RaspberryPi_JetsonNano/python"

# 1. Enable SPI (needed for the e-Paper HAT). raspi-config is Raspberry Pi OS
# specific; on other distros/boards, skip with instructions instead of failing.
# /dev/spidev0.0 only appears once the overlay has actually loaded, which
# requires a reboot after the config file is first changed - so its presence
# beforehand is a reliable "no reboot needed" signal, regardless of exactly
# how raspi-config reports current state.
if command -v raspi-config >/dev/null 2>&1; then
  if [[ -e /dev/spidev0.0 ]]; then
    echo "SPI already enabled."
  else
    NEEDS_REBOOT=1
  fi
  sudo raspi-config nonint do_spi 0
  if [[ "${NEEDS_REBOOT:-0}" -eq 1 ]]; then
    echo "SPI was just enabled - a REBOOT is required before the e-paper display will work."
  fi
else
  echo "raspi-config not found - if this isn't a Raspberry Pi OS host, enable SPI manually" \
       "(e.g. dtparam=spi=on in /boot/firmware/config.txt) before starting the e-paper service." >&2
fi

# 2. System packages needed to create the venv and clone the driver repo.
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends python3-venv python3-pip git
fi

# 3. Python venv with requirements-epaper.txt.
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${REPO_DIR}/scripts/requirements-epaper.txt"

# 4. Vendor Waveshare's e-Paper repo (shallow clone/update) and install the
# waveshare_epd Python package from it.
if [[ ! -d "$VENDOR_DIR/.git" ]]; then
  git clone --depth 1 https://github.com/waveshare/e-Paper.git "$VENDOR_DIR"
else
  git -C "$VENDOR_DIR" fetch --depth 1 origin
  git -C "$VENDOR_DIR" reset --hard origin/HEAD
fi
"${VENV_DIR}/bin/pip" install "$WAVESHARE_PYTHON_DIR"

# 5. Raspberry Pi 5 GPIO compatibility: swap RPi.GPIO for rpi-lgpio.
if grep -q "Raspberry Pi 5" /proc/device-tree/model 2>/dev/null; then
  echo "Raspberry Pi 5 detected - swapping RPi.GPIO for rpi-lgpio."
  "${VENV_DIR}/bin/pip" uninstall -y RPi.GPIO || true
  "${VENV_DIR}/bin/pip" install rpi-lgpio
fi

echo
echo "E-paper host dependencies installed into ${VENV_DIR}."
if [[ "${NEEDS_REBOOT:-0}" -eq 1 ]]; then
  echo "Reboot before starting the epaper-dashboard service so the SPI overlay loads."
fi
