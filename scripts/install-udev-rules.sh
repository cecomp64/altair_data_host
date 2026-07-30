#!/usr/bin/env bash
# Installs the persistent serial device symlink rules (99-solar-serial.rules)
# so /dev/ttyUSB_VICTRON and /dev/ttyUSB_ECOWORTHY survive reboots and USB
# renumbering. Runs on the Docker host - the telegraf container just bind
# mounts /dev, so it automatically sees whatever symlinks udev creates here.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RULES_SRC="${REPO_DIR}/99-solar-serial.rules"
RULES_DEST="/etc/udev/rules.d/99-solar-serial.rules"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "udev rules only apply on Linux hosts; skipping on $(uname -s)." >&2
  exit 0
fi

if [[ $EUID -ne 0 ]]; then
  echo "Re-running with sudo (writes to /etc/udev/rules.d)..." >&2
  exec sudo "$0" "$@"
fi

if [[ ! -f "$RULES_SRC" ]]; then
  echo "Cannot find $RULES_SRC" >&2
  exit 1
fi

install -m 0644 "$RULES_SRC" "$RULES_DEST"
udevadm control --reload-rules
udevadm trigger

echo "Installed $RULES_DEST"
echo "Verifying symlinks (devices must be plugged in):"
ls -l /dev/ttyUSB_VICTRON /dev/ttyUSB_ECOWORTHY 2>/dev/null || \
  echo "  (not present yet - plug in the USB adapters and re-run, or check 'udevadm monitor')"
