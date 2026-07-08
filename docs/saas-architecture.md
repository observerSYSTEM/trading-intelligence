# Trading Intelligence SaaS Architecture

Trading Intelligence SaaS is a multi-user trading intelligence platform for paying subscribers. It exposes authenticated dashboards for Oracle Direction, liquidity targets, daily bias, billing, Telegram settings, and account management.

## High-Level Components

```text
Browser
  |
  | HTTPS
  v
Next.js dashboard
  |
  | Authenticated API calls
  v
FastAPI backend
  |
  | SQLAlchemy
  v
PostgreSQL

FastAPI backend -> Stripe API
FastAPI backend -> Telegram Bot API
FastAPI backend -> OANDA candles
FastAPI backend -> Twelve Data fallback candles
FastAPI backend -> Finnhub news/calendar

Worker process -> Oracle scheduler -> PostgreSQL snapshots/targets
```

## Runtime Services

Backend API:

- FastAPI application in `app/main.py`.
- Registers public, authenticated, admin, billing, health, oracle, target, runner, and settings routes.
- Uses JWT access/refresh tokens for user login.
- Uses SQLAlchemy models in `app/db/models.py`.
- Runs Alembic migrations before production deploy.

Frontend dashboard:

- Next.js application in `trading-ui`.
- Reads `NEXT_PUBLIC_API_BASE_URL` or `NEXT_PUBLIC_API_BASE`.
- Uses bearer-token authenticated API calls in `trading-ui/lib/api.ts`.
- Displays Oracle Direction, Liquidity Targets, Daily Bias Snapshot, billing, symbols, and settings.

Worker:

- `python -m app.worker.oracle_worker`
- Owns scheduled Oracle and target refresh work in production.
- Keeps `ORACLE_SCHEDULER_IN_API=false` so the web process remains request-focused.

Database:

- PostgreSQL is the production source of truth.
- Stores users, subscriptions, refresh tokens, Oracle snapshots, daily permissions, targets, candles, ingest status, Telegram routes, audit logs, and delivery state.

Cache/rate limit:

- Redis is optional but recommended for multi-user production rate limiting.
- `REDIS_URL` enables shared rate-limit state across instances.

## Request Flow

Login:

1. User logs in through the Next.js UI.
2. Backend verifies credentials.
3. Backend returns access and refresh tokens.
4. Frontend sends `Authorization: Bearer <access_token>` to protected endpoints.
5. Refresh flow renews expired access tokens without making protected endpoints public.

Oracle Direction:

1. Dashboard calls `GET /oracle/direction/{symbol}`.
2. FastAPI authenticates the user.
3. Backend resolves the user's subscription plan and enabled symbols.
4. Backend uses the API candle provider in production:
   - Primary: OANDA
   - Fallback: Twelve Data
5. Backend reads latest permissions, snapshots, and liquidity targets from PostgreSQL.
6. Backend fetches Finnhub news/calendar context when configured.
7. Backend returns:
   - `direction`
   - `buy_percent`
   - `sell_percent`
   - `confidence_percent`
   - `next_buy_liquidity`
   - `next_sell_liquidity`

Liquidity Targets:

1. Worker refreshes target snapshots from market candles.
2. Target rows are written to PostgreSQL.
3. Dashboard reads latest target state through authenticated API calls.

Billing:

1. User selects a subscription plan.
2. Backend creates Stripe Checkout or portal sessions.
3. Stripe redirects the user back to the frontend.
4. Stripe webhooks update local subscription records.
5. Plan gates determine symbol access and feature visibility.

Telegram:

1. User or admin configures Telegram route settings.
2. Backend stores Telegram chat/route state.
3. Worker/API sends notifications through the Telegram Bot API.
4. Delivery attempts and failures are logged.

## Market Data Design

Production mode is provider-based and does not require MetaTrader:

```env
DATA_PROVIDER=api
MARKET_DATA_PROVIDER=api
CANDLE_PROVIDER=oanda
CANDLE_FALLBACK_PROVIDER=twelvedata
NEWS_PROVIDER=finnhub
DISABLE_MT5=true
```

OANDA:

- Primary candle provider.
- Used because OANDA candles include completion status.
- Supports the closed-candle logic required by 08:01 London anchor behavior.

Twelve Data:

- Fallback candle provider.
- Used when OANDA cannot return a supported symbol or timeframe.
- The newest candle is treated carefully because it may still be forming.

Finnhub:

- News and economic calendar provider only.
- Not used for OHLC candle logic.

MetaTrader:

- Optional local runner path only.
- Must remain disabled in public production unless a separate deployment explicitly needs it.

## Security Boundaries

Public:

- Frontend pages.
- Stripe redirects.
- Backend `/health`.

Authenticated user:

- Dashboard data.
- Oracle Direction.
- Liquidity Targets.
- Billing portal.
- Telegram settings.
- Symbol preferences.

Admin:

- Readiness and ops endpoints.
- Runner reconnect actions.
- Admin Oracle actions.
- Support and account operations.

Runner/service integration:

- Protected with `RUNNER_API_KEY`.
- Optionally restricted by IP allow lists.

Never expose:

- `DATABASE_URL`
- `JWT_SECRET`
- Stripe secret keys
- Stripe webhook secret
- Market-data API keys
- Telegram bot token
- Runner API key

## Data Model Groups

Identity:

- Users
- Refresh tokens
- Account activation tokens

Billing:

- Subscriptions
- Usage ledger
- Stripe webhook idempotency

Oracle intelligence:

- Oracle snapshots
- Daily permission snapshots
- Targets snapshots
- Weekly/quarterly range snapshots
- Market state rows

Market ingest:

- Candle rows
- Ingest status rows
- Runner status rows

Notifications:

- Telegram routes
- Delivery logs
- Audit events

## Production Scaling Model

Start with:

- 1 backend API instance
- 1 worker instance
- 1 frontend instance
- 1 managed PostgreSQL database
- Optional Redis

Scale API horizontally when:

- Request latency rises.
- Login/dashboard traffic grows.
- Stripe webhook throughput increases.

Keep only one scheduler owner unless scheduler locking is audited for multiple workers. The safe default is a single worker process.

Scale Postgres before API if:

- Query latency rises.
- Storage nears quota.
- Connection limits are approached.

## Observability

Backend logs should include:

- Auth failures
- Stripe webhook results
- Oracle job starts and completions
- Candle provider fallback events
- OANDA/Twelve Data/Finnhub request failures
- Telegram delivery failures

Health surfaces:

- `/health` for uptime
- `/ops/ready` for service readiness
- `/admin/ops/readiness` for admin-only diagnostics
- Dashboard runner/debug cards for market-data freshness

Metrics to watch:

- API 5xx rate
- Stripe webhook failures
- Worker job failures
- Market-feed age
- Postgres CPU/storage/connections
- Provider quota usage

## Compliance and Product Notes

This system provides trading intelligence, not custody. Production deployment should keep these controls in place:

- Use HTTPS only.
- Disable public registration unless intentionally launched.
- Keep `AUTOTRADE_ENABLED=false` for the SaaS dashboard.
- Keep secrets in the hosting provider secret store.
- Use least-privilege staff access.
- Review legal copy for subscription billing, risk disclosures, refund policy, and data handling.
- Back up PostgreSQL before migrations.

## Deployment Artifacts

- [render.yaml](../render.yaml): Render Blueprint.
- [docker-compose.prod.yml](../docker-compose.prod.yml): VPS/container production stack.
- [Dockerfile.api](../Dockerfile.api): backend and worker image.
- [trading-ui/Dockerfile](../trading-ui/Dockerfile): frontend image.
- [.env.example](../.env.example): production env template.
- [docs/production-deployment.md](./production-deployment.md): deployment runbook.
