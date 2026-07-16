#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/observer/trading-intelligence-saas"
COMPOSE_FILE="docker-compose.pi.yml"

cd "$PROJECT_DIR"

if [ "$(uname -m)" != "aarch64" ]; then
  echo "ERROR: 64-bit Raspberry Pi OS is required. Detected architecture: $(uname -m)" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is not installed." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: Docker Compose v2 is not installed." >&2
  exit 1
fi

mkdir -p /home/observer/trading-intelligence-backups

if [ ! -f .env ]; then
  cp .env.pi.example .env
  chmod 600 .env
  echo "Created .env from .env.pi.example. Edit it before exposing the stack."
else
  echo ".env already exists; leaving it unchanged."
fi

docker compose -f "$COMPOSE_FILE" build
docker compose -f "$COMPOSE_FILE" run --rm trading_intelligence_migrate
docker compose -f "$COMPOSE_FILE" up -d trading_intelligence_postgres trading_intelligence_redis trading_intelligence_api trading_intelligence_worker trading_intelligence_frontend

echo
echo "Trading Intelligence SaaS started."
echo "Frontend: http://PI_TAILSCALE_IP:3100"
echo "API docs: http://PI_TAILSCALE_IP:8100/docs"
echo "Status: bash deploy/pi/status.sh"
