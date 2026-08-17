#!/usr/bin/env bash
# Provisions Discord alerting (contact point, default notification policy,
# 5 alert rules) into Grafana via its API - see init_grafana_alerting.py's
# docstring for why this is a script rather than committed YAML like every
# other piece of Grafana config in this repo (confirmed Grafana bug: env
# var expansion doesn't work in alerting provisioning files). Idempotent -
# safe to re-run any time DISCORD_WEBHOOK_URL or the rules change. Requires
# the grafana container to already be up.
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

if [[ -z "${DISCORD_WEBHOOK_URL:-}" ]]; then
  echo "DISCORD_WEBHOOK_URL not set in .env - skipping Discord alerting setup." >&2
  echo "Create a webhook (Discord: Server Settings > Integrations > Webhooks) and set it to enable this." >&2
  exit 0
fi

export GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"

# grafana has no docker-compose healthcheck (unlike influxdb), so unlike
# init-influx-buckets.sh this can't just assume the service is already up -
# poll briefly before handing off to the API calls.
echo "Waiting for Grafana to become available..."
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null "${GRAFANA_URL}/api/health" 2>/dev/null; then
    break
  fi
  sleep 2
done

python3 "${REPO_DIR}/scripts/init_grafana_alerting.py"
