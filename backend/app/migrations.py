"""Apply Alembic migrations programmatically.

Phase 1 created tables with ``metadata.create_all`` for first-run simplicity.
In production the schema evolves, so the control plane now owns a real migration
history and brings the database to ``head`` on startup.

``apply_migrations`` runs the upgrade in a worker thread: Alembic's command layer
is synchronous and env.py drives it with ``asyncio.run``, which cannot be nested
inside the already-running event loop of the app lifespan. Running it off-thread
keeps the async server and the sync migration machinery from colliding.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def _alembic_dir() -> Path:
    """Find the directory that holds ``alembic.ini`` and ``alembic/``.

    Robust across layouts: an editable install (source lives in ``backend/``), a
    non-editable install in the image (``app`` in site-packages but the source
    tree copied to the working directory), and an explicit override.
    """
    candidates = []
    override = os.environ.get("ACP_ALEMBIC_DIR")
    if override:
        candidates.append(Path(override))
    candidates.append(Path(__file__).resolve().parents[1])  # editable: backend/
    candidates.append(Path.cwd())  # image WORKDIR that holds the copied source
    for base in candidates:
        if (base / "alembic.ini").is_file() and (base / "alembic").is_dir():
            return base
    raise FileNotFoundError(
        "could not locate alembic.ini; set ACP_ALEMBIC_DIR to the directory "
        "that contains it and the alembic/ scripts, then rerun the failed "
        "control-plane command"
    )


def _config(dsn: str) -> Config:
    base = _alembic_dir()
    cfg = Config(str(base / "alembic.ini"))
    cfg.set_main_option("script_location", str(base / "alembic"))
    # env.py reads the target URL from here so no DSN lives in a committed file.
    cfg.attributes["dsn"] = dsn
    return cfg


def upgrade_head_sync(dsn: str) -> None:
    command.upgrade(_config(dsn), "head")


async def apply_migrations(dsn: str) -> None:
    await asyncio.to_thread(upgrade_head_sync, dsn)
