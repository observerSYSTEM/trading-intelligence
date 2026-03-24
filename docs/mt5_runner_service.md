# MT5 Runner Service (Windows)

## Purpose
The MT5 Runner executes `trade_jobs` from the API and reports fills/position updates back to FastAPI.

## Install
```powershell
cd F:\Trading Intelligence SaaS
.venv\Scripts\pip install -r runner\requirements_runner.txt
```

## Required Env Vars (Runner machine)
- `API_BASE` (example: `https://your-render-api.onrender.com`)
- `RUNNER_API_KEY`
- `RUNNER_ID`
- `MT5_TERMINAL_PATH`
- `MT5_LOGIN`
- `MT5_PASSWORD`
- `MT5_SERVER`
- `RUNNER_SYMBOLS` (example: `XAUUSD,GBPUSD,EURUSD,GBPJPY,BTCUSD`)
- `RUNNER_JOB_POLL_SECONDS` (default `5`)
- `RUNNER_HEARTBEAT_SECONDS` (default `30`)
- `RUNNER_POS_SYNC_SECONDS` (default `30`)

## Run Manually
```powershell
runner\run_mt5_autotrade_runner.bat
```

## MT5 Portable Mode
Use MT5 terminal portable mode so all data/profile files stay in the terminal folder:

```powershell
"C:\Path\To\terminal64.exe" /portable
```

Set `MT5_TERMINAL_PATH` to that `terminal64.exe` path.  
Launch MT5 once with `/portable`, log in to the trading account, then keep the terminal available while runner is active.

## Windows Service Option (NSSM)
1. Install NSSM.
2. Create service:
```powershell
nssm install TradingIntelMT5Runner "F:\Trading Intelligence SaaS\.venv\Scripts\python.exe" "-m runner.runner_main"
```
3. Set working directory to:
```powershell
F:\Trading Intelligence SaaS
```
4. In NSSM environment settings, add required runner env vars.
5. Start service:
```powershell
nssm start TradingIntelMT5Runner
```

## Health Checks
- `POST /runner/mt5/heartbeat` should update runner last-seen timestamp.
- `GET /runner/jobs/next` returns `204` when queue is empty.
- `POST /runner/positions/sync` should keep `position_state` fresh.
