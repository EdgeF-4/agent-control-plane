"""Run routes — the end-to-end ingest, enforcement, audit and replay surface."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models, schemas, services
from ..deps import (
    CurrentUser,
    get_bus,
    get_current_user,
    get_hub,
    get_ingest_principal,
    get_session,
)
from ..engines import EngineHub
from ..live import EventBus

router = APIRouter(prefix="/runs", tags=["runs"])


async def _get_run(session: AsyncSession, current: CurrentUser, run_id: uuid.UUID) -> models.Run:
    run = await session.get(models.Run, run_id)
    if run is None or run.tenant_id != current.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    # An API key may only touch runs in the one project it is scoped to.
    if current.scoped_project_id is not None and run.project_id != current.scoped_project_id:
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
    current: CurrentUser = Depends(get_ingest_principal),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
    bus: EventBus = Depends(get_bus),
) -> models.Run:
    try:
        return await services.create_run(session, hub, bus, current, payload)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))


@router.post("/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_run(
    payload: schemas.IngestRun,
    current: CurrentUser = Depends(get_ingest_principal),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
    bus: EventBus = Depends(get_bus),
) -> dict:
    try:
        run, outcomes = await services.ingest_run(session, hub, bus, current, payload)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
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
    current: CurrentUser = Depends(get_ingest_principal),
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
    current: CurrentUser = Depends(get_ingest_principal),
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
    current: CurrentUser = Depends(get_ingest_principal),
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


@router.get("/{run_id}/timeline")
async def run_timeline(
    run_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cumulative cost/tokens over a run's events, plus the burn rate.

    Powers the per-run cost timeline and burn-rate chart in the run drawer.
    """
    run = await _get_run(session, current, run_id)
    result = await session.execute(
        select(models.RunEvent)
        .where(models.RunEvent.run_id == run_id)
        .order_by(models.RunEvent.seq)
    )
    events = list(result.scalars().all())
    t0 = events[0].ts if events else run.started_at
    points: list[dict] = []
    cum_micro = 0
    cum_tokens = 0
    for e in events:
        cum_micro += e.cost_micro or 0
        cum_tokens += int((e.tokens or {}).get("total", 0) or 0)
        points.append({
            "seq": e.seq,
            "ts": e.ts.isoformat(),
            "event_type": e.event_type,
            "name": e.name,
            "cost_micro": e.cost_micro or 0,
            "cumulative_micro": cum_micro,
            "cumulative_tokens": cum_tokens,
            "elapsed_s": max(0.0, (e.ts - t0).total_seconds()),
        })
    duration_s = (events[-1].ts - t0).total_seconds() if len(events) > 1 else 0.0
    burn = (cum_micro / 1_000_000) / (duration_s / 60) if duration_s > 0 else 0.0
    return {
        "run_id": str(run.id),
        "points": points,
        "total_cost_micro": cum_micro,
        "total_tokens": cum_tokens,
        "duration_s": duration_s,
        "burn_rate_usd_per_min": burn,
    }


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
