#!/usr/bin/env bash
# One-time VPS setup for the SEO Agent Docker deployment.
#
# Run this ON THE VPS from the repo directory (after cloning SEO-Agent here):
#   git clone https://github.com/aurora10/SEO-Agent.git /srv/seo-agent
#   cd /srv/seo-agent && sudo bash deploy/setup_vps.sh
#
# What it does:
#   1. Installs Docker + the compose plugin if missing (Debian/Ubuntu).
#   2. Creates .env from .env.example (EDIT it with real secrets next).
#   3. Clones the constructief site into ./repo (needed by Agent 3 + monitor).
#   4. Pulls the image and starts the stack.
#
# Assumptions: Debian/Ubuntu VPS, runner has sudo/root, constructief repo is
# cloneable (public or you SSH-clone it yourself). Set CONSTRUCTIEF_URL if needed.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONSTRUCTIEF_URL="${CONSTRUCTIEF_URL:-https://github.com/aurora10/constructief.git}"
cd "$APP_DIR"

echo "==> Installing Docker + compose plugin (if missing)"
if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

echo "==> Creating .env from .env.example (if not present)"
if [ ! -f .env ]; then
  cp .env.example .env
  echo ">> Open $APP_DIR/.env and fill in the real secrets, then re-run:"
  echo ">>   docker compose up -d"
  echo ">> (See .env.example comments for the base64 credentials commands.)"
fi

echo "==> Preparing ./repo volume for the constructief auto-clone"
# The container clones the constructief repo itself at startup (google-sheets branch),
# so no manual checkout is needed here — we only create the mount point.
mkdir -p repo

echo "==> Pulling image + starting"
docker compose pull
docker compose up -d

echo ""
echo ">> Done. Watch logs with:  docker compose logs -f"
echo ">> The container self-schedules via cron (daily collect, weekly analyze/content/monitor)."
