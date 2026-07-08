# Trading Intelligence SaaS Production Deployment Guide

This guide deploys the public Trading Intelligence SaaS with:

- FastAPI backend
- PostgreSQL
- Stripe subscriptions and billing portal
- User login and account activation
- Telegram integration
- Oracle Direction dashboard
- Liquidity Targets
- API-based market/news data with OANDA, Twelve Data fallback, and Finnhub

The production default is non-MT5 mode:

```env
DATA_PROVIDER=api
MARKET_DATA_PROVIDER=api
CANDLE_PROVIDER=oanda
CANDLE_FALLBACK_PROVIDER=twelvedata
NEWS_PROVIDER=finnhub
DISABLE_MT5=true
```

Do not enable trade execution for the public SaaS unless a separate audited execution service has been reviewed and isolated.

## Deployment Choices

Use Render for the fastest managed deployment:

- Render Web Service: FastAPI backend
- Render Web Service: Next.js dashboard
- Render Worker: Oracle scheduler and target refresh jobs
- Render Postgres: application database

Use a VPS when you need full control:

- Docker Compose production stack
- Reverse proxy and TLS, such as Caddy, Nginx, or Traefik
- Managed backups for Postgres volumes
- OS-level patching and monitoring

## Required External Accounts

Create and collect these before deploying:

- Stripe live secret key, webhook signing secret, and three recurring price IDs.
- OANDA API key for candle data.
- Twelve Data API key for fallback candles.
- Finnhub API key for news and economic calendar.
- Telegram bot token and production chat ID.
- Production domain names, for example `api.example.com` and `app.example.com`.

## Environment Variables

Start from [.env.example](../.env.example).

Important production values:

```env
APP_ENV=production
APP_URL=https://api.example.com
FRONTEND_URL=https://app.example.com
NEXT_PUBLIC_API_BASE_URL=https://api.example.com
DATABASE_URL=...
JWT_SECRET=...
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID_BASIC=price_...
STRIPE_PRICE_ID_PRO=price_...
STRIPE_PRICE_ID_ELITE=price_...
OANDA_API_KEY=...
TWELVE_DATA_API_KEY=...
FINNHUB_API_KEY=...
TELEGRAM_BOT_TOKEN=...
RUNNER_API_KEY=...
SIGNAL_API_TOKEN=...
```

`APP_URL` and `FRONTEND_URL` must be non-localhost values in production. The backend validates this at startup.

## Render Deployment

The repository includes [render.yaml](../render.yaml).

1. Push the repository to GitHub or GitLab.
2. In Render, create a new Blueprint from this repository.
3. Fill all `sync: false` values:
   - `APP_URL`
   - `FRONTEND_URL`
   - `NEXT_PUBLIC_API_BASE_URL`
   - `NEXT_PUBLIC_API_BASE`
   - Stripe secrets and price IDs
   - OANDA, Twelve Data, Finnhub, Telegram secrets
4. Deploy the Blueprint.
5. Confirm the API service runs the pre-deploy migration:

```bash
alembic upgrade head
```

6. Add custom domains:
   - API domain points to the backend service.
   - App domain points to the frontend service.
7. Update the frontend service env after domains are final:

```env
NEXT_PUBLIC_API_BASE_URL=https://api.example.com
NEXT_PUBLIC_API_BASE=https://api.example.com
```

8. Redeploy the frontend after changing `NEXT_PUBLIC_*` values because Next.js embeds public variables during build.

### Render Service Layout

Backend API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Worker:

```bash
python -m app.worker.oracle_worker
```

Frontend:

```bash
npm run start -- -H 0.0.0.0 -p $PORT
```

Health check:

```text
/health
```

### Stripe Webhooks On Render

Set the Stripe webhook endpoint to:

```text
https://api.example.com/billing/webhook
```

Events to enable:

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.payment_succeeded`
- `invoice.payment_failed`

Store the signing secret in:

```env
STRIPE_WEBHOOK_SECRET=whsec_...
```

## VPS Deployment

Install Docker and Docker Compose on the server. Copy the repository to the server, then create `.env.production` from `.env.example`.

Required values for Compose:

```env
APP_ENV=production
APP_URL=https://api.example.com
FRONTEND_URL=https://app.example.com
NEXT_PUBLIC_API_BASE_URL=https://api.example.com
POSTGRES_PASSWORD=replace-with-strong-password
JWT_SECRET=replace-with-32-plus-character-secret
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
OANDA_API_KEY=...
TWELVE_DATA_API_KEY=...
FINNHUB_API_KEY=...
TELEGRAM_BOT_TOKEN=...
```

Build and start:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

Check service health:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f api
curl https://api.example.com/health
```

Run migrations manually if needed:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production run --rm migrate
```

### Reverse Proxy

Terminate TLS at a reverse proxy:

- `api.example.com` -> `127.0.0.1:8000`
- `app.example.com` -> `127.0.0.1:3000`

Keep only ports 80 and 443 open publicly. Restrict database and Redis ports to the Docker network.

## Initial Admin

Create or activate an admin user after the database is migrated. Use the existing seed/reset scripts with production environment loaded:

```bash
python -m app.scripts.seed_admin
python reset_admin_password.py
```

Run these from a secure shell only. Replace temporary passwords immediately.

## Post-Deploy Smoke Tests

Backend:

```bash
curl https://api.example.com/health
curl https://api.example.com/ops/ready
```

Frontend:

- Open `https://app.example.com`.
- Login as an admin/test account.
- Confirm dashboard cards load:
  - Oracle Direction
  - Liquidity Targets
  - Daily Bias Snapshot
  - Billing status

Market data:

```bash
python scripts/test_candle_provider.py --symbol XAUUSD --timeframe M15
```

Stripe:

- Create a test customer in Stripe live mode only after all URLs and price IDs are correct.
- Confirm checkout redirects to the frontend success/cancel URLs.
- Confirm the webhook updates the user subscription row.

Telegram:

- Use the dashboard Telegram test route.
- Confirm message delivery to the production chat.

## Operational Checks

Daily:

- API `/health` returns `ok`.
- Worker logs show scheduled Oracle jobs.
- Market feed is fresh.
- Stripe webhook failures are zero.
- Postgres storage and CPU are healthy.

Weekly:

- Review admin audit logs.
- Verify database backups.
- Rotate staff access as needed.
- Review provider quota usage for OANDA, Twelve Data, and Finnhub.

Before every deploy:

```bash
python -m compileall app runner scripts
cd trading-ui && npm run build
```

## Rollback

Render:

- Use Render rollback for the affected service.
- If a migration was destructive, restore from a database backup.

VPS:

```bash
git checkout <known-good-commit>
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

Do not roll back the database unless you have checked migration compatibility.

## References

- Render Blueprint YAML reference: https://render.com/docs/blueprint-spec
- Docker Compose services reference: https://docs.docker.com/reference/compose-file/services/
