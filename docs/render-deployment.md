# Render Deployment (Production)

For the current public SaaS deployment plan, use
[production-deployment.md](./production-deployment.md) and the root
[render.yaml](../render.yaml). This file is retained as a compact command
reference for the Render services.

## Services

### 1) Web Service
- Start command:
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

### 2) Worker Service (scheduler source of truth)
- Start command:
```bash
python -m app.worker.oracle_worker
```

### 3) Optional Cron Service (single-run jobs)
- Command examples:
```bash
python -m app.worker.job_runner --job hourly
python -m app.worker.job_runner --job m15
python -m app.worker.job_runner --job permission
python -m app.worker.job_runner --job eod
```

### 3b) Optional Render Cron ping for ingest trigger
If you run MT5 ingestion externally and want a Render cron fallback:
```bash
curl -X POST "$API_BASE/admin/ingest/run" \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -H "Content-Type: application/json" \
  -d '{"timeframes":["M1","M15","H1"]}'
```
Recommended cadence: every minute.

### 4) Release Command
- Run migrations before deploy:
```bash
alembic upgrade head
```

## Required Production Environment Variables

- Core:
  - `APP_ENV=production`
  - `DATABASE_URL`
  - `JWT_SECRET` (strong, >= 32 chars)
  - `FRONTEND_URL`

- Stripe:
  - `STRIPE_SECRET_KEY`
  - `STRIPE_WEBHOOK_SECRET`
  - `STRIPE_PRICE_ID_BASIC`
  - `STRIPE_PRICE_ID_PRO`
  - `STRIPE_PRICE_ID_ELITE`

- Telegram:
  - `TELEGRAM_BOT_TOKEN`

- Runner / ingest:
  - `RUNNER_API_KEY`
  - Optional: `RUNNER_CONTROL_URL` (admin reconnect control only; if unset dashboard shows a warning, core compute still runs)
  - Optional: `RUNNER_REQUIRE_IP_ALLOWLIST=true`
  - Optional: `RUNNER_ALLOWED_IPS=...`

- Oracle scheduler:
  - `ORACLE_ENABLED_SYMBOLS`
  - `ORACLE_SCHEDULER_IN_API=false` (recommended when worker service is enabled)

- Timezone data (Windows/local compatibility):
  - Ensure `tzdata` package installed in environment.
