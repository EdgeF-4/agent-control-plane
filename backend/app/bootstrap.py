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
                superadmin=True,
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


async def refresh_ingest_keys(session, hub: EngineHub) -> None:
    """Rebuild the hub's ingest authenticator from the active API keys.

    Called on startup and after any key is created or revoked, so the
    gateway-composed authenticator always reflects the current set.
    """
    result = await session.execute(
        select(models.ApiKey).where(models.ApiKey.revoked_at.is_(None))
    )
    keys = [
        {"client_id": str(k.id), "key_sha256": k.key_sha256}
        for k in result.scalars().all()
    ]
    hub.set_ingest_keys(keys)
