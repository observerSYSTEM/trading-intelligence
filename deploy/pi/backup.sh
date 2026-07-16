#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/home/observer/trading-intelligence-saas"
BACKUP_DIR="/home/observer/trading-intelligence-backups"
COMPOSE_FILE="docker-compose.pi.yml"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="$BACKUP_DIR/$STAMP"

cd "$PROJECT_DIR"
mkdir -p "$OUT_DIR"

POSTGRES_USER_VALUE="${POSTGRES_USER:-trading_intelligence}"
POSTGRES_DB_VALUE="${POSTGRES_DB:-trading_intelligence}"
if [ -f .env ]; then
  POSTGRES_USER_VALUE="$(grep -E '^POSTGRES_USER=' .env | tail -n1 | cut -d= -f2- || true)"
  POSTGRES_DB_VALUE="$(grep -E '^POSTGRES_DB=' .env | tail -n1 | cut -d= -f2- || true)"
  POSTGRES_USER_VALUE="${POSTGRES_USER_VALUE:-trading_intelligence}"
  POSTGRES_DB_VALUE="${POSTGRES_DB_VALUE:-trading_intelligence}"
fi

docker compose -f "$COMPOSE_FILE" exec -T trading_intelligence_postgres pg_dump -U "$POSTGRES_USER_VALUE" "$POSTGRES_DB_VALUE" > "$OUT_DIR/postgres.sql"

[ -f .env ] && cp .env "$OUT_DIR/.env"
cp "$COMPOSE_FILE" "$OUT_DIR/"
cp .env.pi.example "$OUT_DIR/"
[ -f Dockerfile.pi.api ] && cp Dockerfile.pi.api "$OUT_DIR/"
[ -f trading-ui/Dockerfile.pi ] && mkdir -p "$OUT_DIR/trading-ui" && cp trading-ui/Dockerfile.pi "$OUT_DIR/trading-ui/"

chmod -R go-rwx "$OUT_DIR"
echo "Trading Intelligence backup created: $OUT_DIR"
