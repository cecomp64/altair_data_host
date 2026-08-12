#!/usr/bin/env bash
# Snapshots the *live* Grafana dashboard (including any edits made in the UI)
# into provisioning/dashboards/solar-observatory.json, backing up whatever
# was previously committed there first.
#
# Grafana's file-based dashboard provider mounts provisioning/ read-only, so
# UI edits (allowUiUpdates: true) only ever land in Grafana's own database,
# never back into this file. They survive there only until the committed
# JSON's content next changes - at that point Grafana treats the file as
# authoritative and silently overwrites the UI-made version. Run this script
# BEFORE hand-editing the dashboard JSON, so you're editing on top of
# whatever's actually live (including prior UI tweaks) instead of clobbering
# them. See README §9.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [[ ! -f .env ]]; then
  echo ".env not found - run scripts/deploy.sh first (or copy .env.example)." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
source .env
set +a

GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
DASHBOARD_UID="solar-observatory"
DEST="grafana/provisioning/dashboards/solar-observatory.json"
BACKUP_DIR="grafana/dashboard_backups"
KEEP=5

mkdir -p "$BACKUP_DIR"

echo "Fetching live dashboard from ${GRAFANA_URL}..."
curl -fsS -u "${GRAFANA_ADMIN_USER}:${GRAFANA_ADMIN_PASSWORD}" \
  "${GRAFANA_URL}/api/dashboards/uid/${DASHBOARD_UID}" \
  | python3 -c '
import json, sys
dash = json.load(sys.stdin)["dashboard"]
# Provisioning assigns its own id on load; a stale one just adds JSON-diff
# noise across snapshots for no benefit.
dash["id"] = None
json.dump(dash, sys.stdout, indent=2)
sys.stdout.write("\n")
' > "${DEST}.new"

if [[ -f "$DEST" ]] && ! cmp -s "$DEST" "${DEST}.new"; then
  timestamp="$(date +%Y%m%d-%H%M%S)"
  backup="${BACKUP_DIR}/solar-observatory.${timestamp}.json"
  cp "$DEST" "$backup"
  echo "Backed up previous version to ${backup}"
  # Keep only the most recent $KEEP backups.
  ls -1t "${BACKUP_DIR}"/solar-observatory.*.json 2>/dev/null | tail -n "+$((KEEP + 1))" | xargs -r rm --
fi

mv "${DEST}.new" "$DEST"
echo "Wrote ${DEST}"
echo "Now hand-edit this file. Grafana re-polls provisioning/ every 30s (dashboards.yml), no restart needed."
