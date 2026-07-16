#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/observer/trading-intelligence-saas"
COMPOSE_FILE="docker-compose.pi.yml"

cd "$PROJECT_DIR"

git pull origin main
docker compose -f "$COMPOSE_FILE" build trading_intelligence_migrate trading_intelligence_api trading_intelligence_worker trading_intelligence_frontend
docker compose -f "$COMPOSE_FILE" run --rm trading_intelligence_migrate
docker compose -f "$COMPOSE_FILE" up -d --no-deps trading_intelligence_api trading_intelligence_worker trading_intelligence_frontend

echo "Trading Intelligence SaaS update complete."
