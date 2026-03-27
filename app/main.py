from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import FastAPI
from app.db.base import Base
from app.db.session import engine

Base.metadata.create_all(bind=engine)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api.admin_gold import router as admin_gold_router
from app.api.admin_autotrade import router as admin_autotrade_router
from app.api.admin_ingest import router as admin_ingest_router
from app.api.admin_ops import router as admin_ops_router
from app.api.admin_oracle import router as admin_oracle_router
from app.api.admin_signals import router as admin_signals_router
from app.api.auth import router as auth_router
from app.api.billing import router as billing_router
from app.api.health import router as health_router
from app.api.ingest_mt5 import router as ingest_mt5_router
from app.api.intel_gold import router as intel_gold_router
from app.api.me import router as me_router
from app.api.notifications import router as notifications_router
from app.api.oracle import router as oracle_router
from app.api.oracle_elite import router as oracle_elite_router
from app.api.oracle_mt5 import router as oracle_mt5_router
from app.api.ops import router as ops_router
from app.api.runner import router as runner_router
from app.api.settings import router as settings_router
from app.api.signals import router as signals_router
from app.api.symbols import router as symbols_router
from app.api.usage import router as usage_router
from app.core.config import settings
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    RequestTimeoutMiddleware,
    SecurityHeadersMiddleware,
)
from app.services.oracle_scheduler import start_oracle_scheduler, stop_oracle_scheduler
from app.services.stripe import validate_price_catalog

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Trading Intelligence SaaS API",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)


def _all_routers():
    return [
        auth_router,
        me_router,
        billing_router,
        oracle_router,
        oracle_elite_router,
        oracle_mt5_router,
        signals_router,
        usage_router,
        notifications_router,
        settings_router,
        symbols_router,
        admin_autotrade_router,
        admin_ingest_router,
        admin_oracle_router,
        admin_signals_router,
        admin_ops_router,
        admin_gold_router,
        intel_gold_router,
        ops_router,
        ingest_mt5_router,
        runner_router,
        health_router,
    ]


for _router in _all_routers():
    app.include_router(_router)

# Versioned aliases for inventory clarity; legacy paths remain supported.
for _router in _all_routers():
    app.include_router(_router, prefix=settings.API_VERSION_PREFIX)

# Backward-compatible alias for signal ingestion clients that post to /api/signals.
app.include_router(signals_router, prefix="/api")
# Backward-compatible alias for runner clients that post/read from /api/runner/*.
app.include_router(runner_router, prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Runner-Key", "Stripe-Signature", "X-Request-ID"],
)
app.add_middleware(
    BodySizeLimitMiddleware,
    default_limit=settings.REQUEST_MAX_BODY_BYTES,
    webhook_limit=settings.WEBHOOK_MAX_BODY_BYTES,
)
app.add_middleware(RequestTimeoutMiddleware, timeout_seconds=settings.REQUEST_TIMEOUT_SECONDS)
app.add_middleware(SecurityHeadersMiddleware, hsts_seconds=settings.SECURITY_HSTS_SECONDS, enable_hsts=settings.is_production)
app.add_middleware(RequestContextMiddleware)


@app.on_event("startup")
def _startup() -> None:
    settings.validate_runtime()
    if settings.is_production and settings.STRIPE_SECRET_KEY.strip():
        price_checks = validate_price_catalog()
        failures = {plan: status for plan, status in price_checks.items() if status != "ok"}
        if failures:
            raise RuntimeError(
                f"Stripe price/key mode validation failed. Fix STRIPE_PRICE_* for current key mode: {failures}"
            )
    if settings.ORACLE_SCHEDULER_IN_API:
        try:
            start_oracle_scheduler()
        except Exception:
            logger.exception("Failed to start oracle scheduler.")
    else:
        logger.info("Oracle scheduler is disabled in API process (ORACLE_SCHEDULER_IN_API=false).")


@app.on_event("shutdown")
def _shutdown() -> None:
    if settings.ORACLE_SCHEDULER_IN_API:
        stop_oracle_scheduler()
