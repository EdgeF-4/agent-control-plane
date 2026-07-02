"""Eval routes — reliability suites, scheduling, regression diffs, and the gate.

The bundled suite runs against an offline adapter, so reliability checks work in
an air-gapped install with no outbound calls. A schedule re-runs it on a cadence;
a project's eval gate blocks new runs while its suite is regressed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models, schemas
from ..deps import CurrentUser, get_current_user, get_hub, get_session, require_admin
from ..engines import EngineHub
from ..eval_service import run_and_record
from ..evals.suites import default_suite_name

router = APIRouter(prefix="/evals", tags=["evals"])


@router.get("/suites")
async def list_suites(
    current: CurrentUser = Depends(get_current_user),
    hub: EngineHub = Depends(get_hub),
) -> list[dict]:
    """Available reliability suites: name, adapter type, case count, runnable."""
    return hub.eval_suites_meta()


@router.get("")
async def list_evals(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    result = await session.execute(
        select(models.EvalRun)
        .where(models.EvalRun.tenant_id == current.tenant_id)
        .order_by(models.EvalRun.created_at.desc())
        .limit(50)
    )
    return [
        {
            "id": str(r.id),
            "eval_run_id": r.eval_run_id,
            "suite_name": r.suite_name,
            "pass_rate": r.pass_rate,
            "mean_score": r.mean_score,
            "passed": r.passed,
            "failed": r.failed,
            "total": r.total,
            "is_baseline": r.is_baseline,
            "has_regressions": r.has_regressions,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in result.scalars().all()
    ]


@router.post("/run", status_code=status.HTTP_201_CREATED)
async def run_eval(
    suite: str | None = None,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    if suite and suite not in hub.eval_suite_names():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown suite '{suite}'")
    row, _comparison = await run_and_record(
        session, hub, tenant_id=current.tenant_id, suite_name=suite,
        label=f"manual:{suite or default_suite_name()}",
    )
    return {
        "id": str(row.id),
        "eval_run_id": row.eval_run_id,
        "suite_name": row.suite_name,
        "pass_rate": row.pass_rate,
        "passed": row.passed,
        "failed": row.failed,
        "total": row.total,
        "has_regressions": row.has_regressions,
    }


@router.get("/{eval_id}/diff")
async def eval_diff(
    eval_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The stored regression diff of this run against the baseline it ran against."""
    row = await session.get(models.EvalRun, eval_id)
    if row is None or row.tenant_id != current.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "eval run not found")
    comparison = (row.data or {}).get("comparison")
    return {
        "eval_run_id": row.eval_run_id,
        "suite_name": row.suite_name,
        "has_regressions": row.has_regressions,
        "comparison": comparison,
    }


@router.post("/{eval_id}/baseline")
async def set_baseline(
    eval_id: uuid.UUID,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    row = await session.get(models.EvalRun, eval_id)
    if row is None or row.tenant_id != current.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "eval run not found")
    import asyncio

    await asyncio.to_thread(hub.set_eval_baseline, row.suite_name, row.eval_run_id)
    others = await session.execute(
        select(models.EvalRun).where(
            models.EvalRun.tenant_id == current.tenant_id,
            models.EvalRun.suite_name == row.suite_name,
        )
    )
    for other in others.scalars().all():
        other.is_baseline = other.id == row.id
    await session.commit()
    return {"baseline": row.eval_run_id, "suite": row.suite_name}


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #
@router.get("/schedules", response_model=list[schemas.EvalScheduleOut])
async def list_schedules(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[models.EvalSchedule]:
    result = await session.execute(
        select(models.EvalSchedule)
        .where(models.EvalSchedule.tenant_id == current.tenant_id)
        .order_by(models.EvalSchedule.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/schedules", response_model=schemas.EvalScheduleOut,
             status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: schemas.EvalScheduleCreate,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
) -> models.EvalSchedule:
    project_id = None
    if payload.project_slug:
        result = await session.execute(
            select(models.Project).where(
                models.Project.tenant_id == current.tenant_id,
                models.Project.slug == payload.project_slug,
            )
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
        project_id = project.id
    suite_name = payload.suite_name or default_suite_name()
    if suite_name not in hub.eval_suite_names():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown suite '{suite_name}'")
    schedule = models.EvalSchedule(
        tenant_id=current.tenant_id,
        project_id=project_id,
        suite_name=suite_name,
        interval_minutes=max(1, payload.interval_minutes),
        enabled=payload.enabled,
        next_run_at=datetime.now(timezone.utc),
    )
    session.add(schedule)
    await session.commit()
    await session.refresh(schedule)
    return schedule


@router.post("/schedules/{schedule_id}/run-now", status_code=status.HTTP_201_CREATED)
async def run_schedule_now(
    schedule_id: uuid.UUID,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    schedule = await session.get(models.EvalSchedule, schedule_id)
    if schedule is None or schedule.tenant_id != current.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "schedule not found")
    row, _ = await run_and_record(
        session, hub, tenant_id=schedule.tenant_id, project_id=schedule.project_id,
        suite_name=schedule.suite_name or None,
        label=f"manual:{schedule.suite_name}",
    )
    schedule.last_run_at = datetime.now(timezone.utc)
    from datetime import timedelta

    schedule.next_run_at = schedule.last_run_at + timedelta(minutes=schedule.interval_minutes)
    await session.commit()
    return {"eval_id": str(row.id), "has_regressions": row.has_regressions}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: uuid.UUID,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    schedule = await session.get(models.EvalSchedule, schedule_id)
    if schedule is None or schedule.tenant_id != current.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "schedule not found")
    await session.delete(schedule)
    await session.commit()
    return {"deleted": True, "id": str(schedule_id)}


# --------------------------------------------------------------------------- #
# Eval gate
# --------------------------------------------------------------------------- #
@router.put("/gate/{project_slug}")
async def set_gate(
    project_slug: str,
    payload: schemas.EvalGateUpdate,
    current: CurrentUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
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
    project.eval_gate_suite = payload.suite_name or ""
    await session.commit()
    return {"project": project_slug, "eval_gate_suite": project.eval_gate_suite}
