from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(String, nullable=False, server_default=text("''"))
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    role = Column(Enum("admin", "user", name="user_roles"), default="user")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AccountActivationToken(Base):
    __tablename__ = "account_activation_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_account_activation_tokens_token_hash"),
        Index("ix_account_activation_tokens_user_id", "user_id"),
        Index("ix_account_activation_tokens_expires_at", "expires_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    token_hash = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
        Index("ix_refresh_tokens_revoked_at", "revoked_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    token_hash = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    replaced_by_token_hash = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Subscription(Base):
    __tablename__ = "subscriptions"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    status = Column(String, nullable=False, server_default="inactive")
    plan = Column(String, nullable=False, server_default="basic")
    current_period_end = Column(DateTime, nullable=True)
    last_renewal_reminder_at = Column(DateTime(timezone=True), nullable=True)
    usage_count = Column(Integer, nullable=False, server_default="0")
    usage_reset_at = Column(DateTime, nullable=True)
    autotrade_enabled = Column(Boolean, nullable=False, server_default=text("false"))


class NotificationRoute(Base):
    __tablename__ = "notification_routes"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    email_enabled = Column(Boolean, default=True)
    telegram_enabled = Column(Boolean, default=False)
    telegram_chat_id = Column(String, nullable=True)
    telegram_pin_daily_bias = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserSignalPref(Base):
    __tablename__ = "user_signal_prefs"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    symbols_json = Column(JSON, nullable=False, default=list)
    telegram_enabled = Column(Boolean, nullable=False, server_default=text("false"))
    telegram_chat_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class MarketStateDaily(Base):
    __tablename__ = "market_state_daily"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String)
    date_uk = Column(DateTime)
    allowed_direction = Column(String)  # BUY_ONLY | SELL_ONLY | NO_TRADE
    internal_bias_json = Column(JSON)  # ADMIN ONLY
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SignalEvent(Base):
    __tablename__ = "signal_events"
    __table_args__ = (
        Index(
            "uq_signal_events_symbol_asof_tiermin_dispatch",
            "symbol",
            "snapshot_as_of_utc",
            "tier_min",
            unique=True,
            postgresql_where=text("snapshot_as_of_utc IS NOT NULL AND tier_min IS NOT NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    symbol = Column(String)
    status = Column(String)  # ALLOWED | BLOCKED
    tier_min = Column(String, nullable=True)
    snapshot_as_of_utc = Column(DateTime(timezone=True), nullable=True)
    dispatch_kind = Column(String, nullable=True)
    public_reason_json = Column(JSON)  # USER SAFE
    internal_reason_json = Column(JSON)  # ADMIN ONLY
    event_time = Column(DateTime(timezone=True), server_default=func.now())


class LiquiditySignal(Base):
    __tablename__ = "liquidity_signals"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_liquidity_signals_dedup_key"),
        Index("ix_liquidity_signals_detected_at", "detected_at"),
        Index("ix_liquidity_signals_symbol_timeframe_detected", "symbol", "timeframe", "detected_at"),
        Index("ix_liquidity_signals_symbol_type_detected", "symbol", "signal_type", "detected_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    signal_type = Column(String(40), nullable=False)
    direction = Column(String(20), nullable=True)
    magnet_level = Column(Float, nullable=True)
    price = Column(Float, nullable=True)
    bias = Column(String(20), nullable=True)
    source = Column(String(64), nullable=False, server_default="unknown")
    detected_at = Column(DateTime(timezone=True), nullable=False)
    meta_json = Column(JSON, nullable=False, default=dict)
    dedup_key = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DeliveryLog(Base):
    __tablename__ = "delivery_log"
    __table_args__ = (
        Index("ix_delivery_log_user_created", "user_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    symbol = Column(String, nullable=False)
    channel = Column(String, nullable=False, server_default="telegram")
    source = Column(String, nullable=False)  # oracle_run | ingest_mt5
    tier = Column(String, nullable=True)
    subscription_status = Column(String, nullable=True)
    send_status = Column(String, nullable=False)  # SENT | FAILED | SKIPPED
    consume_status = Column(String, nullable=False, server_default="NOT_ATTEMPTED")
    detail = Column(String, nullable=True)
    context_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UsageLedger(Base):
    __tablename__ = "usage_ledger"
    __table_args__ = (
        UniqueConstraint("user_id", "signal_id", name="uq_usage_ledger_user_signal"),
        Index("ix_usage_ledger_user_created", "user_id", "created_at"),
        Index("ix_usage_ledger_created_at", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    tier = Column(String, nullable=False)
    symbol = Column(String, nullable=True)
    reason = Column(String, nullable=False)
    signal_id = Column(String, nullable=True)
    quantity = Column(Integer, nullable=False, server_default="1")
    meta_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MT5Candle(Base):
    __tablename__ = "mt5_candles"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "timeframe",
            "time_utc",
            name="uq_mt5_candles_symbol_tf_time",
        ),
        Index("ix_mt5_candles_symbol_tf_time", "symbol", "timeframe", "time_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False)
    time_utc = Column(DateTime(timezone=True), nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MT5IngestStatus(Base):
    __tablename__ = "mt5_ingest_status"
    __table_args__ = (
        Index("ix_mt5_ingest_status_last_ingested_at", "last_ingested_at"),
    )

    symbol = Column(String, primary_key=True)
    last_ingested_at = Column(DateTime(timezone=True), nullable=False)
    broker_offset_seconds = Column(Integer, nullable=True)
    broker_offset_detected_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class OracleTargetsSnapshot(Base):
    __tablename__ = "oracle_targets_snapshot"
    __table_args__ = (
        Index("ix_oracle_targets_snapshot_as_of_utc", "as_of_utc"),
        Index("ix_oracle_targets_snapshot_symbol_tier_asof", "symbol", "tier", "as_of_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False)
    tier = Column(String, nullable=False, server_default="pro")
    timeframe_base = Column(String, nullable=False, server_default="H1")
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    price_bid = Column(Float, nullable=True)
    price_ask = Column(Float, nullable=True)
    magnet_price = Column(Float, nullable=False)
    zone_to_zone_target = Column(Float, nullable=False)
    sellside_liquidity = Column(Float, nullable=False)
    buyside_liquidity = Column(Float, nullable=False)
    magnet_state = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DailyPermissionSnapshot(Base):
    __tablename__ = "daily_permission_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "date_uk", "daily_permission_stage", name="uq_daily_permission_symbol_date_stage"),
        Index("ix_daily_permission_symbol_date", "symbol", "date_uk"),
        Index("ix_daily_permission_symbol_date_stage", "symbol", "date_uk", "daily_permission_stage"),
        Index("ix_daily_permission_symbol_asof", "symbol", "as_of_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False)
    date_uk = Column(Date, nullable=False)
    for_date = Column(Date, nullable=False)
    timeframe = Column(String, nullable=False, server_default="M1")
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    computed_at_utc = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    daily_permission = Column(String, nullable=False)  # BUY_ONLY | SELL_ONLY | NO_TRADE
    daily_permission_stage = Column(String, nullable=False, server_default="OFFICIAL")  # PRELIM | OFFICIAL
    permission_source = Column(String, nullable=False, server_default="LONDON_0801")  # ASIA | LONDON_0801
    official = Column(Boolean, nullable=False, server_default=text("false"))
    confidence = Column(Float, nullable=True)
    reasons_json = Column(JSON, nullable=False, default=list)
    reason = Column(String, nullable=True)
    spread = Column(Float, nullable=True)
    volatility = Column(Float, nullable=True)
    is_extreme = Column(Boolean, nullable=False, server_default=text("false"))
    factors_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class OracleMagnetState(Base):
    __tablename__ = "oracle_magnet_state"
    __table_args__ = (
        Index("ix_oracle_magnet_state_asof", "as_of_utc"),
    )

    symbol = Column(String, primary_key=True)
    timeframe_base = Column(String, nullable=False, server_default="H1")
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    magnet_price = Column(Float, nullable=False)
    magnet_side = Column(String, nullable=False)
    zone_to_zone_target = Column(Float, nullable=False)
    sellside_liquidity = Column(Float, nullable=False)
    buyside_liquidity = Column(Float, nullable=False)
    state_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class GoldRegimeDaily(Base):
    __tablename__ = "gold_regime_daily"
    __table_args__ = (
        UniqueConstraint("symbol", "as_of_utc", name="uq_gold_regime_daily_symbol_as_of"),
        Index("ix_gold_regime_daily_symbol_as_of", "symbol", "as_of_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False, default="XAUUSD")
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    regime = Column(String, nullable=False)  # bullish | neutral | bearish
    confidence = Column(Float, nullable=False)  # 0.00 - 1.00
    allowed_direction = Column(String, nullable=False)  # BUY_ONLY | SELL_ONLY | NO_TRADE
    final_allowed_basic = Column(String, nullable=True)
    final_allowed_elite = Column(String, nullable=True)
    daily_bias = Column(String, nullable=True)
    confirm_ok = Column(Boolean, nullable=True)
    public_factors_json = Column(JSON, nullable=False, default=dict)
    internal_factors_json = Column(JSON, nullable=False, default=dict)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GoldPositioningSnapshot(Base):
    __tablename__ = "gold_positioning_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "as_of_utc",
            name="uq_gold_positioning_snapshot_symbol_as_of",
        ),
        Index("ix_gold_positioning_snapshot_symbol_as_of", "symbol", "as_of_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False, default="XAUUSD")
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    cot_net_non_commercial = Column(Integer, nullable=True)
    comex_open_interest = Column(Integer, nullable=True)
    gld_flow_tonnes = Column(Float, nullable=True)
    iau_flow_tonnes = Column(Float, nullable=True)
    crowding_score = Column(Float, nullable=False)  # 0 - 100
    positioning_bias = Column(String, nullable=False)  # bullish | neutral | bearish
    squeeze_risk = Column(String, nullable=False)  # low | medium | high
    contra_signal = Column(Boolean, nullable=False, default=False)
    public_factors_json = Column(JSON, nullable=False, default=dict)
    internal_factors_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GoldStressIntraday(Base):
    __tablename__ = "gold_stress_intraday"
    __table_args__ = (
        UniqueConstraint("symbol", "as_of_utc", name="uq_gold_stress_intraday_symbol_as_of"),
        Index("ix_gold_stress_intraday_symbol_as_of", "symbol", "as_of_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False, default="XAUUSD")
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    basis_bps = Column(Float, nullable=True)
    front_month_spread_bps = Column(Float, nullable=True)
    spread_volatility_bps = Column(Float, nullable=True)
    inventory_stress_score = Column(Float, nullable=True)
    stress_score = Column(Float, nullable=False)  # 0 - 100
    state = Column(String, nullable=False)  # green | amber | red
    execution_guidance = Column(String, nullable=False)  # normal | reduce_size | avoid
    public_factors_json = Column(JSON, nullable=False, default=dict)
    internal_factors_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OracleRun(Base):
    __tablename__ = "oracle_runs"
    __table_args__ = (
        Index("ix_oracle_runs_as_of_utc", "as_of_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False, server_default="XAUUSD")
    timeframe = Column(String, nullable=False, server_default="H1")
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    bias = Column(String, nullable=False)  # BUY_ONLY | SELL_ONLY | NO_TRADE
    confidence = Column(Float, nullable=False)
    manipulation_score = Column(Integer, nullable=False, server_default=text("0"))
    manipulation_level = Column(String, nullable=False, server_default="low")
    internal_json = Column(JSON, nullable=False, default=dict)
    public_json = Column(JSON, nullable=False, default=dict)
    status = Column(String, nullable=False, server_default="candidate")  # candidate | confirmed | sent | skipped
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OracleConfirmation(Base):
    __tablename__ = "oracle_confirmations"
    __table_args__ = (
        Index("ix_oracle_confirmations_run_id", "run_id"),
        Index("ix_oracle_confirmations_as_of_utc", "as_of_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("oracle_runs.id"), nullable=False)
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    confirm_ok = Column(Boolean, nullable=False)
    confirm_reason_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OracleQuarterlySnapshot(Base):
    __tablename__ = "oracle_quarterly_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "quarter_key", name="uq_oracle_quarterly_snapshots_symbol_quarter"),
        Index("ix_oracle_quarterly_snapshots_symbol_quarter", "symbol", "quarter_key"),
        Index("ix_oracle_quarterly_snapshots_symbol_asof", "symbol", "as_of_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False)
    quarter_key = Column(String, nullable=False)
    quarter_open = Column(Float, nullable=False)
    q_high = Column(Float, nullable=False)
    q_low = Column(Float, nullable=False)
    q_mid = Column(Float, nullable=False)
    premium_discount = Column(String, nullable=False)  # premium | discount | near_open
    quarterly_bias = Column(String, nullable=False)  # BUY_ONLY | SELL_ONLY | BOTH | NO_TRADE
    permission_mode = Column(String, nullable=False)  # STRICT | SOFT
    conflict_rule = Column(String, nullable=False)  # BLOCK_COUNTER | DOWNGRADE_COUNTER
    confidence = Column(Float, nullable=False)
    factors_json = Column(JSON, nullable=False, default=dict)
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OraclePermissionDaily(Base):
    __tablename__ = "oracle_permission_daily"
    __table_args__ = (
        UniqueConstraint("symbol", "date_uk", name="uq_oracle_permission_daily_symbol_date"),
        Index("ix_oracle_permission_daily_symbol_date", "symbol", "date_uk"),
        Index("ix_oracle_permission_daily_symbol_asof", "symbol", "as_of_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False)
    date_uk = Column(Date, nullable=False)
    daily_bias_raw = Column(String, nullable=False)  # BUY_ONLY | SELL_ONLY | NO_TRADE
    quarterly_bias = Column(String, nullable=False)  # BUY_ONLY | SELL_ONLY | BOTH | NO_TRADE
    allowed_direction_final = Column(String, nullable=False)  # BUY_ONLY | SELL_ONLY | BOTH | NO_TRADE
    alignment = Column(String, nullable=False)  # ALIGNED | CONFLICT | NEUTRAL
    confidence_final = Column(Float, nullable=False)
    message_tag = Column(String, nullable=False)  # TREND_DAY_OK | COUNTERTREND_CAUTION | NO_TRADE_FILTER
    details_json = Column(JSON, nullable=False, default=dict)
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TelegramThreadState(Base):
    __tablename__ = "telegram_thread_state"
    __table_args__ = (
        UniqueConstraint("date_uk", "symbol", "chat_id", name="uq_telegram_thread_state_date_symbol_chat"),
        Index("ix_telegram_thread_state_date_symbol", "date_uk", "symbol"),
        Index("ix_telegram_thread_state_chat", "chat_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date_uk = Column(Date, nullable=False)
    symbol = Column(String, nullable=False)
    pinned_message_id = Column(Integer, nullable=False)
    chat_id = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WeeklyRangeSnapshot(Base):
    __tablename__ = "weekly_range_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "week_key", name="uq_weekly_range_snapshots_symbol_week"),
        Index("ix_weekly_range_snapshots_symbol_week", "symbol", "week_key"),
        Index("ix_weekly_range_snapshots_symbol_asof", "symbol", "as_of_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False)
    week_key = Column(String, nullable=False)
    week_start_uk = Column(Date, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    mid = Column(Float, nullable=False)
    range_ready = Column(Boolean, nullable=False, server_default=text("false"))
    as_of_utc = Column(DateTime(timezone=True), nullable=False)
    meta_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TelegramThread(Base):
    __tablename__ = "telegram_threads"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "date_uk", name="uq_telegram_threads_user_symbol_date"),
        Index("ix_telegram_threads_user_id", "user_id"),
        Index("ix_telegram_threads_date_uk", "date_uk"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    symbol = Column(String, nullable=False, server_default="XAUUSD")
    date_uk = Column(Date, nullable=False)
    anchor_message_id = Column(Integer, nullable=False)
    update_count = Column(Integer, nullable=False, server_default=text("0"))
    pinned = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SignalDelivery(Base):
    __tablename__ = "signal_deliveries"
    __table_args__ = (
        UniqueConstraint("user_id", "run_id", name="uq_signal_deliveries_user_run"),
        Index("ix_signal_deliveries_user_id", "user_id"),
        Index("ix_signal_deliveries_run_id", "run_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    run_id = Column(UUID(as_uuid=True), ForeignKey("oracle_runs.id"), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    channel = Column(String, nullable=False, server_default="telegram")
    status = Column(String, nullable=False)  # sent | failed | skipped
    error_text = Column(String, nullable=True)


class StripeWebhookEvent(Base):
    __tablename__ = "stripe_webhook_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_stripe_webhook_events_event_id"),
        Index("ix_stripe_webhook_events_received_at", "received_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    processed = Column(Boolean, nullable=False, server_default=text("false"))
    received_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)


class StripeWebhookIdempotency(Base):
    __tablename__ = "stripe_webhook_idempotency"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_stripe_webhook_idempotency_event_id"),
        Index("ix_stripe_webhook_idempotency_seen_at", "seen_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(String, nullable=False)
    seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_action_ts", "action", "ts"),
        Index("ix_audit_logs_ts", "ts"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    action = Column(String, nullable=False)
    ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    ts = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    meta_json = Column(JSON, nullable=False, default=dict)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_ip_ts", "ip", "ts"),
        Index("ix_login_attempts_email_ts", "email", "ts"),
        Index("ix_login_attempts_ts", "ts"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ip = Column(String, nullable=True)
    email = Column(String, nullable=True)
    success = Column(Boolean, nullable=False)
    user_agent = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    ts = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class UserSymbolPreference(Base):
    __tablename__ = "user_symbol_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_user_symbol_preferences_user_symbol"),
        Index("ix_user_symbol_preferences_user_id", "user_id"),
        Index("ix_user_symbol_preferences_symbol", "symbol"),
        Index("ix_user_symbol_preferences_user_enabled", "user_id", "enabled"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    symbol = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, server_default=text("true"))
    autotrade_enabled = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class UserRiskSetting(Base):
    __tablename__ = "user_risk_settings"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    risk_mode = Column(String, nullable=False, server_default="fixed")  # fixed | percent
    risk_value = Column(Float, nullable=False, server_default=text("0.01"))
    max_trades_day = Column(Integer, nullable=False, server_default=text("3"))
    max_daily_loss = Column(Float, nullable=False, server_default=text("3.0"))
    max_open_trades = Column(Integer, nullable=False, server_default=text("1"))
    max_lot = Column(Float, nullable=False, server_default=text("0.10"))
    allowed_symbols_json = Column(JSON, nullable=False, default=list)
    avoid_mondays = Column(Boolean, nullable=False, server_default=text("false"))
    block_on_volume_spike = Column(Boolean, nullable=False, server_default=text("false"))
    news_filter_enabled = Column(Boolean, nullable=False, server_default=text("true"))
    news_block_minutes = Column(Integer, nullable=False, server_default=text("30"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class AutoTradeGlobalControl(Base):
    __tablename__ = "autotrade_global_control"

    id = Column(Integer, primary_key=True, server_default=text("1"))
    autotrade_enabled = Column(Boolean, nullable=False, server_default=text("false"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class AutoTradeSymbolControl(Base):
    __tablename__ = "autotrade_symbol_control"
    __table_args__ = (
        Index("ix_autotrade_symbol_control_enabled", "autotrade_enabled"),
    )

    symbol = Column(String, primary_key=True)
    autotrade_enabled = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TradeJob(Base):
    __tablename__ = "trade_jobs"
    __table_args__ = (
        Index("ix_trade_jobs_status_created", "status", "created_at"),
        Index("ix_trade_jobs_user_symbol", "user_id", "symbol"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    run_id = Column(UUID(as_uuid=True), ForeignKey("oracle_runs.id"), nullable=True, index=True)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)  # BUY | SELL
    volume = Column(Float, nullable=False)
    entry_type = Column(String, nullable=False, server_default="MARKET")  # MARKET | LIMIT | STOP
    entry_price = Column(Float, nullable=True)
    sl = Column(Float, nullable=True)
    tp = Column(Float, nullable=True)
    reason_json = Column(JSON, nullable=False, default=dict)
    status = Column(String, nullable=False, server_default="queued")  # queued | dispatched | filled | failed | canceled | blocked
    broker_runner_id = Column(String, nullable=True)
    sent_to_runner_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class TradeExec(Base):
    __tablename__ = "trade_exec"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_trade_exec_job_id"),
        Index("ix_trade_exec_status", "status"),
        Index("ix_trade_exec_broker_ticket", "broker_ticket"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("trade_jobs.id"), nullable=False)
    broker_ticket = Column(String, nullable=True)
    filled_price = Column(Float, nullable=True)
    status = Column(String, nullable=False)  # sent | filled | failed | canceled
    error = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)


class PositionState(Base):
    __tablename__ = "position_state"
    __table_args__ = (
        UniqueConstraint("user_id", "ticket", name="uq_position_state_user_ticket"),
        Index("ix_position_state_user_symbol", "user_id", "symbol"),
        Index("ix_position_state_updated_at", "updated_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    symbol = Column(String, nullable=False)
    ticket = Column(String, nullable=False)
    side = Column(String, nullable=False)  # BUY | SELL
    volume = Column(Float, nullable=False)
    entry = Column(Float, nullable=False)
    sl = Column(Float, nullable=True)
    tp = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RunnerHeartbeat(Base):
    __tablename__ = "runner_heartbeats"

    runner_id = Column(String, primary_key=True)
    version = Column(String, nullable=True)
    symbols_enabled_json = Column(JSON, nullable=False, default=list)
    last_ip = Column(String, nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class RunnerStatus(Base):
    __tablename__ = "runner_status"
    __table_args__ = (
        Index("ix_runner_status_last_ok_at", "last_ok_at"),
        Index("ix_runner_status_updated_at", "updated_at"),
    )

    runner_id = Column(String, primary_key=True)
    mt5_connected = Column(Boolean, nullable=False, server_default=text("false"))
    last_tick_utc = Column(DateTime(timezone=True), nullable=True)
    last_ingest_utc = Column(DateTime(timezone=True), nullable=True)
    symbols_ok_json = Column(JSON, nullable=False, default=list)
    last_error = Column(String, nullable=True)
    last_ok_at = Column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    last_signal_utc = Column(DateTime(timezone=True), nullable=True)
    last_telegram_sent_utc = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class OracleProcessingState(Base):
    __tablename__ = "oracle_processing_state"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", name="uq_oracle_processing_state_symbol_timeframe"),
        Index("ix_oracle_processing_state_last_processed", "last_processed_candle_utc"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String, nullable=False)
    timeframe = Column(String, nullable=False)  # M1 | M15 | H1
    last_processed_candle_utc = Column(DateTime(timezone=True), nullable=True)
    last_compute_at_utc = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_user_created", "user_id", "created_at"),
        Index("ix_audit_events_action_created", "action", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    symbol = Column(String, nullable=True)
    action = Column(String, nullable=False)
    allowed = Column(Boolean, nullable=False)
    reason_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Trade(Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_user_id", "user_id"),
        Index("ix_trades_date_uk", "date_uk"),
        Index("ix_trades_status", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    symbol = Column(String, nullable=False, server_default="XAUUSD")
    date_uk = Column(Date, nullable=False)
    tier = Column(String, nullable=False)
    direction = Column(String, nullable=False)  # BUY | SELL
    entry = Column(Float, nullable=True)
    sl = Column(Float, nullable=True)
    tp1 = Column(Float, nullable=True)
    tp2 = Column(Float, nullable=True)
    status = Column(String, nullable=False, server_default="OPEN")  # OPEN | CLOSED
    opened_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)
    result = Column(String, nullable=True)  # WIN | LOSS | BE
    rr_realized = Column(Float, nullable=True)
    reason_json = Column(JSON, nullable=True)


class TradeEvent(Base):
    __tablename__ = "trade_events"
    __table_args__ = (
        Index("ix_trade_events_trade_id", "trade_id"),
        Index("ix_trade_events_user_id", "user_id"),
        Index("ix_trade_events_symbol_created", "symbol", "created_at"),
        Index("ix_trade_events_created_at", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trade_id = Column(UUID(as_uuid=True), ForeignKey("trades.id"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    symbol = Column(String, nullable=True)
    event_type = Column(String, nullable=False)  # BIAS | SIGNAL | ENTRY | TP | SL | UPDATE | DAILY_AUDIT | ...
    tier_min = Column(String, nullable=True)  # basic | pro | elite
    title = Column(String, nullable=True)
    message = Column(String, nullable=True)
    meta_json = Column(JSON, nullable=False, default=dict)
    price = Column(Float, nullable=True)
    note = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
