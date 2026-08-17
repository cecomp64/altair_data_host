#!/usr/bin/env bash
# Installs this repo's udev rules:
#   99-solar-serial.rules - persistent /dev/ttyUSB_VICTRON/_ECOWORTHY
#     symlinks so they survive reboots and USB renumbering.
#   99-vcio-telegraf.rules - loosens /dev/vcio's permissions so the
#     unprivileged telegraf process user can call vcgencmd (Pi power health).
# Runs on the Docker host - the telegraf container just bind mounts /dev, so
# it automatically sees whatever permissions/symlinks udev sets here.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RULES_FILES=("99-solar-serial.rules" "99-vcio-telegraf.rules")

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "udev rules only apply on Linux hosts; skipping on $(uname -s)." >&2
  exit 0
fi

if [[ $EUID -ne 0 ]]; then
  echo "Re-running with sudo (writes to /etc/udev/rules.d)..." >&2
  exec sudo "$0" "$@"
fi

for rules_file in "${RULES_FILES[@]}"; do
  src="${REPO_DIR}/${rules_file}"
  if [[ ! -f "$src" ]]; then
    echo "Cannot find $src" >&2
    exit 1
  fi
  install -m 0644 "$src" "/etc/udev/rules.d/${rules_file}"
  echo "Installed /etc/udev/rules.d/${rules_file}"
done

udevadm control --reload-rules
udevadm trigger
udevadm settle

echo "Verifying symlinks (devices must be plugged in):"
ls -l /dev/ttyUSB_VICTRON /dev/ttyUSB_ECOWORTHY 2>/dev/null || \
  echo "  (not present yet - plug in the USB adapters and re-run, or check 'udevadm monitor')"
echo "Verifying /dev/vcio permissions:"
ls -l /dev/vcio 2>/dev/null || echo "  (not present - not Raspberry Pi hardware?)"
