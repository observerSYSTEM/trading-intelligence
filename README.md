# Trading Intelligence SaaS

## Railway deployment
- Full deployment guide: `DEPLOY_RAILWAY.md`
- Existing Render notes: `docs/render-deployment.md`

## MT5 runner on Windows (Vantage)
1. Activate environment and install runner deps:
```powershell
.\.venv\Scripts\activate
pip install MetaTrader5 requests python-dotenv
```
2. Configure env values:
- `API_BASE`
- `RUNNER_API_KEY`
- `RUNNER_SYMBOLS` (for example `XAUUSD,GBPUSD,EURUSD,GBPJPY,BTCUSD`)
- `RUNNER_TIMEFRAMES` (for example `M1,M15,H1`)
- `MT5_TERMINAL_PATH`, `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`
3. Start continuous runner:
```powershell
python -m app.runner.mt5_runner
```

The runner ingests latest closed candles every loop and posts heartbeat/status to `/runner/mt5/heartbeat`.

## Render worker/cron freshness
- Web service should run API only.
- Scheduler source of truth should be worker:
```bash
python -m app.worker.oracle_worker
```
- Optional Render Cron fallback to trigger ingest:
```bash
curl -X POST "$API_BASE/admin/ingest/run" \
  -H "Authorization: Bearer $ADMIN_JWT" \
  -H "Content-Type: application/json" \
  -d '{"timeframes":["M1","M15","H1"]}'
```

Use `/health/runner`, `/ops/market/status`, and `/oracle/status` to confirm lag and freshness.
