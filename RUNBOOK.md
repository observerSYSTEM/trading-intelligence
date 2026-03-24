# Trading Intelligence SaaS Runbook

## 1) Local startup (API + worker)

### Backend API
```powershell
.\.venv\Scripts\activate
pip install tzdata
uvicorn app.main:app --reload
```

### Oracle scheduler worker (recommended source of truth)
Run in a second terminal:
```powershell
.\.venv\Scripts\activate
pip install tzdata
python -m app.worker.oracle_worker
```

### MT5 ingest runner (Windows Vantage terminal)
Run on the Windows machine that has MT5 connected to your broker:
```powershell
.\.venv\Scripts\activate
pip install MetaTrader5 requests python-dotenv
python -m app.runner.mt5_runner
```

Required env on runner machine:
- `API_BASE` (Render/API URL)
- `RUNNER_API_KEY`
- `RUNNER_SYMBOLS` (e.g. `XAUUSD,GBPUSD,EURUSD,GBPJPY,BTCUSD`)
- `RUNNER_TIMEFRAMES` (e.g. `M1,M15,H1`)
- `MT5_TERMINAL_PATH`, `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`
- optional runner control API:
  - `RUNNER_CONTROL_ENABLED=true`
  - `RUNNER_CONTROL_BIND=0.0.0.0`
  - `RUNNER_CONTROL_PORT=8787`
  - `RUNNER_CONTROL_REQUIRE_KEY=true`
  - backend `RUNNER_CONTROL_URL=https://<runner-host>:8787`
  - if `RUNNER_CONTROL_URL` is unset, dashboard shows a non-blocking warning and reconnect control is disabled; ingest/compute continue.

Runner health endpoint:
- `GET /health/runner` (admin) shows `mt5_connected`, `last_tick_utc`, `last_ingest_utc`, `lag_seconds`, `symbols_ok`.
- runner local control API:
  - `GET /health` (with `X-Runner-Key`)
  - `POST /reconnect` (with `X-Runner-Key`)

Notes:
- In development you may run scheduler in API process by setting `ORACLE_SCHEDULER_IN_API=true`.
- In production, keep scheduler in a dedicated worker process to avoid duplicate jobs.
- Scheduler cadence:
  - MT5 ingest: `M1` every 1 minute, `M5` every 5 minutes, `M15` at `:00/:15/:30/:45`, `H1` at `:00`.
  - After ingest, Oracle recompute runs only when a newer candle close is detected than the last processed candle per symbol/timeframe.
  - Oracle hourly snapshot + target refresh: every hour at minute `02` London time.
  - Daily permission anchor compute: `08:02` London (from the `08:01` M1 candle).
  - London `08:01` capture window backfill (`07:58`-`08:05` London fetch window): retried from `07:58` through `08:20` London.
  - Daily permission degraded check: `08:20` London (alerts only if still missing/degraded after backfill).
  - M15 opportunity scan: every 15 minutes.
  - Daily audit: configured by `ORACLE_DAILY_AUDIT_HOUR` / `ORACLE_DAILY_AUDIT_MINUTE` (default 21:00 London).

### 08:01 Permission Computation
- Before London lock (`< 08:02 Europe/London`), system computes `PRELIM` permission from Asia session (`00:00-06:00 London`) using M15 sweep/displacement/volume context.
- The system computes daily permission from the exact `08:01 Europe/London` M1 candle and stores UTC in DB.
- MT5 broker-time alignment is applied:
  - ingest stores `broker_offset_seconds` per symbol,
  - expected 08:01 candle time is converted to broker-time (`expected_0801_broker_time`),
  - fallback search scans broker-mapped candles for London `07:58-08:05` and picks the closest candle to London 08:01.
- At/after `08:02 Europe/London`, system computes/stores `OFFICIAL` permission from `08:01` M1 and this becomes the lock for intraday alignment.
- If `08:01` is missing on first attempt, it auto-backfills using range ingestion (`07:58`-`08:05` London) and re-evaluates until `08:20`.
- If still missing after `08:20` London, status is degraded and API stale reasons include `missing_0801`.

### Telegram Send Timing
- One daily pinned message is used per symbol/day for daily permission.
- Hourly magnet updates and M15 opportunity alerts are sent as threaded replies only when values materially change.
- TP/SL and end-of-day audit updates are also sent as replies in the same thread.

## 2) Database migrations

```powershell
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\alembic current
```

## 3) Verify market ingest + freshness

- API:
  - `POST /admin/ingest/run` (admin, optional manual/cron trigger)
  - `GET /ops/market/status`
  - `GET /health/market`
  - `GET /health/runner`
  - `GET /oracle/status?symbol=XAUUSD`
  - `GET /oracle/snapshot/latest?symbol=XAUUSD`
- Check:
  - `mt5_connected=true` on `/ops/market/status`
  - `(symbol,timeframe)` candle lags stay below threshold in `/ops/market/status`
  - `last_tick_utc` and `lag_seconds` on `/health/runner` move forward each minute
  - `last_ingest_at` advances
  - `last_compute_at` advances
  - `is_stale=false` during normal ingest/compute
  - stale uses candle freshness only: `now_utc - latest_m15_close_utc <= 2 * 900s`
  - `stale_reasons` empty
  - `last_08_01_candle_time_utc` populated after daily anchor is available
  - broker diagnostics present:
    - `broker_offset_hours`
    - `broker_server_time_utc`
    - `expected_0801_broker_time`
    - `actual_candle_found_time`

## 4) Oracle execution checks

### Manual run (admin)
```http
POST /admin/oracle/run
{
  "symbols": ["XAUUSD","GBPUSD"]
}
```

### Snapshot read (auth)
```http
GET /oracle/snapshot/latest?symbol=XAUUSD
```

Expected:
- Latest row by `as_of_utc`
- `timeframe_main = "M15"`
- `timeframe_fast = "M1"`

## 5) Telegram setup + test

### Save settings (auth)
```http
POST /settings/telegram
{
  "telegram_enabled": true,
  "telegram_chat_id": "123456789",
  "symbols": ["XAUUSD","GBPUSD"]
}
```

### Send test message (auth)
```http
POST /settings/telegram/test
```

Expected:
- Settings save is idempotent (upsert on `user_signal_prefs.user_id`)
- Test message is delivered to configured chat
- Daily anchor message is pinned per symbol/day; intraday updates are replies to that anchor.
- Magnet taken updates are pushed automatically as threaded replies.

## 6) Stripe webhook test flow

### Local listener
```powershell
stripe listen --forward-to http://127.0.0.1:8000/billing/webhook
```

### Trigger events
```powershell
stripe trigger invoice.payment_failed
stripe trigger invoice.payment_succeeded
stripe trigger customer.subscription.deleted
```

Checks:
- Webhook signature validated (missing/invalid signature rejected)
- Idempotency table prevents duplicate reprocessing
- Subscription status/plan updates are reflected in DB
- Billing reminder scheduler sends only once per reminder window

## 7) Frontend checks

Start frontend:
```powershell
cd trading-ui
npm run dev
```

Validate:
- Dashboard shows latest snapshot and stale badge when delayed
- Admin-only `Run Oracle` button works
- Telegram settings page saves + sends test message
- Symbol settings enforce tier locks and save selected symbols
