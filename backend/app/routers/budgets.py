"""Budget routes — live cost-vs-budget straight from the cost engine."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..deps import CurrentUser, get_current_user, get_hub, get_session, require_admin
from ..engines import EngineHub

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("")
async def budget_status(
    current: CurrentUser = Depends(get_current_user),
    hub: EngineHub = Depends(get_hub),
) -> list[dict]:
    prefix = f"{current.tenant_id}/"
    return await asyncio.to_thread(hub.budget_status, prefix)


@router.post("/{project_slug}/release")
async def release_kill_switch(
    project_slug: str,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    result = await session.execute(
        select(models.Project).where(
            models.Project.tenant_id == current.tenant_id,
            models.Project.slug == project_slug,
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    await asyncio.to_thread(hub.release, project.cost_scope_id)
    return {"released": True, "project": project_slug}
