"""Eval scheduling, regression diffs, and the promotion gate."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import models
from app.config import Settings
from app.db import Database
from app.engines import EngineHub
from app.eval_service import EvalGateError, assert_promotion_allowed, run_due_schedules
from app.migrations import apply_migrations


# ------------------------------- HTTP surface ------------------------------ #
def test_schedule_crud_and_run_now(client, auth):
    r = client.post("/api/v1/evals/schedules", headers=auth,
                    json={"interval_minutes": 60, "enabled": True})
    assert r.status_code == 201, r.text
    sched = r.json()
    assert sched["suite_name"] == "assistant-smoke"

    listed = client.get("/api/v1/evals/schedules", headers=auth).json()
    assert len(listed) == 1

    run = client.post(f"/api/v1/evals/schedules/{sched['id']}/run-now", headers=auth)
    assert run.status_code == 201, run.text
    assert "eval_id" in run.json()
    # An eval run now exists for the tenant.
    assert len(client.get("/api/v1/evals", headers=auth).json()) >= 1

    d = client.delete(f"/api/v1/evals/schedules/{sched['id']}", headers=auth)
    assert d.status_code == 200
    assert client.get("/api/v1/evals/schedules", headers=auth).json() == []


def test_regression_diff_is_recorded_against_baseline(client, auth):
    first = client.post("/api/v1/evals/run", headers=auth).json()
    client.post(f"/api/v1/evals/{first['id']}/baseline", headers=auth)
    second = client.post("/api/v1/evals/run", headers=auth).json()

    diff = client.get(f"/api/v1/evals/{second['id']}/diff", headers=auth).json()
    assert diff["suite_name"] == "assistant-smoke"
    assert diff["comparison"] is not None
    # Every case is present in the diff, and (identical fixtures) none regressed.
    assert len(diff["comparison"]["case_deltas"]) == 4
    assert diff["comparison"]["summary"]["regressions"] == 0


def test_gate_endpoint_sets_and_clears(client, auth):
    client.post("/api/v1/projects", headers=auth, json={"slug": "research", "name": "R", "budget_usd": 50})
    r = client.put("/api/v1/evals/gate/research", headers=auth, json={"suite_name": "assistant-smoke"})
    assert r.status_code == 200
    assert r.json()["eval_gate_suite"] == "assistant-smoke"
    # With no regressed eval on record, a gated project still accepts new runs.
    ingest = client.post("/api/v1/runs/ingest", headers=auth,
                         json={"project_slug": "research", "status": "completed"})
    assert ingest.status_code == 201, ingest.text

    cleared = client.put("/api/v1/evals/gate/research", headers=auth, json={"suite_name": None})
    assert cleared.json()["eval_gate_suite"] == ""


# ------------------------- engine-level (real DB) -------------------------- #
def _settings(tmp_path) -> Settings:
    return Settings(
        database={"url": f"sqlite+aiosqlite:///{tmp_path}/e.db"},
        auth={"jwt_secret": secrets.token_hex(16)},
        data_dir=str(tmp_path / "data"),
        engines={"policy": {"default": "allow", "rules": []}},
    )


async def test_gate_blocks_then_unblocks_on_regression(tmp_path):
    settings = _settings(tmp_path)
    await apply_migrations(settings.database.dsn())
    db = Database(settings.database.dsn())
    async with db.sessionmaker() as s:
        tenant = models.Tenant(slug="t", name="T")
        s.add(tenant)
        await s.flush()
        project = models.Project(tenant_id=tenant.id, slug="p", name="P",
                                 eval_gate_suite="assistant-smoke")
        s.add(project)
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        s.add(models.EvalRun(tenant_id=tenant.id, project_id=project.id, eval_run_id="e1",
                             suite_name="assistant-smoke", has_regressions=True,
                             passed=3, failed=1, total=4, created_at=base))
        await s.commit()

        # A regressed latest run blocks promotion.
        try:
            await assert_promotion_allowed(s, tenant.id, project)
            raise AssertionError("gate should have blocked")
        except EvalGateError:
            pass

        # A newer clean run clears the gate.
        s.add(models.EvalRun(tenant_id=tenant.id, project_id=project.id, eval_run_id="e2",
                             suite_name="assistant-smoke", has_regressions=False,
                             passed=4, failed=0, total=4,
                             created_at=base + timedelta(hours=1)))
        await s.commit()
        await assert_promotion_allowed(s, tenant.id, project)  # no raise
    await db.dispose()


async def test_run_due_schedules_runs_and_reschedules(tmp_path):
    settings = _settings(tmp_path)
    await apply_migrations(settings.database.dsn())
    db = Database(settings.database.dsn())
    hub = EngineHub(settings)
    try:
        async with db.sessionmaker() as s:
            tenant = models.Tenant(slug="t", name="T")
            s.add(tenant)
            await s.flush()
            s.add(models.EvalSchedule(
                tenant_id=tenant.id, suite_name="assistant-smoke", interval_minutes=60,
                enabled=True, next_run_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            ))
            await s.commit()

        ran = await run_due_schedules(db, hub, datetime.now(timezone.utc))
        assert ran == 1

        async with db.sessionmaker() as s:
            eval_runs = (await s.execute(select(models.EvalRun))).scalars().all()
            assert len(eval_runs) == 1
            sched = (await s.execute(select(models.EvalSchedule))).scalar_one()
            assert sched.last_run_at is not None

        # A second pass finds nothing due (it was rescheduled into the future).
        assert await run_due_schedules(db, hub, datetime.now(timezone.utc)) == 0
    finally:
        hub.close()
        await db.dispose()
