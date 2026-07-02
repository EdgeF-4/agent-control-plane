"""Service layer: orchestrate engines, project to the store, broadcast live.

Each operation follows the same shape:
  1. call the relevant engine(s) off the event loop (``asyncio.to_thread``);
  2. project the authoritative result into the Postgres view;
  3. publish a live frame to the tenant's subscribers.

The engines remain the source of truth; the projection is what the dashboard
reads, and it can always be re-verified against the recorder's hash chain.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from cost_governor import micro_to_usd, usd_to_micro
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models, schemas
from .deps import CurrentUser
from .engines import EngineHub
from .live import EventBus


def _utc(epoch: float | None) -> datetime:
    if epoch is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _truncate(value, limit: int = 500):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {k: _truncate(v, limit) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return [_truncate(v, limit) for v in value[:50]]
    return value


def _project_event(run: models.Run, sealed: dict) -> models.RunEvent:
    """Turn one sealed recorder event into a queryable audit-trail row."""
    cost_usd = sealed.get("cost_usd")
    return models.RunEvent(
        tenant_id=run.tenant_id,
        run_id=run.id,
        seq=int(sealed.get("seq", 0)),
        event_type=str(sealed.get("type", "log")),
        name=sealed.get("name"),
        ts=_utc(sealed.get("ts")),
        cost_micro=usd_to_micro(cost_usd) if cost_usd else None,
        tokens=sealed.get("tokens"),
        latency_ms=sealed.get("latency_ms"),
        payload_summary=_truncate(sealed.get("payload") or {}),
        hash=sealed.get("hash", ""),
        prev_hash=sealed.get("prev_hash", ""),
    )


async def _resolve_project(
    session: AsyncSession, tenant_id: uuid.UUID, slug: str
) -> models.Project:
    result = await session.execute(
        select(models.Project).where(
            models.Project.tenant_id == tenant_id, models.Project.slug == slug
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise KeyError(slug)
    return project


async def _resolve_project_for_principal(
    session: AsyncSession, current: CurrentUser, slug: str
) -> models.Project:
    """Resolve the target project, enforcing an API key's project scope.

    An API-key principal may only act on the one project it belongs to: the slug
    is optional (the key implies it), and a mismatching slug is refused.
    """
    if current.scoped_project_id is not None:
        project = await session.get(models.Project, current.scoped_project_id)
        if project is None or project.tenant_id != current.tenant_id:
            raise KeyError(slug or "scoped project")
        if slug and project.slug != slug:
            raise PermissionError("this api key is scoped to a different project")
        return project
    return await _resolve_project(session, current.tenant_id, slug)


# --------------------------------------------------------------------------- #
# Run lifecycle
# --------------------------------------------------------------------------- #
async def create_run(
    session: AsyncSession,
    hub: EngineHub,
    bus: EventBus,
    current: CurrentUser,
    payload: schemas.RunCreate,
) -> models.Run:
    project = await _resolve_project_for_principal(session, current, payload.project_slug)
    external_run_id = f"{current.tenant.slug}__{project.slug}__{uuid.uuid4().hex[:12]}"

    sealed = await asyncio.to_thread(
        hub.open_run,
        external_run_id,
        input=payload.input,
        meta={"tenant": str(current.tenant_id), "project": project.slug, **payload.meta},
    )

    run = models.Run(
        tenant_id=current.tenant_id,
        project_id=project.id,
        external_run_id=external_run_id,
        agent_name=payload.agent_name,
        status="running",
        label=payload.label,
        meta=payload.meta,
    )
    session.add(run)
    await session.flush()
    if sealed:
        session.add(_project_event(run, sealed))
    await session.commit()
    await session.refresh(run)

    await _audit(hub, current, project, run, event_type="server_lifecycle",
                 decision="allow", name="run_start", reason="run opened")
    await _broadcast_run(bus, current, run, "run.created")
    return run


async def report_usage(
    session: AsyncSession,
    hub: EngineHub,
    bus: EventBus,
    current: CurrentUser,
    run: models.Run,
    payload: schemas.UsageReport,
) -> schemas.UsageOutcome:
    project = await session.get(models.Project, run.project_id)
    scope_id = project.cost_scope_id
    cost_micro = usd_to_micro(payload.cost_usd) if payload.cost_usd is not None else None

    outcome = await asyncio.to_thread(
        hub.record_and_check,
        client=str(current.tenant_id),
        scope_id=scope_id,
        agent=run.external_run_id,
        provider=payload.provider,
        model=payload.model,
        input_tokens=payload.input_tokens,
        output_tokens=payload.output_tokens,
        cost_micro=cost_micro,
    )

    sealed = await asyncio.to_thread(
        hub.emit,
        run.external_run_id,
        "model_response",
        {"provider": payload.provider, "model": payload.model},
        name=payload.name or payload.model,
        tokens={"prompt": payload.input_tokens, "completion": payload.output_tokens,
                "total": payload.input_tokens + payload.output_tokens},
        cost_usd=micro_to_usd(outcome.cost_micro),
        latency_ms=payload.latency_ms,
    )
    if sealed:
        session.add(_project_event(run, sealed))

    run.total_input_tokens += payload.input_tokens
    run.total_output_tokens += payload.output_tokens
    run.total_cost_micro += outcome.cost_micro

    decision = "deny" if not outcome.allowed else "allow"
    if not outcome.allowed:
        # Budget hard-capped: the engine has latched its kill switch. This run
        # is killed and the enforcement is recorded as a denied decision.
        run.status = "killed"
        run.ended_at = datetime.now(timezone.utc)
        kill_sealed = await asyncio.to_thread(
            hub.emit, run.external_run_id, "error",
            {"reason": "budget cap reached; kill switch engaged"}, name="kill_switch",
        )
        if kill_sealed:
            session.add(_project_event(run, kill_sealed))
        session.add(models.PolicyDecision(
            tenant_id=current.tenant_id, run_id=run.id, server="budget",
            tool="spend", decision="deny",
            reason="; ".join(f"{v['budget']} {v['kind']}" for v in outcome.violations) or "cap",
        ))

    await session.commit()
    await session.refresh(run)

    await _audit(hub, current, project, run, event_type="completion",
                 decision=decision, name=payload.model,
                 reason="usage recorded" if outcome.allowed else "budget cap",
                 extra={"cost_usd": micro_to_usd(outcome.cost_micro)})
    await _broadcast_run(bus, current, run, "run.usage")
    await _broadcast_budget(bus, hub, current)

    return schemas.UsageOutcome(
        cost_usd=micro_to_usd(outcome.cost_micro),
        allowed=outcome.allowed,
        state=outcome.state,
        alerts=outcome.alerts,
        violations=outcome.violations,
        run_status=run.status,
    )


async def report_tool_call(
    session: AsyncSession,
    hub: EngineHub,
    bus: EventBus,
    current: CurrentUser,
    run: models.Run,
    payload: schemas.ToolCallReport,
) -> schemas.DecisionOut:
    project = await session.get(models.Project, run.project_id)

    policy_ok = await asyncio.to_thread(
        hub.policy_allows, str(current.tenant_id), [current.user.role],
        payload.server, payload.tool,
    )
    cost_dec = await asyncio.to_thread(
        hub.check, client=str(current.tenant_id),
        scope_id=project.cost_scope_id, agent=run.external_run_id,
    )
    allowed = policy_ok and cost_dec.allowed
    if not policy_ok:
        reason = f"policy denies {payload.server}__{payload.tool}"
    elif not cost_dec.allowed:
        reason = "blocked by budget kill switch"
    else:
        reason = "allowed"

    sealed = await asyncio.to_thread(
        hub.emit, run.external_run_id, "tool_call",
        {"arguments": payload.arguments, "decision": "allow" if allowed else "deny"},
        name=payload.tool,
    )
    if sealed:
        session.add(_project_event(run, sealed))
    record = models.PolicyDecision(
        tenant_id=current.tenant_id, run_id=run.id, server=payload.server,
        tool=payload.tool, decision="allow" if allowed else "deny", reason=reason,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    await _audit(hub, current, project, run, event_type="tool_call",
                 decision="allow" if allowed else "deny", name=payload.tool, reason=reason)
    await _broadcast(bus, current, {
        "type": "policy.decision", "run_id": str(run.id),
        "tool": payload.tool, "decision": record.decision, "reason": reason,
    })
    return schemas.DecisionOut.model_validate(record)


async def complete_run(
    session: AsyncSession,
    hub: EngineHub,
    bus: EventBus,
    current: CurrentUser,
    run: models.Run,
    payload: schemas.RunComplete,
) -> models.Run:
    if run.status not in ("running",):
        return run
    sealed = await asyncio.to_thread(
        hub.end_run, run.external_run_id,
        status="ok" if payload.status == "completed" else "error",
        output=payload.output,
    )
    if sealed:
        session.add(_project_event(run, sealed))
    run.status = payload.status
    run.ended_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(run)

    project = await session.get(models.Project, run.project_id)
    await _audit(hub, current, project, run, event_type="server_lifecycle",
                 decision="allow", name="run_end", reason=f"run {payload.status}")
    await _broadcast_run(bus, current, run, "run.completed")
    return run


async def ingest_run(
    session: AsyncSession,
    hub: EngineHub,
    bus: EventBus,
    current: CurrentUser,
    payload: schemas.IngestRun,
) -> tuple[models.Run, list[schemas.UsageOutcome]]:
    """Open a run, replay usage and tool calls, then close — all in one call."""
    run = await create_run(session, hub, bus, current, schemas.RunCreate(
        project_slug=payload.project_slug, agent_name=payload.agent_name,
        label=payload.label, input=payload.input, meta=payload.meta,
    ))
    outcomes: list[schemas.UsageOutcome] = []
    for usage in payload.usage:
        outcomes.append(await report_usage(session, hub, bus, current, run, usage))
        if run.status == "killed":
            break
    if run.status != "killed":
        for tool_call in payload.tool_calls:
            await report_tool_call(session, hub, bus, current, run, tool_call)
        await complete_run(session, hub, bus, current, run,
                           schemas.RunComplete(status=payload.status, output=payload.output))
    return run, outcomes


# --------------------------------------------------------------------------- #
# Audit + broadcast helpers
# --------------------------------------------------------------------------- #
async def _audit(
    hub: EngineHub, current: CurrentUser, project: models.Project, run: models.Run,
    *, event_type: str, decision: str, name: str, reason: str, extra: dict | None = None,
) -> None:
    event = {
        "event_type": event_type,
        "decision": decision,
        "decision_reason": reason,
        "actor": {"id": str(current.user.id), "type": "service", "name": current.user.email},
        "tool": {"name": name},
        "labels": {"tenant": str(current.tenant_id), "project": project.slug,
                   "run": run.external_run_id},
        "arguments": extra or {},
    }
    await asyncio.to_thread(hub.audit, event)


async def _broadcast(bus: EventBus, current: CurrentUser, message: dict) -> None:
    await bus.publish(str(current.tenant_id), message)


async def _broadcast_run(bus: EventBus, current: CurrentUser, run: models.Run, kind: str) -> None:
    await _broadcast(bus, current, {
        "type": kind,
        "run": {
            "id": str(run.id), "external_run_id": run.external_run_id,
            "project_id": str(run.project_id), "agent_name": run.agent_name,
            "status": run.status, "total_cost_usd": micro_to_usd(run.total_cost_micro),
            "total_input_tokens": run.total_input_tokens,
            "total_output_tokens": run.total_output_tokens,
            "started_at": run.started_at.isoformat(),
            "ended_at": run.ended_at.isoformat() if run.ended_at else None,
            "label": run.label,
        },
    })


async def _broadcast_budget(bus: EventBus, hub: EngineHub, current: CurrentUser) -> None:
    prefix = f"{current.tenant_id}/"
    status = await asyncio.to_thread(hub.budget_status, prefix)
    await _broadcast(bus, current, {"type": "budget.updated", "budgets": status})
