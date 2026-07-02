"""Migrations build the whole schema, are idempotent, and match the models.

These run synchronously (no ``async def``) so Alembic's own ``asyncio.run`` in
env.py has a clean event loop to drive the async engine.
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.db import Base
from app import models  # noqa: F401 — populate metadata
from app.migrations import _config, upgrade_head_sync


def _head_revision() -> str:
    return ScriptDirectory.from_config(_config("sqlite://")).get_current_head()


def _sqlite_path(tmp_path) -> tuple[str, str]:
    db_file = tmp_path / "migrated.db"
    return f"sqlite+aiosqlite:///{db_file}", str(db_file)


def test_upgrade_head_builds_every_table(tmp_path):
    dsn, db_file = _sqlite_path(tmp_path)
    upgrade_head_sync(dsn)

    conn = sqlite3.connect(db_file)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()

    for expected in Base.metadata.tables:
        assert expected in tables, f"migration did not create {expected!r}"
    assert version[0] == _head_revision()


def test_upgrade_head_is_idempotent(tmp_path):
    dsn, _ = _sqlite_path(tmp_path)
    upgrade_head_sync(dsn)
    # Running again against an already-migrated database is a no-op, not an error.
    upgrade_head_sync(dsn)


def test_schema_matches_models_no_drift(tmp_path):
    """Autogenerate against the migrated database must detect no changes."""
    dsn, db_file = _sqlite_path(tmp_path)
    upgrade_head_sync(dsn)

    sync_engine = create_engine(f"sqlite:///{db_file}")
    try:
        with sync_engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "render_as_batch": True},
            )
            diffs = compare_metadata(context, Base.metadata)
    finally:
        sync_engine.dispose()

    assert diffs == [], f"schema drift between migrations and models: {diffs}"
    assert make_url(dsn).get_backend_name() == "sqlite"
