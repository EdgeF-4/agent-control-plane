"""API request/response models.

Money crosses the wire as USD (human-friendly) and is converted to integer
micro-dollars internally, where the cost engine keeps its books.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- auth ------------------------------------------------------------------ #
class LoginRequest(BaseModel):
    # A login identifier, not necessarily a deliverable address — self-hosted
    # installs routinely use internal domains (.local, .internal, .test).
    email: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    name: str
    role: str
    tenant_id: uuid.UUID


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --- projects -------------------------------------------------------------- #
class ProjectCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str
    budget_usd: float = 0.0
    budget_period: str = "monthly"
    budget_warn_threshold: float = 0.8
    budget_latches_kill: bool = True


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    name: str
    budget_limit_micro: int
    budget_period: str
    budget_warn_threshold: float
    budget_latches_kill: bool


# --- api keys -------------------------------------------------------------- #
class ApiKeyCreate(BaseModel):
    name: str = ""


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    # The plaintext key, returned exactly once at creation time.
    key: str


# --- runs ------------------------------------------------------------------ #
class RunCreate(BaseModel):
    # Optional for API-key callers: the key already implies its project.
    project_slug: str = ""
    agent_name: str = ""
    label: str = ""
    input: dict | None = None
    meta: dict = Field(default_factory=dict)


class UsageReport(BaseModel):
    provider: str = "local"
    model: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    # Either give an explicit cost, or let the engine price it from its table.
    cost_usd: float | None = None
    name: str | None = None
    latency_ms: float | None = None


class ToolCallReport(BaseModel):
    server: str = "tools"
    tool: str
    arguments: dict = Field(default_factory=dict)


class RunComplete(BaseModel):
    status: str = "completed"  # completed | error
    output: dict | None = None


class IngestRun(BaseModel):
    """Atomic ingest: open, report usage and tool calls, then close in one call."""

    project_slug: str = ""
    agent_name: str = ""
    label: str = ""
    input: dict | None = None
    meta: dict = Field(default_factory=dict)
    usage: list[UsageReport] = Field(default_factory=list)
    tool_calls: list[ToolCallReport] = Field(default_factory=list)
    status: str = "completed"
    output: dict | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    external_run_id: str
    project_id: uuid.UUID
    agent_name: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    total_input_tokens: int
    total_output_tokens: int
    total_cost_micro: int
    label: str


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    event_type: str
    name: str | None
    ts: datetime
    cost_micro: int | None
    tokens: dict | None
    latency_ms: float | None
    payload_summary: dict
    hash: str
    prev_hash: str


class DecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    server: str
    tool: str
    decision: str
    reason: str
    ts: datetime


class UsageOutcome(BaseModel):
    cost_usd: float
    allowed: bool
    state: str
    alerts: list[str]
    violations: list[dict]
    run_status: str


class VerifyResult(BaseModel):
    ok: bool
    event_count: int
    broken_index: int | None = None
    reason: str | None = None


# --- evals ----------------------------------------------------------------- #
class EvalScheduleCreate(BaseModel):
    suite_name: str = ""
    project_slug: str = ""
    interval_minutes: int = 1440
    enabled: bool = True


class EvalScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    suite_name: str
    project_id: uuid.UUID | None
    interval_minutes: int
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime


class EvalGateUpdate(BaseModel):
    # Empty / null clears the gate.
    suite_name: str | None = None
