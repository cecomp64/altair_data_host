#!/usr/bin/env bash
# Installs Docker Engine + the Compose plugin via Docker's official apt
# repository (not the get.docker.com convenience script - Docker's own docs
# don't recommend that one outside quick evaluation). Idempotent: skips
# entirely if docker + `docker compose` are already installed and usable by
# the invoking user.
#
# Group membership changes only take effect in a new login session, so on a
# genuinely fresh install this exits 1 with instructions to log back in and
# re-run - there's no way around that being a one-time speed bump.
set -euo pipefail

SERVICE_USER="${SUDO_USER:-${USER}}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer targets Linux hosts; skipping on $(uname -s)." >&2
  echo "Install Docker Desktop manually: https://docs.docker.com/get-docker/" >&2
  exit 0
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "Docker + Compose plugin already installed ($(docker --version))."
else
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "This installer only supports apt-based distros (Debian/Raspberry Pi OS/Ubuntu)." >&2
    echo "Install Docker manually for your distro: https://docs.docker.com/engine/install/" >&2
    exit 1
  fi

  echo "Installing Docker Engine + Compose plugin from Docker's official apt repository..."

  # Remove any conflicting older/distro-packaged Docker bits first, per
  # Docker's own install docs.
  for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
    sudo apt-get remove -y "$pkg" >/dev/null 2>&1 || true
  done

  sudo apt-get update
  sudo apt-get install -y --no-install-recommends ca-certificates curl

  # shellcheck disable=SC1091
  . /etc/os-release

  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

  sudo apt-get update
  sudo apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  echo "Docker installed: $(docker --version)"
fi

needs_new_session=0

if ! id -nG "$SERVICE_USER" | grep -qw docker; then
  echo "Adding ${SERVICE_USER} to the docker group."
  sudo usermod -aG docker "$SERVICE_USER"
  needs_new_session=1
fi

if ! docker version >/dev/null 2>&1; then
  needs_new_session=1
fi

if [[ "$needs_new_session" -eq 1 ]]; then
  cat >&2 <<'EOF'

Docker was just installed and/or this user was just added to the docker
group. Group membership only takes effect in a NEW login session - log out
and back in (or reboot), then re-run scripts/deploy.sh to continue.
EOF
  exit 1
fi

echo "Docker is installed and usable by ${SERVICE_USER}."
