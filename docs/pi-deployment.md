# Raspberry Pi Deployment

Trading Intelligence SaaS runs as an independent Raspberry Pi stack. It must not share ObserverAI folders, databases, services, Docker networks, Docker volumes, ports, environment files, backups, or systemd units.

## Target Path

```bash
/home/observer/trading-intelligence-saas
```

## Clone

```bash
git clone https://github.com/observerSYSTEM/trading-intelligence-saas.git /home/observer/trading-intelligence-saas
cd /home/observer/trading-intelligence-saas
```

## Configure

```bash
cp .env.pi.example .env
nano .env
```

Set only Trading Intelligence values in `.env`. Do not copy secrets from any ObserverAI project.

Required production values include:

- `POSTGRES_PASSWORD`
- `DATABASE_URL` or the provided internal PostgreSQL settings
- `REDIS_URL` or the provided internal Redis settings
- `APP_URL=http://PI_TAILSCALE_IP:8100`
- `FRONTEND_URL=http://PI_TAILSCALE_IP:3100`
- `NEXT_PUBLIC_API_BASE_URL=http://PI_TAILSCALE_IP:8100`
- `JWT_SECRET`
- `RUNNER_API_KEY`
- `OANDA_API_KEY`
- `OANDA_ACCOUNT_ID`
- `TWELVE_DATA_API_KEY`
- `FINNHUB_API_KEY`
- Stripe and Telegram settings for paid production use

## Start

```bash
bash deploy/pi/install.sh
```

## Access Through Tailscale

```text
http://PI_TAILSCALE_IP:3100
http://PI_TAILSCALE_IP:8100/docs
```

## Update

```bash
bash deploy/pi/update.sh
```

## Backup

```bash
bash deploy/pi/backup.sh
```

Backups are written to:

```bash
/home/observer/trading-intelligence-backups
```

## Status

```bash
bash deploy/pi/status.sh
```

Protected API endpoints require a dashboard auth token. To include one:

```bash
TI_AUTH_TOKEN=YOUR_BEARER_TOKEN bash deploy/pi/status.sh
```

## Optional Systemd Wrapper

```bash
sudo cp deploy/pi/trading-intelligence-stack.service /etc/systemd/system/trading-intelligence-stack.service
sudo systemctl daemon-reload
sudo systemctl enable trading-intelligence-stack.service
sudo systemctl start trading-intelligence-stack.service
```

This wrapper controls only `docker-compose.pi.yml` services named `trading_intelligence_*`.

## Pi Readiness Smoke Test

```bash
python scripts/test_pi_readiness.py
```

This checks Python architecture, API mode, environment completeness, database connectivity, Redis connectivity, OANDA candle fetch, FastAPI route registration, LCE, ODE, ORE, and the frontend API base.

## Security

- Use VPN/Tailscale access only.
- Do not configure public port forwarding.
- Keep secrets only in `.env`.
- `.env` is ignored by Git.
- PostgreSQL and Redis are not exposed publicly by `docker-compose.pi.yml`.
- Do not reuse ObserverAI ports, networks, volumes, services, backups, or systemd units.

## Pi Services

- `trading_intelligence_postgres`
- `trading_intelligence_redis`
- `trading_intelligence_migrate`
- `trading_intelligence_api`
- `trading_intelligence_worker`
- `trading_intelligence_frontend`

The API is published on port `8100`; the frontend is published on port `3100`.
