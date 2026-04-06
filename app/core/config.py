from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from urllib.parse import urlsplit

from app.core.db_url import normalize_database_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/tradeos"
    APP_ENV: str = "development"
    API_VERSION_PREFIX: str = "/api/v1"
    CORS_ALLOW_ORIGINS: str = ""
    CORS_ORIGINS: str = ""
    CORS_ALLOW_CREDENTIALS: bool = False
    TRUSTED_HOSTS: str = ""
    REQUEST_MAX_BODY_BYTES: int = 1_048_576
    WEBHOOK_MAX_BODY_BYTES: int = 2_097_152
    REQUEST_TIMEOUT_SECONDS: int = 30
    SECURITY_HSTS_SECONDS: int = 31_536_000
    RATE_LIMIT_ENABLED: bool = True
    REDIS_URL: str | None = None

    MARKET_DATA_PROVIDER: str = "mt5"
    MARKET_INGEST_HEARTBEAT_ENABLED: bool = False
    ORACLE_SCHEDULER_IN_API: bool = False
    ORACLE_SYMBOL: str = "XAUUSD"
    ORACLE_TIMEFRAME: str = "M1"
    ORACLE_ENABLED_SYMBOLS: str = "XAUUSD,GBPUSD,EURUSD,GBPJPY,BTCUSD"
    ORACLE_LONDON_OPEN_HOUR: int = 8

    MT5_TERMINAL_PATH: str | None = None
    MT5_LOGIN: int | None = None
    MT5_PASSWORD: str | None = None
    MT5_SERVER: str | None = None
    RUNNER_API_KEY: str = ""
    RUNNER_ALLOWED_IPS: str = ""
    RUNNER_IP_ALLOWLIST: str = ""
    RUNNER_REQUIRE_IP_ALLOWLIST: bool = False
    RUNNER_TRUST_PROXY_HEADERS: bool = False
    RUNNER_CONTROL_URL: str = ""
    RUNNER_CONTROL_TIMEOUT_SECONDS: int = 10
    RUNNER_CONTROL_VERIFY_TLS: bool = True
    RUNNER_HEARTBEAT_STALE_SECONDS: int = 180

    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_BASIC: str = ""
    STRIPE_PRICE_PRO: str = ""
    STRIPE_PRICE_ELITE: str = ""
    STRIPE_PRICE_ID_BASIC: str = ""
    STRIPE_PRICE_ID_PRO: str = ""
    STRIPE_PRICE_ID_ELITE: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    SIGNAL_API_TOKEN: str = ""

    JWT_SECRET: str = "dev-secret-change-me"
    JWT_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_EXPIRE_DAYS: int = 14
    JWT_ISSUER: str | None = None
    JWT_AUDIENCE: str | None = None
    ALLOW_PUBLIC_REGISTRATION: bool = False
    ACCOUNT_ACTIVATION_TOKEN_TTL_MINUTES: int = 120

    APP_URL: str = "http://127.0.0.1:8000"
    FRONTEND_URL: str = "http://127.0.0.1:3000"
    BACKEND_API_URL: str = ""
    SIGNAL_POST_TIMEOUT_SECONDS: int = 10

    ORACLE_DAILY_EMA_PERIOD: int = 20
    ORACLE_CONFIRM_EMA_PERIOD: int = 50
    ORACLE_VOLUME_LOOKBACK: int = 20
    ORACLE_VOLUME_LOW_MULT: float = 0.7
    ORACLE_VOLUME_HIGH_MULT: float = 1.6
    ORACLE_ATR_H1_MIN: float = 0.5
    ORACLE_ATR_H1_MAX: float = 60.0
    ORACLE_ADR_D1_MIN: float = 5.0
    ORACLE_ADR_D1_MAX: float = 300.0
    ORACLE_H1_VOLATILITY_RATIO_MAX: float = 3.0
    ORACLE_NEWS_WINDOWS_JSON: str = "[]"
    ORACLE_NEWS_BLOCK_MINUTES: int = 30
    ORACLE_CONFIRM_DELAY_MINUTES: int = 15
    ORACLE_PRICE_MONITOR_INTERVAL_SECONDS: int = 120
    ORACLE_DAILY_AUDIT_HOUR: int = 21
    ORACLE_DAILY_AUDIT_MINUTE: int = 0
    ORACLE_ASIA_START_HOUR: int = 0
    ORACLE_ASIA_END_HOUR: int = 6
    ORACLE_ASIA_VOLUME_SPIKE_MULT: float = 1.8
    ORACLE_EXEC_DEFAULT_TTL_SECONDS: int = 900
    ORACLE_EXEC_MIN_TTL_SECONDS: int = 60
    ORACLE_EXEC_MAX_TTL_SECONDS: int = 7200
    ORACLE_EXEC_LONDON_START_HOUR: int = 7
    ORACLE_EXEC_LONDON_END_HOUR: int = 12
    ORACLE_EXEC_NEWYORK_START_HOUR: int = 13
    ORACLE_EXEC_NEWYORK_END_HOUR: int = 18
    ORACLE_EXEC_ALLOW_OFF_SESSION: bool = False
    ORACLE_EXEC_MAX_RISK_PERCENT: float = 0.5
    ORACLE_EXEC_MAX_RISK_POINTS: float = 25.0
    ORACLE_EXEC_MAX_SPREAD_POINTS: int = 45
    ORACLE_EXEC_MAX_POSITIONS: int = 1
    ORACLE_EXEC_ENTRY_BUFFER_ATR_MULT: float = 0.20
    ORACLE_EXEC_SL_ATR_MULT: float = 0.85
    ORACLE_EXEC_TP1_R_MULT: float = 1.5
    ORACLE_EXEC_TP2_R_MULT: float = 2.4
    BILLING_RENEWAL_REMINDER_HOUR: int = 9
    BILLING_RENEWAL_REMINDER_MINUTE: int = 0
    ORACLE_M15_MANIPULATION_LOOKBACK: int = 20
    ORACLE_M15_MANIPULATION_Z_WINDOW: int = 96
    STRATEGY_MATRIX_ENABLE_ELITE_LIQ_SWEEP_EXPANSION: bool = False
    STRATEGY_MATRIX_ENABLE_ELITE_ZONE_TO_ZONE_EXPANSION: bool = False
    AUTOTRADE_DEFAULT_VOLUME: float = 0.01
    AUTOTRADE_MAX_VOLUME: float = 0.10
    AUTOTRADE_MAX_TRADES_PER_DAY: int = 3
    AUTOTRADE_MAX_OPEN_TRADES_PER_SYMBOL: int = 1
    AUTOTRADE_BLOCK_HIGH_RISK: bool = True
    AUTOTRADE_NEWS_BLOCK_ENABLED: bool = True
    AUTOTRADE_ENABLED: bool = False
    AUTOTRADE_ADMIN_EMAIL: str = ""
    

    @field_validator("MT5_TERMINAL_PATH", "MT5_PASSWORD", "MT5_SERVER", mode="before")
    @classmethod
    def empty_string_to_none_for_strings(cls, value: str | None):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("MT5_LOGIN", mode="before")
    @classmethod
    def empty_string_to_none_for_int(cls, value):
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url_value(cls, value: str | None):
        return normalize_database_url(value or "")

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in {"prod", "production"}

    @property
    def cors_origins(self) -> list[str]:
        origins = [self.FRONTEND_URL.strip()]
        raw = ",".join([self.CORS_ALLOW_ORIGINS, self.CORS_ORIGINS])
        if raw:
            for value in raw.split(","):
                item = value.strip()
                if item:
                    origins.append(item)
        unique: list[str] = []
        for item in origins:
            if not item:
                continue
            for candidate in self._expand_loopback_origin_variants(item):
                if candidate and candidate not in unique:
                    unique.append(candidate)
        return unique

    @staticmethod
    def _expand_loopback_origin_variants(origin: str) -> list[str]:
        clean = (origin or "").strip()
        if not clean:
            return []
        parsed = urlsplit(clean)
        host = (parsed.hostname or "").strip().lower()
        if host not in {"localhost", "127.0.0.1"}:
            return [clean]

        scheme = parsed.scheme or "http"
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path or ""
        if path == "/":
            path = ""
        candidates = [
            f"{scheme}://127.0.0.1{port}{path}",
            f"{scheme}://localhost{port}{path}",
        ]
        return list(dict.fromkeys(candidates))

    @property
    def docs_enabled(self) -> bool:
        return not self.is_production

    @property
    def runner_allowed_ips(self) -> set[str]:
        values: set[str] = set()
        raw = ",".join([self.RUNNER_ALLOWED_IPS, self.RUNNER_IP_ALLOWLIST])
        for value in raw.split(","):
            ip = value.strip()
            if ip:
                values.add(ip)
        return values

    @property
    def trusted_hosts(self) -> list[str]:
        values: list[str] = ["127.0.0.1", "localhost"]
        for raw_url in (self.APP_URL, self.FRONTEND_URL):
            parsed = urlsplit((raw_url or "").strip())
            host = parsed.hostname or ""
            if host and host not in values:
                values.append(host)
        for value in self.TRUSTED_HOSTS.split(","):
            host = value.strip()
            if host and host not in values:
                values.append(host)
        return values

    @property
    def stripe_key_mode(self) -> str:
        key = self.STRIPE_SECRET_KEY.strip()
        if key.startswith("sk_live"):
            return "live"
        if key.startswith("sk_test"):
            return "test"
        return "unknown"

    @property
    def stripe_price_basic(self) -> str:
        return (self.STRIPE_PRICE_ID_BASIC or self.STRIPE_PRICE_BASIC).strip()

    @property
    def stripe_price_pro(self) -> str:
        return (self.STRIPE_PRICE_ID_PRO or self.STRIPE_PRICE_PRO).strip()

    @property
    def stripe_price_elite(self) -> str:
        return (self.STRIPE_PRICE_ID_ELITE or self.STRIPE_PRICE_ELITE).strip()

    def validate_runtime(self) -> None:
        if self.STRIPE_SECRET_KEY.strip():
            if not self.is_production and self.stripe_key_mode == "live":
                raise RuntimeError(
                    "Unsafe Stripe configuration: APP_ENV is development but STRIPE_SECRET_KEY is live (sk_live...). "
                    "Use sk_test key in development."
                )
            if self.is_production and self.stripe_key_mode != "live":
                raise RuntimeError(
                    "Unsafe Stripe configuration: production requires STRIPE_SECRET_KEY with sk_live prefix."
                )
            for key, value in (
                ("STRIPE_PRICE_ID_BASIC", self.stripe_price_basic),
                ("STRIPE_PRICE_ID_PRO", self.stripe_price_pro),
                ("STRIPE_PRICE_ID_ELITE", self.stripe_price_elite),
            ):
                if value and not value.startswith("price_"):
                    raise RuntimeError(f"Invalid {key}: expected a Stripe price id starting with 'price_'.")

        if self.AUTOTRADE_ENABLED and not self.AUTOTRADE_ADMIN_EMAIL.strip():
            raise RuntimeError("AUTOTRADE_ENABLED requires AUTOTRADE_ADMIN_EMAIL to be set.")

        if not self.is_production:
            return
        missing: list[str] = []
        for key in (
            "DATABASE_URL",
            "APP_URL",
            "FRONTEND_URL",
            "JWT_SECRET",
            "RUNNER_API_KEY",
            "STRIPE_SECRET_KEY",
            "STRIPE_WEBHOOK_SECRET",
            "TELEGRAM_BOT_TOKEN",
        ):
            value = getattr(self, key, None)
            if isinstance(value, str):
                if not value.strip():
                    missing.append(key)
            elif value is None:
                missing.append(key)
        if self.JWT_SECRET.strip() == "dev-secret-change-me" or len(self.JWT_SECRET.strip()) < 32:
            missing.append("JWT_SECRET(strong)")
        if "localhost" in self.APP_URL or "127.0.0.1" in self.APP_URL:
            missing.append("APP_URL(non-localhost)")
        if "localhost" in self.FRONTEND_URL or "127.0.0.1" in self.FRONTEND_URL:
            missing.append("FRONTEND_URL(non-localhost)")
        if missing:
            raise RuntimeError(f"Missing required production security settings: {', '.join(sorted(set(missing)))}")


settings = Settings()
