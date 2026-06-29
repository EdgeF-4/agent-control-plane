"""First-run bootstrap and engine re-sync.

On startup we make sure the configured admin exists and that every project's
budget is registered with the cost engine. ``set_budget`` is an upsert, so this
is safe to run on every boot — it reconciles the cost engine with the store.
"""

from __future__ import annotations

from sqlalchemy import select

from . import models
from .config import Settings
from .db import Database
from .engines import EngineHub
from .security import hash_password


async def bootstrap_tenant(settings: Settings, db: Database) -> None:
    if settings.bootstrap is None:
        return
    bs = settings.bootstrap
    async with db.sessionmaker() as session:
        existing = await session.execute(
            select(models.Tenant).where(models.Tenant.slug == bs.tenant_slug)
        )
        if existing.scalar_one_or_none() is not None:
            return
        tenant = models.Tenant(slug=bs.tenant_slug, name=bs.tenant_name)
        session.add(tenant)
        await session.flush()
        session.add(
            models.User(
                tenant_id=tenant.id,
                email=bs.admin_email,
                password_hash=hash_password(bs.admin_password),
                name="Administrator",
                role="admin",
            )
        )
        await session.commit()


async def sync_budgets(db: Database, hub: EngineHub) -> None:
    async with db.sessionmaker() as session:
        result = await session.execute(select(models.Project))
        for project in result.scalars().all():
            hub.ensure_budget(
                budget_id=str(project.id),
                name=f"{project.name} cap",
                scope_id=project.cost_scope_id,
                limit_micro=project.budget_limit_micro,
                period=project.budget_period,
                warn_threshold=project.budget_warn_threshold,
                latch=project.budget_latches_kill,
            )
