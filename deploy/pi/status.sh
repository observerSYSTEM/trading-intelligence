#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/observer/trading-intelligence-saas"
COMPOSE_FILE="docker-compose.pi.yml"
API_URL="${API_URL:-http://127.0.0.1:8100}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:3100}"
SYMBOL="${SYMBOL:-XAUUSD}"
AUTH_TOKEN="${TI_AUTH_TOKEN:-}"

cd "$PROJECT_DIR"

check_http() {
  local label="$1"
  local url="$2"
  local args=(-fsS --max-time 10)
  if [ -n "$AUTH_TOKEN" ]; then
    args+=(-H "Authorization: Bearer $AUTH_TOKEN")
  fi
  if curl "${args[@]}" "$url" >/tmp/trading-intelligence-status.json 2>/tmp/trading-intelligence-status.err; then
    echo "$label: OK"
    cat /tmp/trading-intelligence-status.json
    echo
  else
    echo "$label: FAILED"
    cat /tmp/trading-intelligence-status.err || true
    echo
  fi
}

echo "Container health:"
docker compose -f "$COMPOSE_FILE" ps trading_intelligence_postgres trading_intelligence_redis trading_intelligence_api trading_intelligence_worker trading_intelligence_frontend
echo

check_http "API health" "$API_URL/health"
check_http "Frontend" "$FRONTEND_URL"
check_http "Latest OANDA/API ingest timestamp" "$API_URL/health/runner"
check_http "LCE endpoint" "$API_URL/lce/checkpoint/$SYMBOL?timeframe=H1"
check_http "ODE endpoint" "$API_URL/observer/decision/$SYMBOL?timeframe=H1"
check_http "ORE endpoint" "$API_URL/observer/recommendation/$SYMBOL?timeframe=H1"
