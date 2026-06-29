"""Run routes — the end-to-end ingest, enforcement, audit and replay surface."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models, schemas, services
from ..deps import CurrentUser, get_bus, get_current_user, get_hub, get_session
from ..engines import EngineHub
from ..live import EventBus

router = APIRouter(prefix="/runs", tags=["runs"])


async def _get_run(session: AsyncSession, current: CurrentUser, run_id: uuid.UUID) -> models.Run:
    run = await session.get(models.Run, run_id)
    if run is None or run.tenant_id != current.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


@router.get("", response_model=list[schemas.RunOut])
async def list_runs(
    project_slug: str | None = None,
    limit: int = 100,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[models.Run]:
    query = select(models.Run).where(models.Run.tenant_id == current.tenant_id)
    if project_slug:
        sub = select(models.Project.id).where(
            models.Project.tenant_id == current.tenant_id,
            models.Project.slug == project_slug,
        )
        query = query.where(models.Run.project_id.in_(sub))
    query = query.order_by(models.Run.started_at.desc()).limit(min(limit, 500))
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("", response_model=schemas.RunOut, status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: schemas.RunCreate,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
    bus: EventBus = Depends(get_bus),
) -> models.Run:
    try:
        return await services.create_run(session, hub, bus, current, payload)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")


@router.post("/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_run(
    payload: schemas.IngestRun,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
    bus: EventBus = Depends(get_bus),
) -> dict:
    try:
        run, outcomes = await services.ingest_run(session, hub, bus, current, payload)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return {
        "run": schemas.RunOut.model_validate(run).model_dump(mode="json"),
        "outcomes": [o.model_dump() for o in outcomes],
    }


@router.get("/{run_id}", response_model=schemas.RunOut)
async def get_run(
    run_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> models.Run:
    return await _get_run(session, current, run_id)


@router.post("/{run_id}/usage", response_model=schemas.UsageOutcome)
async def report_usage(
    run_id: uuid.UUID,
    payload: schemas.UsageReport,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
    bus: EventBus = Depends(get_bus),
) -> schemas.UsageOutcome:
    run = await _get_run(session, current, run_id)
    if run.status != "running":
        raise HTTPException(status.HTTP_409_CONFLICT, f"run is {run.status}")
    return await services.report_usage(session, hub, bus, current, run, payload)


@router.post("/{run_id}/tool-call", response_model=schemas.DecisionOut)
async def report_tool_call(
    run_id: uuid.UUID,
    payload: schemas.ToolCallReport,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
    bus: EventBus = Depends(get_bus),
) -> schemas.DecisionOut:
    run = await _get_run(session, current, run_id)
    return await services.report_tool_call(session, hub, bus, current, run, payload)


@router.post("/{run_id}/complete", response_model=schemas.RunOut)
async def complete_run(
    run_id: uuid.UUID,
    payload: schemas.RunComplete,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
    bus: EventBus = Depends(get_bus),
) -> models.Run:
    run = await _get_run(session, current, run_id)
    return await services.complete_run(session, hub, bus, current, run, payload)


@router.get("/{run_id}/events", response_model=list[schemas.EventOut])
async def run_events(
    run_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[models.RunEvent]:
    await _get_run(session, current, run_id)
    result = await session.execute(
        select(models.RunEvent)
        .where(models.RunEvent.run_id == run_id)
        .order_by(models.RunEvent.seq)
    )
    return list(result.scalars().all())


@router.get("/{run_id}/decisions", response_model=list[schemas.DecisionOut])
async def run_decisions(
    run_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[models.PolicyDecision]:
    await _get_run(session, current, run_id)
    result = await session.execute(
        select(models.PolicyDecision)
        .where(models.PolicyDecision.run_id == run_id)
        .order_by(models.PolicyDecision.ts)
    )
    return list(result.scalars().all())


@router.get("/{run_id}/verify", response_model=schemas.VerifyResult)
async def verify_run(
    run_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
) -> schemas.VerifyResult:
    run = await _get_run(session, current, run_id)
    import asyncio

    result = await asyncio.to_thread(hub.verify_run, run.external_run_id)
    return schemas.VerifyResult(**result)
