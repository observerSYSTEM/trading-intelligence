from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---- Path + .env loading ----
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

# ---- Alembic Config ----
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---- Import Base metadata ----
# Make sure these imports match your project
from app.db.base import Base  # noqa: E402  (Base = declarative_base() or SQLAlchemy Base)
from app.core.db_url import normalize_database_url  # noqa: E402
# IMPORTANT: import models so metadata is populated for autogenerate
import app.db.models  # noqa: F401, E402

target_metadata = Base.metadata

# ---- Force Alembic to use DATABASE_URL from .env ----
db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise RuntimeError("DATABASE_URL not set (expected in .env)")

config.set_main_option("sqlalchemy.url", normalize_database_url(db_url))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
