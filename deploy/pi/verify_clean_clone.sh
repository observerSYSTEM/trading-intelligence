#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-$(pwd)}"
COMPOSE_FILE="$ROOT_DIR/docker-compose.pi.yml"
ENV_FILE="$ROOT_DIR/.env"
FAILURES=()

fail() {
  FAILURES+=("$1")
}

contains_forbidden_observer_ref() {
  local path="$1"
  if grep -RInE 'ObserverAI|Observer Forecast|observerai|observer-forecast|observer_forecast' \
    "$path/docker-compose.pi.yml" \
    "$path/deploy/pi" \
    --exclude='verify_clean_clone.sh' >/tmp/trading-intelligence-observer-scan.txt 2>/dev/null; then
    return 0
  fi
  return 1
}

if [ "$(uname -s)" != "Linux" ]; then
  fail "Expected Linux, detected $(uname -s)."
fi

case "$(uname -m)" in
  aarch64|arm64) ;;
  *) fail "Expected ARM64 Linux, detected $(uname -m)." ;;
esac

if find "$ROOT_DIR" -path '*/.git' -type d | grep -q .; then
  fail "Nested .git folder found in clean clone/export."
fi

if [ ! -f "$ENV_FILE" ]; then
  fail ".env does not exist. Create it from .env.pi.example before starting the Pi stack."
fi

if [ -f "$ROOT_DIR/.gitignore" ] && ! grep -Eq '(^|/)\.env($|[[:space:]])' "$ROOT_DIR/.gitignore"; then
  fail ".env is not ignored by .gitignore."
fi

if grep -RIn '^MetaTrader5' "$ROOT_DIR/requirements.txt" "$ROOT_DIR/pyproject.toml" "$ROOT_DIR/Dockerfile.pi.api" "$ROOT_DIR/docker-compose.pi.yml" 2>/dev/null; then
  fail "MetaTrader5 is listed in application requirements."
fi

if [ ! -f "$COMPOSE_FILE" ]; then
  fail "docker-compose.pi.yml is missing."
else
  if ! grep -q '8100:8000' "$COMPOSE_FILE"; then
    fail "API port 8100 is not configured."
  fi
  if ! grep -q '3100:3100' "$COMPOSE_FILE"; then
    fail "Frontend port 3100 is not configured."
  fi

  for name in \
    trading_intelligence_postgres \
    trading_intelligence_redis \
    trading_intelligence_api \
    trading_intelligence_worker \
    trading_intelligence_frontend; do
    if ! grep -q "$name" "$COMPOSE_FILE"; then
      fail "Missing unique container/service name: $name."
    fi
  done

  if grep -Eiq 'ObserverAI|Observer Forecast|observerai|observer-forecast|observer_forecast|8787:|8000:8000|3000:3000' "$COMPOSE_FILE"; then
    fail "docker-compose.pi.yml references ObserverAI/Observer Forecast naming or non-Pi ports."
  fi

  if ! grep -q 'DATA_PROVIDER: api' "$COMPOSE_FILE"; then
    fail "DATA_PROVIDER api mode is not enabled in docker-compose.pi.yml."
  fi
  if ! grep -q 'MARKET_DATA_PROVIDER: api' "$COMPOSE_FILE"; then
    fail "MARKET_DATA_PROVIDER api mode is not enabled in docker-compose.pi.yml."
  fi
  if ! grep -q 'DISABLE_MT5: "true"' "$COMPOSE_FILE"; then
    fail "DISABLE_MT5=true is not enforced in docker-compose.pi.yml."
  fi
fi

for module in \
  "$ROOT_DIR/app/services/liquidity_checkpoint_engine.py" \
  "$ROOT_DIR/app/services/tlee_engine.py" \
  "$ROOT_DIR/app/services/loe_engine.py" \
  "$ROOT_DIR/app/services/rre_engine.py" \
  "$ROOT_DIR/app/services/ppe_engine.py" \
  "$ROOT_DIR/app/services/observer_decision_engine.py" \
  "$ROOT_DIR/app/services/observer_recommendation_engine.py"; do
  if [ ! -f "$module" ]; then
    fail "Missing intelligence module: $module"
  fi
done

if contains_forbidden_observer_ref "$ROOT_DIR"; then
  fail "ObserverAI or Observer Forecast references found:"
  cat /tmp/trading-intelligence-observer-scan.txt
fi

if [ "${#FAILURES[@]}" -gt 0 ]; then
  echo "CLEAN CLONE VERIFICATION FAILED"
  printf '%s\n' "${FAILURES[@]}"
  exit 1
fi

echo "CLEAN CLONE VERIFIED"
echo "Project: $ROOT_DIR"
echo "Ports: frontend 3100, API 8100"
echo "Mode: OANDA/API candles with MT5 disabled"
