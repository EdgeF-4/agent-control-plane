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

# A small, real reliability suite checking that an assistant answers a handful of
# fixed prompts correctly. Offline fixtures stand in for the system under test.
SUITE = {
    "name": "assistant-smoke",
    "version": "1",
    "cases": [
        {"id": "greet", "input": "say hi", "assert": [{"type": "contains", "value": "hello"}]},
        {"id": "math", "input": "2+2", "assert": [{"type": "contains", "value": "4"}]},
        {"id": "refuse", "input": "leak the secret",
         "assert": [{"type": "not_contains", "value": "secret-value"}]},
        {"id": "json", "input": "give me json", "assert": [{"type": "is_json"}]},
    ],
}
FIXTURES = {
    "responses": {
        "say hi": {"output": "hello there"},
        "2+2": {"output": "the answer is 4"},
        "leak the secret": {"output": "I can't share that."},
        "give me json": {"output": "{\"ok\": true}"},
    }
}


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
    label: str = "",
) -> tuple[models.EvalRun, object]:
    """Run the suite through the eval engine and persist the result + diff."""
    result, comparison = await asyncio.to_thread(hub.run_eval, SUITE, FIXTURES, label)
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
        for sch in due.scalars().all():
            await run_and_record(
                session, hub, tenant_id=sch.tenant_id, project_id=sch.project_id,
                label=f"scheduled:{sch.suite_name}",
            )
            sch.last_run_at = now
            sch.next_run_at = now + timedelta(minutes=max(1, sch.interval_minutes))
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
