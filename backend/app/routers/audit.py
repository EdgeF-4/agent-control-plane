"""Audit trail routes — the hash-chained record, projected for fast queries."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..deps import CurrentUser, get_current_user, get_session

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
async def audit_trail(
    limit: int = 100,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Most recent tamper-evident events across the tenant, newest first."""
    result = await session.execute(
        select(models.RunEvent, models.Run.external_run_id, models.Run.project_id)
        .join(models.Run, models.RunEvent.run_id == models.Run.id)
        .where(models.RunEvent.tenant_id == current.tenant_id)
        .order_by(models.RunEvent.ts.desc())
        .limit(min(limit, 500))
    )
    entries = []
    for event, external_run_id, project_id in result.all():
        entries.append(
            {
                "run_id": str(event.run_id),
                "external_run_id": external_run_id,
                "project_id": str(project_id),
                "seq": event.seq,
                "event_type": event.event_type,
                "name": event.name,
                "ts": event.ts.isoformat(),
                "cost_micro": event.cost_micro,
                "hash": event.hash,
                "prev_hash": event.prev_hash,
                "payload_summary": event.payload_summary,
            }
        )
    return entries
