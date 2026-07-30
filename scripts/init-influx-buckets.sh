#!/usr/bin/env bash
# Creates the weather/observatory/imaging buckets (the "power" bucket is
# created automatically by InfluxDB's first-run setup via docker-compose.yml's
# DOCKER_INFLUXDB_INIT_BUCKET). Idempotent - safe to re-run any time the
# bucket/retention list below changes. Requires the influxdb container to
# already be up and healthy.
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

# "name:retention" - retention uses InfluxDB duration syntax, or 0 for infinite.
# Weather/power telemetry is high-frequency but low-value per-point, so it's
# capped; observatory/imaging events are low-volume and high-value, so they're
# kept forever. Adjust freely - this only sets retention at creation time,
# use `influx bucket update` to change it later.
BUCKETS=(
  "${INFLUXDB_BUCKET_WEATHER}:730d"
  "${INFLUXDB_BUCKET_OBSERVATORY}:0"
  "${INFLUXDB_BUCKET_IMAGING}:0"
)

for entry in "${BUCKETS[@]}"; do
  name="${entry%%:*}"
  retention="${entry##*:}"
  if docker compose exec -T influxdb influx bucket list \
      --org "$INFLUXDB_ORG" --token "$INFLUXDB_ADMIN_TOKEN" --hide-headers \
      | awk '{print $2}' | grep -qx "$name"; then
    echo "Bucket '$name' already exists, skipping."
  else
    echo "Creating bucket '$name' (retention: ${retention})..."
    docker compose exec -T influxdb influx bucket create \
      --name "$name" \
      --org "$INFLUXDB_ORG" \
      --token "$INFLUXDB_ADMIN_TOKEN" \
      --retention "$retention"
  fi
done
