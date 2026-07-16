# Fresh GitHub Repository and Raspberry Pi Install

This guide prepares Trading Intelligence SaaS for a brand-new GitHub repository and a clean Raspberry Pi installation.

The clean export must remain separate from ObserverAI and any Observer Forecast project. Do not copy their folders, databases, ports, Docker networks, Docker volumes, systemd units, backups, or environment files.

## 1. Create a Clean Export on Windows

From the existing Trading Intelligence SaaS workspace:

```powershell
cd "F:\Trading Intelligence SaaS"
powershell -ExecutionPolicy Bypass -File deploy\export_clean_repo.ps1
```

The export is created at:

```text
F:\Trading-Intelligence-SaaS-Clean
```

The export script:

- copies the Trading Intelligence project without Git history
- excludes local secrets, `.env`, virtual environments, build output, databases, caches, logs, and temporary files
- preserves `.env.example` and `.env.pi.example`
- scans the clean folder for likely real secrets
- fails with the exact file if a likely secret is found
- confirms no nested `.git` folder exists
- runs Python syntax checks
- runs Docker Compose config checks when Docker is available
- runs Next.js typecheck only when dependencies are available in the exported folder

It prints `CLEAN EXPORT READY` only after validation passes.

## 2. Create a Fresh GitHub Repository

Do this inside the clean export folder, not the original working project:

```powershell
cd "F:\Trading-Intelligence-SaaS-Clean"
git init
git add .
git commit -m "Initial clean Trading Intelligence SaaS import"
git branch -M main
git remote add origin https://github.com/observerSYSTEM/trading-intelligence-saas.git
git push -u origin main
```

Do not run these commands from `F:\Trading Intelligence SaaS`.

## 3. Clone on Raspberry Pi

```bash
git clone https://github.com/observerSYSTEM/trading-intelligence-saas.git /home/observer/trading-intelligence-saas
cd /home/observer/trading-intelligence-saas
```

## 4. Configure Pi Environment

```bash
cp .env.pi.example .env
nano .env
```

Set real secrets only in `.env` on the Pi:

- `POSTGRES_PASSWORD`
- `DATABASE_URL` or internal PostgreSQL values
- `REDIS_URL` or internal Redis values
- `JWT_SECRET`
- `RUNNER_API_KEY`
- `OANDA_API_KEY`
- `OANDA_ACCOUNT_ID`
- `TWELVE_DATA_API_KEY`
- `FINNHUB_API_KEY`
- Stripe values for billing
- Telegram values for alerts

Keep:

```env
DATA_PROVIDER=api
MARKET_DATA_PROVIDER=api
DISABLE_MT5=true
CANDLE_PROVIDER=oanda
CANDLE_FALLBACK_PROVIDER=twelvedata
NEWS_PROVIDER=finnhub
```

## 5. Verify the Clean Pi Clone

```bash
bash deploy/pi/verify_clean_clone.sh /home/observer/trading-intelligence-saas
```

This confirms:

- ARM64 Linux
- `.env` exists and is ignored by Git
- no MetaTrader5 requirement
- ports `3100` and `8100`
- unique Trading Intelligence container names
- no ObserverAI or Observer Forecast references
- OANDA/API mode with MT5 disabled
- LCE, TLEE, LOE, RRE, PPE, ODE, and ORE modules exist

## 6. Start the Pi Stack

```bash
bash deploy/pi/install.sh
```

Access through Tailscale/VPN only:

```text
http://PI_TAILSCALE_IP:3100
http://PI_TAILSCALE_IP:8100/docs
```

## 7. Maintain the Pi Stack

Update:

```bash
bash deploy/pi/update.sh
```

Backup:

```bash
bash deploy/pi/backup.sh
```

Status:

```bash
bash deploy/pi/status.sh
```

For protected status endpoints:

```bash
TI_AUTH_TOKEN=YOUR_BEARER_TOKEN bash deploy/pi/status.sh
```

## Security Checklist

- Do not push `.env`.
- Do not include Git history from the old repository.
- Do not copy local databases or backups.
- Do not copy virtual environments or build output.
- Do not expose PostgreSQL or Redis ports publicly.
- Use Tailscale/VPN-only access.
- Keep ObserverAI and Observer Forecast completely separate.
