#!/usr/bin/env bash
# DEV/DEMO ONLY: generates synthetic history for all four buckets so you can
# see what the Grafana dashboard looks like without any real hardware
# attached. Brings up influxdb+grafana (not telegraf - it's not needed here
# and its privileged/`/dev`-mounted container is more host access than a demo
# needs), creates buckets if missing, then loads generated line protocol via
# `docker compose exec influxdb influx write`. Safe to re-run - each run adds
# another overlapping batch of points at current timestamps, which is harmless
# for a demo but not something to do repeatedly against a real deployment.
#
# Extra args are forwarded to generate_mock_data.py, e.g.:
#   scripts/seed-mock-data.sh --hours 72
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

echo "Bringing up influxdb + grafana (not telegraf - not needed for mock data)..."
docker compose up -d influxdb grafana

echo "Waiting for InfluxDB to become healthy..."
health="starting"
for _ in $(seq 1 30); do
  health="$(docker inspect --format '{{.State.Health.Status}}' influxdb 2>/dev/null || echo starting)"
  [[ "$health" == "healthy" ]] && break
  sleep 2
done
if [[ "$health" != "healthy" ]]; then
  echo "InfluxDB did not become healthy in time; check 'docker compose logs influxdb'" >&2
  exit 1
fi

"${REPO_DIR}/scripts/init-influx-buckets.sh"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Generating mock data..."
python3 "${REPO_DIR}/scripts/dev/generate_mock_data.py" --out-dir "$TMP_DIR" \
  --weather-station-name "$WEATHER_STATION_NAME" \
  --alpaca-device-number "$ALPACA_DOME_DEVICE_NUMBER" \
  "$@"

# "name:bucket" pairs, not an associative array - macOS ships bash 3.2
# (last GPLv2 release; Apple never upgraded), which doesn't support them.
DATASETS=(
  "power:${INFLUXDB_BUCKET_POWER}"
  "weather:${INFLUXDB_BUCKET_WEATHER}"
  "observatory:${INFLUXDB_BUCKET_OBSERVATORY}"
  "imaging:${INFLUXDB_BUCKET_IMAGING}"
)

for entry in "${DATASETS[@]}"; do
  name="${entry%%:*}"
  bucket="${entry##*:}"
  echo "Loading ${name}.lp into bucket '${bucket}'..."
  docker compose cp "${TMP_DIR}/${name}.lp" "influxdb:/tmp/${name}.lp"
  docker compose exec -T influxdb influx write \
    --bucket "$bucket" \
    --org "$INFLUXDB_ORG" \
    --token "$INFLUXDB_ADMIN_TOKEN" \
    --precision s \
    -f "/tmp/${name}.lp"
  docker compose exec -T influxdb rm -f "/tmp/${name}.lp"
done

echo
echo "Mock data loaded. Open Grafana at http://localhost:3000"
echo "  user: ${GRAFANA_ADMIN_USER}"
echo "  pass: ${GRAFANA_ADMIN_PASSWORD}"
