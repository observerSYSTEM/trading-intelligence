# Deploying Trading Intelligence SaaS to Railway

This repo is a monorepo:

- Backend root: `.` (FastAPI + Alembic)
- Frontend root: `trading-ui` (Next.js App Router)
- Runner root: `runner` (Windows-only MT5 process, not deployed to Railway)

## 1) Railway project layout

Create one Railway project with three services:

1. `backend-api` (from repo root `.`)
2. `frontend-web` (from `trading-ui`)
3. `postgres` (Railway Postgres plugin/service)

Optional later:
- `oracle-worker` (from repo root `.`) for scheduler-only process

## 2) Backend service (`backend-api`)

Service settings:

- Root Directory: `.`
- Build Command:
  - `pip install -r requirements.txt`
- Start Command:
  - `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health Check Path:
  - `/health`

Recommended release command (run before/after first deploy):

```bash
alembic upgrade head
```

If using Railway "Deploy Hooks" or "Pre-deploy command", use:

```bash
python -m alembic upgrade head
```

## 3) Worker service (`oracle-worker`) - recommended

This avoids duplicate schedulers in web dynos and keeps automation always-on.

- Root Directory: `.`
- Build Command:
  - `pip install -r requirements.txt`
- Start Command:
  - `python -m app.worker.oracle_worker`

Set `ORACLE_SCHEDULER_IN_API=false` on web service when worker is enabled.

## 4) Frontend service (`frontend-web`)

Service settings:

- Root Directory: `trading-ui`
- Build Command:
  - `npm ci && npm run build`
- Start Command:
  - `npm run start -- -p $PORT`
- Health Check Path:
  - `/`

## 5) Required environment variables

## Frontend service env

- `NEXT_PUBLIC_API_BASE=https://<backend-domain>`
- `FRONTEND_URL=https://<frontend-domain>`

## Backend service env

- `APP_ENV=production`
- `APP_URL=https://<backend-domain>`
- `FRONTEND_URL=https://<frontend-domain>`
- `DATABASE_URL=<Railway Postgres DATABASE_URL>`
- `JWT_SECRET=<strong-32+-char-secret>`
- `ADMIN_EMAIL=<admin email>`
- `ADMIN_PASSWORD=<admin password>`
- `RUNNER_API_KEY=<shared secret with local runner>`
- `RUNNER_CONTROL_URL=<optional, can be blank>`
- `CORS_ORIGINS=https://<frontend-domain>`
- `TRUSTED_HOSTS=<backend-domain>,<frontend-domain>`
- `ORACLE_SYMBOL=XAUUSD`
- `ORACLE_TIMEFRAME=M1`
- `ORACLE_ENABLED_SYMBOLS=XAUUSD,GBPUSD,EURUSD,GBPJPY,BTCUSD`
- `MARKET_DATA_PROVIDER=mt5`
- `MARKET_INGEST_HEARTBEAT_ENABLED=false`
- `ORACLE_SCHEDULER_IN_API=false` (if worker service exists)

Stripe:
- `STRIPE_SECRET_KEY=sk_live_...`
- `STRIPE_WEBHOOK_SECRET=whsec_...`
- `STRIPE_PRICE_ID_BASIC=price_...`
- `STRIPE_PRICE_ID_PRO=price_...`
- `STRIPE_PRICE_ID_ELITE=price_...`

Telegram:
- `TELEGRAM_BOT_TOKEN=<bot token>`

Optional security/limits:
- `RUNNER_IP_ALLOWLIST=<comma-separated IPs>`
- `RUNNER_REQUIRE_IP_ALLOWLIST=true`
- `RUNNER_TRUST_PROXY_HEADERS=true`
- `REDIS_URL=<redis url>`

## Local Windows runner env only (do not place in Railway)

- `MT5_TERMINAL_PATH`
- `MT5_LOGIN`
- `MT5_PASSWORD`
- `MT5_SERVER`
- `RUNNER_API_KEY` (must match backend)
- `API_BASE=https://<backend-domain>`

## 6) Stripe production checklist

1. Set live key in backend: `STRIPE_SECRET_KEY=sk_live_...`
2. Set live webhook secret: `STRIPE_WEBHOOK_SECRET=whsec_...`
3. Set live price IDs:
   - `STRIPE_PRICE_ID_BASIC`
   - `STRIPE_PRICE_ID_PRO`
   - `STRIPE_PRICE_ID_ELITE`
4. Configure Stripe webhook endpoint:
   - `https://<backend-domain>/billing/webhook`
5. Verify backend readiness:
   - `GET /ops/ready` (admin-auth required)

## 7) Local runner + Railway backend topology

Supported temporary architecture:

- Railway frontend + Railway backend + Railway Postgres
- MT5 runner on your local Windows machine/VPS pushes candles to backend via:
  - `POST /ingest/mt5/candle`
  - `POST /runner/mt5/heartbeat`

No MT5 terminal is required on Railway.

## 8) Deployment order

1. Add Railway Postgres service.
2. Configure backend env vars.
3. Deploy backend service.
4. Run `alembic upgrade head`.
5. Configure and deploy worker service.
6. Configure frontend env vars.
7. Deploy frontend service.
8. Point local runner `API_BASE` to Railway backend URL and start runner.

## 9) Verification checklist

Backend/API:
- `GET /health` returns `{ "ok": true }`
- `GET /health/db` succeeds with admin token
- `GET /ops/ready` shows all critical checks as `ok=true`

DB/Migrations:
- `alembic current` equals `alembic heads`

Frontend:
- Login/register/dashboard load via frontend domain
- Network calls target `NEXT_PUBLIC_API_BASE` (not localhost)

Runner integration:
- `GET /health/runner` shows recent heartbeat
- Ingest endpoint writes fresh candles
- Oracle snapshots advance over time

Stripe:
- Checkout session creates correctly
- Webhook signature validated at `/billing/webhook`
- Billing portal returns frontend URL correctly
