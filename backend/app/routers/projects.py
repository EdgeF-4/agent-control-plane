"""Project routes — a project carries the budget that the cost engine enforces."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cost_governor import usd_to_micro

from .. import models, schemas
from ..bootstrap import refresh_ingest_keys
from ..deps import CurrentUser, get_current_user, get_hub, get_session, require_admin
from ..engines import EngineHub
from ..security import generate_api_key

router = APIRouter(prefix="/projects", tags=["projects"])


async def _project_or_404(session: AsyncSession, current: CurrentUser, slug: str) -> models.Project:
    result = await session.execute(
        select(models.Project).where(
            models.Project.tenant_id == current.tenant_id,
            models.Project.slug == slug,
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


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


# --------------------------------------------------------------------------- #
# Per-project ingest API keys
# --------------------------------------------------------------------------- #
@router.get("/{slug}/keys", response_model=list[schemas.ApiKeyOut])
async def list_api_keys(
    slug: str,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[models.ApiKey]:
    project = await _project_or_404(session, current, slug)
    result = await session.execute(
        select(models.ApiKey)
        .where(models.ApiKey.project_id == project.id)
        .order_by(models.ApiKey.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/{slug}/keys", response_model=schemas.ApiKeyCreated,
             status_code=status.HTTP_201_CREATED)
async def create_api_key(
    slug: str,
    payload: schemas.ApiKeyCreate,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
) -> schemas.ApiKeyCreated:
    project = await _project_or_404(session, current, slug)
    plaintext, prefix, digest = generate_api_key()
    key = models.ApiKey(
        tenant_id=current.tenant_id, project_id=project.id,
        name=payload.name, key_prefix=prefix, key_sha256=digest,
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    await refresh_ingest_keys(session, hub)
    base = schemas.ApiKeyOut.model_validate(key)
    return schemas.ApiKeyCreated(**base.model_dump(), key=plaintext)


@router.delete("/{slug}/keys/{key_id}")
async def revoke_api_key(
    slug: str,
    key_id: uuid.UUID,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    project = await _project_or_404(session, current, slug)
    key = await session.get(models.ApiKey, key_id)
    if key is None or key.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "api key not found")
    if key.revoked_at is None:
        key.revoked_at = models.utcnow()
        await session.commit()
        await refresh_ingest_keys(session, hub)
    return {"revoked": True, "id": str(key_id)}
