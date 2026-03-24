# Oracle Fast Trigger Scheduler

This project exposes an admin endpoint for compute + Telegram send:

- `POST /admin/oracle/run-and-send`

Use the script below to authenticate as admin and trigger it:

- `scripts/run_fast_trigger.py`

The script reads:

- `APP_URL`
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD`
- `ORACLE_SYMBOL` (optional, default `XAUUSD`)

## MT5 install (Windows)

```powershell
pip install MetaTrader5
```

## Windows Task Scheduler

1. Open Task Scheduler -> Create Task.
2. Trigger:
   - Daily
   - Time: your private trigger time
   - Time zone: `(UTC+00:00) Dublin, Edinburgh, Lisbon, London`
3. Action:
   - Program/script: `F:\Trading Intelligence SaaS\.venv\Scripts\python.exe`
   - Add arguments: `F:\Trading Intelligence SaaS\scripts\run_fast_trigger.py`
   - Start in: `F:\Trading Intelligence SaaS`
4. In task environment/user context, ensure `ADMIN_EMAIL` and `ADMIN_PASSWORD` are available.

## Linux cron example

```cron
1 8 * * * cd /path/to/Trading\ Intelligence\ SaaS && /path/to/.venv/bin/python scripts/run_fast_trigger.py >> /var/log/oracle_fast_trigger.log 2>&1
```
