# MT4 `signal.json` Schema (Oracle Execution Channel)

The MT4 EA must only consume this file and execute/manage trades safely.
Strategy logic remains on the backend Oracle side.

## Top-level object

```json
{
  "schema_version": "1.0",
  "instruction_id": "uuid",
  "enabled": true,
  "created_at_utc": "2026-02-15T06:00:00+00:00",
  "expires_at_utc": "2026-02-15T06:15:00+00:00",
  "symbol": "XAUUSD",
  "side": "BUY",
  "entry_zone": {
    "min": 2312.10,
    "max": 2312.45,
    "order_type": "LIMIT_ZONE"
  },
  "sl": 2309.90,
  "tp1": 2315.35,
  "tp2": 2317.20,
  "permission_layers": {
    "quarterly_permission": "BUY_ONLY",
    "daily_bias": "BUY_ONLY",
    "m15_confirm_ok": true,
    "volume_state": "normal",
    "atr_h1": 1.73,
    "alignment": "ALIGNED",
    "session_window": "london",
    "tier_gate": "elite_only"
  },
  "risk_parameters": {
    "max_risk_percent": 0.5,
    "max_positions": 1,
    "max_spread_points": 45,
    "max_risk_points": 25.0,
    "tp1_r_mult": 1.5,
    "tp2_r_mult": 2.4,
    "risk_points": 1.42,
    "snapshot_confidence": 0.74
  },
  "comment": "Oracle-validated execution instruction. EA must execute/manage only.",
  "meta": {
    "snapshot_run_id": "uuid",
    "snapshot_as_of_utc": "2026-02-15T05:45:00+00:00",
    "snapshot_status": "confirmed",
    "requested_session": "auto",
    "active_session": "london",
    "target_tier": "elite",
    "reasons": []
  },
  "writer": {
    "source": "mt4_signal_writer",
    "written_at_utc": "2026-02-15T06:00:01+00:00"
  }
}
```

## Disabled instruction

When `enabled` is `false`, no action should be taken by EA. The `meta.reasons` list explains why.
