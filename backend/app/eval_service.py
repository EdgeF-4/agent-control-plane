"""Reliability evals: run the suite, record the result + regression diff, drive
schedules, and enforce the promotion gate.

Shared by the eval routes, the background scheduler, and the seeder so a run
recorded any of those ways is identical. The bundled suite runs against an
offline adapter, so reliability checks work in an air-gapped install.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models
from .evals.suites import default_suite_name


class EvalGateError(Exception):
    """Raised when an eval gate blocks promotion (a gated suite is regressed)."""


def _parse_iso(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


async def run_and_record(
    session: AsyncSession,
    hub,
    *,
    tenant_id,
    project_id=None,
    suite_name: str | None = None,
    label: str = "",
) -> tuple[models.EvalRun, object]:
    """Run a named suite through the eval engine and persist the result + diff."""
    suite = suite_name or default_suite_name()
    result, comparison = await asyncio.to_thread(hub.run_eval, suite, label)
    row = models.EvalRun(
        tenant_id=tenant_id,
        project_id=project_id,
        eval_run_id=result.run_id,
        suite_name=result.suite_name,
        suite_hash=result.suite_hash,
        pass_rate=result.pass_rate,
        mean_score=result.mean_score,
        total_cost_usd=result.total_cost_usd,
        passed=sum(1 for c in result.cases if c.passed),
        failed=sum(1 for c in result.cases if not c.passed),
        total=len(result.cases),
        has_regressions=(comparison.has_regressions if comparison else None),
        started_at=_parse_iso(result.started_at),
        finished_at=_parse_iso(result.finished_at),
        data={"comparison": comparison.to_dict() if comparison else None},
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row, comparison


async def run_due_schedules(db, hub, now: datetime | None = None) -> int:
    """Run every enabled schedule whose next_run_at has passed. Returns the count."""
    now = now or datetime.now(timezone.utc)
    ran = 0
    async with db.sessionmaker() as session:
        due = await session.execute(
            select(models.EvalSchedule).where(
                models.EvalSchedule.enabled.is_(True),
                models.EvalSchedule.next_run_at <= now,
            )
        )
        known = set(hub.eval_suite_names())
        for sch in due.scalars().all():
            # Advance the schedule regardless so a suite that was removed from
            # config doesn't wedge the loop re-selecting the same due row.
            sch.last_run_at = now
            sch.next_run_at = now + timedelta(minutes=max(1, sch.interval_minutes))
            if sch.suite_name and sch.suite_name not in known:
                continue
            await run_and_record(
                session, hub, tenant_id=sch.tenant_id, project_id=sch.project_id,
                suite_name=sch.suite_name or None,
                label=f"scheduled:{sch.suite_name}",
            )
            ran += 1
        await session.commit()
    return ran


async def assert_promotion_allowed(session: AsyncSession, tenant_id, project: models.Project) -> None:
    """Enforce a project's eval gate: block a new run when the gated suite is regressed."""
    suite = project.eval_gate_suite
    if not suite:
        return
    latest = await session.execute(
        select(models.EvalRun)
        .where(models.EvalRun.tenant_id == tenant_id, models.EvalRun.suite_name == suite)
        .order_by(models.EvalRun.created_at.desc())
        .limit(1)
    )
    row = latest.scalar_one_or_none()
    if row is not None and row.has_regressions:
        raise EvalGateError(
            f"promotion blocked by eval gate: suite '{suite}' has a regression "
            f"(latest run {row.passed}/{row.total} passing)"
        )
