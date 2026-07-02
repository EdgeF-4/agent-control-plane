"""Application factory and lifespan wiring."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .bootstrap import bootstrap_tenant, refresh_ingest_keys, sync_budgets
from .config import Settings, load_settings
from .db import Database
from .engines import EngineHub
from .live import EventBus
from .migrations import apply_migrations
from .routers import audit, auth, budgets, evals, live, overview, projects, runs, siem


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
        try:
            yield
        finally:
            hub.close()
            await db.dispose()

    app = FastAPI(
        title="Agent Control Plane",
        version=__version__,
        summary="Self-hosted governance, observability, audit and cost control for automated agents.",
        lifespan=lifespan,
    )

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
