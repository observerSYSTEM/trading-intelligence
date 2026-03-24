# MT5 Runner Task Scheduler (Windows)

This runs the external MT5 runner and posts candles to:

- `POST /ingest/mt5/candle`

with header:

- `X-Runner-Key: <RUNNER_API_KEY>`

## 1) Prerequisites

1. Install packages in your backend venv:
   - `pip install MetaTrader5 requests python-dotenv`
2. Set environment values in `.env`:
   - `APP_URL=http://127.0.0.1:8000`
   - `RUNNER_API_KEY=<strong-random-secret>`
   - `RUNNER_SYMBOLS=XAUUSD,GBPUSD,EURUSD,GBPJPY,BTCUSD`
   - `RUNNER_TIMEFRAMES=M1,M15,H1`
   - optional broker alias map: `RUNNER_SYMBOL_MAP_JSON={"BTCUSD":"BTCUSDm"}`
   - `MT5_LOGIN=...`
   - `MT5_PASSWORD=...`
   - `MT5_SERVER=...`
   - optional: `MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe`

## 2) Manual test

From repo root (one pass across configured symbols/timeframes):

```powershell
runner\run_mt5_ingest.bat
```

You should see JSON response from `/ingest/mt5/candle`.

## 3) Create scheduled task

1. Open Task Scheduler -> Create Task.
2. General:
   - Run whether user is logged on or not.
   - Run with highest privileges.
3. Triggers:
   - At startup (recommended for continuous ingest): use `run_mt5_ingest_loop.bat`
   - or Daily for a one-pass ingest: use `run_mt5_ingest.bat`
4. Actions:
   - Program/script (continuous): `F:\Trading Intelligence SaaS\runner\run_mt5_ingest_loop.bat`
   - Start in: `F:\Trading Intelligence SaaS\runner`
5. Settings:
   - Retry on failure (recommended): every 5 minutes, 3 attempts.

## 4) Security note

- Keep `RUNNER_API_KEY` secret.
- Rotate the key if leaked.
- Do not commit real keys/passwords to git.

---

## MT4 Signal Writer (Oracle -> `signal.json`)

This runner calls:

- `POST /admin/oracle/exec`

and atomically writes:

- `MT4_SIGNAL_FILE_PATH` (for example `...\MQL4\Files\signal.json`)

### Required `.env` values

- `API_BASE=http://127.0.0.1:8000`
- `MT4_SIGNAL_FILE_PATH=C:\path\to\MQL4\Files\signal.json`
- `ORACLE_EXEC_ADMIN_TOKEN=<admin bearer token>` or:
  - `ADMIN_EMAIL=...`
  - `ADMIN_PASSWORD=...`
- optional:
  - `ORACLE_SYMBOL=XAUUSD`
  - `ORACLE_EXEC_TARGET_TIER=elite`
  - `ORACLE_EXEC_SESSION=auto`
  - `ORACLE_EXEC_TTL_SECONDS=900`
  - `MT4_WRITER_INTERVAL_SECONDS=60`
  - `MT4_WRITER_ONCE=false`

### Manual run

```powershell
runner\run_mt4_signal_writer.bat
```
