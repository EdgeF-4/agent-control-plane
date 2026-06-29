"""Project routes — a project carries the budget that the cost engine enforces."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cost_governor import usd_to_micro

from .. import models, schemas
from ..deps import CurrentUser, get_current_user, get_hub, get_session, require_admin
from ..engines import EngineHub

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[schemas.ProjectOut])
async def list_projects(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[models.Project]:
    result = await session.execute(
        select(models.Project)
        .where(models.Project.tenant_id == current.tenant_id)
        .order_by(models.Project.created_at)
    )
    return list(result.scalars().all())


@router.post("", response_model=schemas.ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: schemas.ProjectCreate,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
) -> models.Project:
    exists = await session.execute(
        select(models.Project).where(
            models.Project.tenant_id == current.tenant_id,
            models.Project.slug == payload.slug,
        )
    )
    if exists.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "project slug already exists")

    project = models.Project(
        tenant_id=current.tenant_id,
        slug=payload.slug,
        name=payload.name,
        budget_limit_micro=usd_to_micro(payload.budget_usd),
        budget_period=payload.budget_period,
        budget_warn_threshold=payload.budget_warn_threshold,
        budget_latches_kill=payload.budget_latches_kill,
    )
    session.add(project)
    await session.flush()

    hub.ensure_budget(
        budget_id=str(project.id),
        name=f"{project.name} cap",
        scope_id=project.cost_scope_id,
        limit_micro=project.budget_limit_micro,
        period=project.budget_period,
        warn_threshold=project.budget_warn_threshold,
        latch=project.budget_latches_kill,
    )
    await session.commit()
    await session.refresh(project)
    return project
