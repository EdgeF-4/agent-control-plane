"""Application factory and lifespan wiring."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .bootstrap import bootstrap_tenant, refresh_ingest_keys, sync_budgets
from .config import Settings, load_settings
from .db import Database
from .engines import EngineHub
from .eval_service import run_due_schedules
from .errors import install_error_handlers
from .live import EventBus
from .migrations import apply_migrations
from .routers import admin, audit, auth, budgets, evals, live, overview, projects, runs, siem

_log = logging.getLogger("control_plane")


async def _eval_scheduler(db: Database, hub: EngineHub, tick_seconds: int) -> None:
    """Periodically run any due eval schedules; survives individual failures."""
    while True:
        await asyncio.sleep(max(1, tick_seconds))
        try:
            await run_due_schedules(db, hub, datetime.now(timezone.utc))
        except asyncio.CancelledError:
            raise
        except Exception:  # a bad run must never kill the loop
            _log.exception(
                "eval scheduler tick failed; inspect this traceback and correct the "
                "failing schedule or adapter before the next tick"
            )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        os.makedirs(settings.data_dir, exist_ok=True)
        dsn = settings.database.dsn()
        db = Database(dsn)
        # Bring the projection store to head. Migrations own the schema now.
        await apply_migrations(dsn)
        hub = EngineHub(settings)
        bus = EventBus()

        app.state.settings = settings
        app.state.db = db
        app.state.hub = hub
        app.state.bus = bus

        await bootstrap_tenant(settings, db)
        await sync_budgets(db, hub)
        async with db.sessionmaker() as session:
            await refresh_ingest_keys(session, hub)

        scheduler = asyncio.create_task(
            _eval_scheduler(db, hub, settings.eval_scheduler_seconds)
        )
        try:
            yield
        finally:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
            hub.close()
            await db.dispose()

    app = FastAPI(
        title="Agent Control Plane",
        version=__version__,
        summary="Self-hosted governance, observability, audit and cost control for automated agents.",
        lifespan=lifespan,
    )
    install_error_handlers(app)

    if settings.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.server.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/api/health", tags=["health"])
    @app.get("/healthz", tags=["health"])
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    api = "/api/v1"
    app.include_router(auth.router, prefix=api)
    app.include_router(projects.router, prefix=api)
    app.include_router(runs.router, prefix=api)
    app.include_router(budgets.router, prefix=api)
    app.include_router(audit.router, prefix=api)
    app.include_router(evals.router, prefix=api)
    app.include_router(siem.router, prefix=api)
    app.include_router(admin.router, prefix=api)
    app.include_router(overview.router, prefix=api)
    app.include_router(live.router, prefix=api)

    # Optionally serve a built dashboard from the same process (single-container
    # deploys). In dev the dashboard runs on its own port and proxies the API.
    dist = os.environ.get("ACP_FRONTEND_DIST") or str(
        Path(__file__).resolve().parents[2] / "frontend" / "dist"
    )
    if Path(dist).is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")

    return app
