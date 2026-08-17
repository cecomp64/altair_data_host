#!/usr/bin/env bash
# End-to-end deployment: installs Docker itself, generates secrets, installs
# host-level udev rules, builds/starts the Docker stack, waits for InfluxDB
# to be healthy, creates the weather/observatory/imaging/network/starlink/system buckets, installs
# the host-level e-paper Python deps (SPI + venv + waveshare_epd driver) and
# systemd service, then installs the host-level charge-cutoff relay's Python
# deps and (once CHARGE_CUTOFF_GPIO_PIN is set in .env) its systemd service.
# Safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# 1. Install Docker Engine + Compose plugin if not already present. On a
# genuinely fresh install this exits with instructions to log back in (group
# membership needs a new session) - re-run this script after that.
"${REPO_DIR}/scripts/install-docker.sh"

# 2. Ensure secrets exist (never overwrite an existing .env)
if [[ ! -f .env ]]; then
  echo "No .env found - generating one with random secrets at ${REPO_DIR}/.env"
  cat > .env <<EOF
INFLUXDB_ADMIN_USERNAME=admin
INFLUXDB_ADMIN_PASSWORD=$(openssl rand -base64 24)
INFLUXDB_ORG=solar
INFLUXDB_ADMIN_TOKEN=$(openssl rand -hex 32)

INFLUXDB_BUCKET_POWER=power
INFLUXDB_BUCKET_WEATHER=weather
INFLUXDB_BUCKET_OBSERVATORY=observatory
INFLUXDB_BUCKET_IMAGING=imaging
INFLUXDB_BUCKET_NETWORK=network
INFLUXDB_BUCKET_STARLINK=starlink
INFLUXDB_BUCKET_SYSTEM=system

GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 24)
# EDIT to whatever's actually reachable from where you'll read alert
# notifications (Tailscale hostname, LAN IP, real domain) - see .env.example.
GRAFANA_ROOT_URL=http://localhost:3000

# EDIT THESE to point at your actual weather/roof endpoints - see
# .env.example for what each one means. Imaging needs no entry here; N.I.N.A.'s
# InfluxDB Exporter plugin is configured separately (see README "Imaging").
WEATHER_API_URL=http://weather-station.local/v1/current
WEATHER_STATION_NAME=primary
LATITUDE=38.0
LONGITUDE=-121.0
ELEVATION_M=0
ALPACA_BASE_URL=http://roof-controller.local:11111/api/v1/dome/0
ALPACA_DOME_DEVICE_NUMBER=0

# EDIT to your own ping targets (comma-separated) - see .env.example for
# details. Left at these defaults, network health still works out of the box.
PING_TARGETS_INTERNET=1.1.1.1,8.8.8.8,google.com
PING_TARGETS_TAILSCALE=
EOF
  chmod 600 .env
  echo "Review ${REPO_DIR}/.env - in particular, fill in the real WEATHER_API_URL and ALPACA_BASE_URL."
fi

# 3. Persistent serial device symlinks (host-level, idempotent)
"${REPO_DIR}/scripts/install-udev-rules.sh"

# 4. Build the custom telegraf image and start the stack
docker compose build
docker compose up -d

# 5. Wait for InfluxDB to report healthy before wiring up the host-side service
echo "Waiting for InfluxDB to become healthy..."
status="starting"
for _ in $(seq 1 30); do
  status="$(docker inspect --format '{{.State.Health.Status}}' influxdb 2>/dev/null || echo starting)"
  [[ "$status" == "healthy" ]] && break
  sleep 2
done
if [[ "$status" != "healthy" ]]; then
  echo "InfluxDB did not become healthy in time; check 'docker compose logs influxdb'" >&2
  exit 1
fi

# 6. Create the weather/observatory/imaging/network/starlink/system buckets (idempotent)
"${REPO_DIR}/scripts/init-influx-buckets.sh"

# 7. Discord alerting (contact point, policy, alert rules) - skips (doesn't
# fail) until DISCORD_WEBHOOK_URL is set in .env, since that requires a
# Discord webhook you create yourself
"${REPO_DIR}/scripts/init-grafana-alerting.sh"

# 8. Host-level e-paper Python deps (SPI, venv, waveshare_epd driver)
"${REPO_DIR}/scripts/install-epaper-deps.sh"

# 9. Host-level e-paper systemd service
"${REPO_DIR}/scripts/install-epaper-service.sh"

# 10. Host-level charge-cutoff relay Python deps (venv, gpiozero/lgpio)
"${REPO_DIR}/scripts/install-charge-cutoff-deps.sh"

# 11. Host-level charge-cutoff systemd service - skips (doesn't fail) until
# CHARGE_CUTOFF_GPIO_PIN is set in .env, since that's hardware-wiring specific
"${REPO_DIR}/scripts/install-charge-cutoff-service.sh"

echo
echo "Deployment complete."
docker compose ps
