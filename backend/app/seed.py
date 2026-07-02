"""Seed a demonstrable dataset: projects, runs, a budget kill, and evals.

Run once against an empty store. It drives the same service layer the API uses,
so every row it creates is produced by the real engines.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from cost_governor import usd_to_micro

from . import models, schemas, services
from .bootstrap import bootstrap_tenant
from .config import Settings, load_settings
from .db import Database
from .deps import CurrentUser
from .engines import EngineHub
from .live import EventBus
from .migrations import apply_migrations


async def _ensure_project(session, hub, current, *, slug, name, budget_usd, warn=0.8) -> models.Project:
    existing = await session.execute(
        select(models.Project).where(
            models.Project.tenant_id == current.tenant_id, models.Project.slug == slug
        )
    )
    project = existing.scalar_one_or_none()
    if project is not None:
        return project
    project = models.Project(
        tenant_id=current.tenant_id, slug=slug, name=name,
        budget_limit_micro=usd_to_micro(budget_usd), budget_period="monthly",
        budget_warn_threshold=warn, budget_latches_kill=True,
    )
    session.add(project)
    await session.flush()
    hub.ensure_budget(
        budget_id=str(project.id), name=f"{project.name} cap",
        scope_id=project.cost_scope_id, limit_micro=project.budget_limit_micro,
        period="monthly", warn_threshold=warn, latch=True,
    )
    await session.commit()
    await session.refresh(project)
    return project


async def seed(settings: Settings) -> None:
    dsn = settings.database.dsn()
    db = Database(dsn)
    await apply_migrations(dsn)
    hub = EngineHub(settings)
    bus = EventBus()
    await bootstrap_tenant(settings, db)
    try:
        async with db.sessionmaker() as session:
            tenant = (
                await session.execute(
                    select(models.Tenant).where(
                        models.Tenant.slug == settings.bootstrap.tenant_slug
                    )
                )
            ).scalar_one()
            admin = (
                await session.execute(
                    select(models.User).where(
                        models.User.tenant_id == tenant.id, models.User.role == "admin"
                    )
                )
            ).scalars().first()
            current = CurrentUser(user=admin, tenant=tenant)

            research = await _ensure_project(
                session, hub, current, slug="research", name="Research Assistant", budget_usd=50.0
            )
            alpha = await _ensure_project(
                session, hub, current, slug="alpha", name="Alpha Pilot", budget_usd=5.0
            )

            # 1) A healthy run with an allowed and a denied tool call.
            await services.ingest_run(session, hub, bus, current, schemas.IngestRun(
                project_slug=research.slug, agent_name="researcher", label="market scan",
                input={"task": "summarize three sources"},
                usage=[
                    schemas.UsageReport(provider="local", model="assistant-large",
                                        input_tokens=2400, output_tokens=900, cost_usd=0.42),
                    schemas.UsageReport(provider="local", model="assistant-large",
                                        input_tokens=1800, output_tokens=600, cost_usd=0.31),
                ],
                tool_calls=[
                    schemas.ToolCallReport(server="web", tool="search",
                                           arguments={"q": "market size 2026"}),
                    schemas.ToolCallReport(server="db", tool="delete_records",
                                           arguments={"table": "leads"}),
                ],
                status="completed", output={"summary": "three sources reconciled"},
            ))

            # 2) A second healthy run.
            await services.ingest_run(session, hub, bus, current, schemas.IngestRun(
                project_slug=research.slug, agent_name="researcher", label="follow-up",
                usage=[schemas.UsageReport(provider="local", model="assistant-small",
                                           input_tokens=900, output_tokens=300, cost_usd=0.08)],
                tool_calls=[schemas.ToolCallReport(server="web", tool="fetch",
                                                   arguments={"url": "https://example.test"})],
                status="completed",
            ))

            # 3) A run that blows the $5 cap on Alpha — the kill switch latches.
            await services.ingest_run(session, hub, bus, current, schemas.IngestRun(
                project_slug=alpha.slug, agent_name="pilot", label="runaway loop",
                usage=[
                    schemas.UsageReport(provider="local", model="assistant-large",
                                        input_tokens=9000, output_tokens=4000, cost_usd=3.10),
                    schemas.UsageReport(provider="local", model="assistant-large",
                                        input_tokens=9000, output_tokens=4000, cost_usd=3.40),
                ],
                status="completed",
            ))

            # 4) Reliability: run the bundled suite twice and pin a baseline.
            r1, _ = await asyncio.to_thread(hub.run_eval, SUITE, FIXTURES, "baseline")
            await asyncio.to_thread(hub.set_eval_baseline, r1.suite_name, r1.run_id)
            r2, comparison = await asyncio.to_thread(hub.run_eval, SUITE, FIXTURES, "nightly")
            for label, res, comp, base in (("baseline", r1, None, True), ("nightly", r2, comparison, False)):
                session.add(models.EvalRun(
                    tenant_id=tenant.id, eval_run_id=res.run_id, suite_name=res.suite_name,
                    suite_hash=res.suite_hash, pass_rate=res.pass_rate, mean_score=res.mean_score,
                    total_cost_usd=res.total_cost_usd,
                    passed=sum(1 for c in res.cases if c.passed),
                    failed=sum(1 for c in res.cases if not c.passed),
                    total=len(res.cases), is_baseline=base,
                    has_regressions=(comp.has_regressions if comp else None),
                    data={"comparison": comp.to_dict() if comp else None},
                ))
            # 5) A daily schedule for the suite so the cadence is visible.
            session.add(models.EvalSchedule(
                tenant_id=tenant.id, suite_name=r1.suite_name, interval_minutes=1440,
                enabled=True,
            ))
            await session.commit()
        print("seed complete")
    finally:
        hub.close()
        await db.dispose()


# Reuse the shared suite/fixtures so seeded and live evals match.
from .eval_service import FIXTURES, SUITE  # noqa: E402


def main() -> None:
    asyncio.run(seed(load_settings()))


if __name__ == "__main__":
    main()
