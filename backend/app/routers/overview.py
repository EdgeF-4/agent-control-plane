"""A single aggregate call that powers the cockpit landing view."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cost_governor import micro_to_usd

from .. import models, schemas
from ..deps import CurrentUser, get_current_user, get_hub, get_session
from ..engines import EngineHub

router = APIRouter(prefix="/overview", tags=["overview"])


@router.get("")
async def overview(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    tid = current.tenant_id

    status_rows = await session.execute(
        select(models.Run.status, func.count())
        .where(models.Run.tenant_id == tid)
        .group_by(models.Run.status)
    )
    by_status = {s: c for s, c in status_rows.all()}

    recent_runs = await session.execute(
        select(models.Run)
        .where(models.Run.tenant_id == tid)
        .order_by(models.Run.started_at.desc())
        .limit(10)
    )
    runs = [schemas.RunOut.model_validate(r).model_dump(mode="json") for r in recent_runs.scalars().all()]

    decisions = await session.execute(
        select(models.PolicyDecision)
        .where(models.PolicyDecision.tenant_id == tid)
        .order_by(models.PolicyDecision.ts.desc())
        .limit(10)
    )
    recent_decisions = [
        {
            "tool": d.tool,
            "server": d.server,
            "decision": d.decision,
            "reason": d.reason,
            "ts": d.ts.isoformat(),
            "run_id": str(d.run_id) if d.run_id else None,
        }
        for d in decisions.scalars().all()
    ]

    spend_row = await session.execute(
        select(func.coalesce(func.sum(models.Run.total_cost_micro), 0)).where(
            models.Run.tenant_id == tid
        )
    )
    total_spend_micro = spend_row.scalar_one()

    event_count_row = await session.execute(
        select(func.count()).where(models.RunEvent.tenant_id == tid)
    )
    audit_event_count = event_count_row.scalar_one()

    latest_eval = await session.execute(
        select(models.EvalRun)
        .where(models.EvalRun.tenant_id == tid)
        .order_by(models.EvalRun.created_at.desc())
        .limit(1)
    )
    eval_row = latest_eval.scalar_one_or_none()
    eval_summary = (
        {
            "suite_name": eval_row.suite_name,
            "pass_rate": eval_row.pass_rate,
            "passed": eval_row.passed,
            "failed": eval_row.failed,
            "total": eval_row.total,
            "has_regressions": eval_row.has_regressions,
            "finished_at": eval_row.finished_at.isoformat() if eval_row.finished_at else None,
        }
        if eval_row
        else None
    )

    budgets = await asyncio.to_thread(hub.budget_status, f"{tid}/")

    denied = sum(1 for d in recent_decisions if d["decision"] == "deny")
    return {
        "tenant": {"id": str(tid), "slug": current.tenant.slug, "name": current.tenant.name},
        "runs": {
            "total": sum(by_status.values()),
            "running": by_status.get("running", 0),
            "completed": by_status.get("completed", 0),
            "killed": by_status.get("killed", 0),
            "error": by_status.get("error", 0),
        },
        "total_spend_usd": micro_to_usd(total_spend_micro),
        "audit_event_count": audit_event_count,
        "recent_denied_decisions": denied,
        "budgets": budgets,
        "recent_runs": runs,
        "recent_decisions": recent_decisions,
        "eval": eval_summary,
    }
