#!/usr/bin/env bash
# Creates the weather/observatory/imaging/network/starlink buckets (the "power" bucket is
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
# Weather/power/observatory telemetry is capped at 2 years; imaging metadata
# (frame stats, HFR, guiding, etc.) is comparatively low-volume and stays
# useful indefinitely for long-term equipment/seeing analysis, so it's kept
# forever. Network ping health is kept much shorter (90d) - it's an
# operational signal you care about recently, not a dataset worth 2 years of
# storage. Starlink power/alert history gets a middle-ground 180d - power
# draw trends and alert patterns are worth looking back on longer than raw
# ping health, but it's still not a session log like imaging. Adjust freely -
# this only sets retention at creation time, use `influx bucket update` to
# change it later. (The "power" bucket itself is created separately, at
# first-run setup, via docker-compose.yml's
# DOCKER_INFLUXDB_INIT_BUCKET/DOCKER_INFLUXDB_INIT_RETENTION.)
BUCKETS=(
  "${INFLUXDB_BUCKET_WEATHER}:730d"
  "${INFLUXDB_BUCKET_OBSERVATORY}:730d"
  "${INFLUXDB_BUCKET_IMAGING}:0"
  "${INFLUXDB_BUCKET_NETWORK}:90d"
  "${INFLUXDB_BUCKET_STARLINK}:180d"
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
