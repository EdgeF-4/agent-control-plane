"""Alembic environment.

The control plane applies migrations two ways, and this file supports both:

* On API startup the app calls :func:`app.migrations.apply_migrations`, which
  runs ``alembic upgrade head`` in a worker thread and hands the target database
  URL down through ``config.attributes['dsn']``.
* An operator can run ``alembic upgrade head`` from ``backend/`` directly; the
  URL is then read from the app settings (``ACP_CONFIG`` / ``config.json``).

Either way the URL is resolved at runtime — never stored in ``alembic.ini`` —
and the same async engine machinery the app uses drives the migration, so
Postgres (asyncpg) and SQLite (aiosqlite) both work unchanged. SQLite gets
``render_as_batch`` so column-altering migrations rebuild the table safely.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Importing the models populates Base.metadata for autogenerate/compare.
from app.db import Base
from app import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:  # pragma: no cover - logging config is best effort
        pass

target_metadata = Base.metadata


def _dsn() -> str:
    dsn = config.attributes.get("dsn")
    if dsn:
        return dsn
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from app.config import load_settings

    return load_settings().database.dsn()


def _is_sqlite(dsn: str) -> bool:
    return dsn.startswith("sqlite")


def run_migrations_offline() -> None:
    dsn = _dsn()
    context.configure(
        url=dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(dsn),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection, dsn: str) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=_is_sqlite(dsn),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    dsn = _dsn()
    engine = create_async_engine(dsn, poolclass=pool.NullPool, future=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run_migrations, dsn)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
