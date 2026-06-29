"""Async database access for the unified projection store."""

from __future__ import annotations

from collections.abc import AsyncIterator

import os
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every projection table."""


def _ensure_sqlite_dir(dsn: str) -> None:
    url = make_url(dsn)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


class Database:
    """Owns the engine and session factory; created once per process."""

    def __init__(self, dsn: str, *, echo: bool = False) -> None:
        _ensure_sqlite_dir(dsn)
        self.engine: AsyncEngine = create_async_engine(dsn, echo=echo, future=True)
        self.sessionmaker = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    async def create_all(self) -> None:
        """Create tables directly (used for tests and first-run convenience)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session
