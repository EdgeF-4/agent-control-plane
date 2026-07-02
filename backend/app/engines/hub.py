"""The engine hub: one instance of each engine, behind a small, typed surface.

The hub is deliberately synchronous and thread-safe. The async service layer
calls it through ``asyncio.to_thread`` so engine work never blocks the event
loop. The cost engine locks internally; the SIEM pipeline and the run registry
are guarded here.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Cost engine — spend caps + kill switch.
import cost_governor as cg
from cost_governor.config import Config as CostConfig

# Recorder engine — tamper-evident, hash-chained run timeline.
import flight_recorder as fr
from flight_recorder.hashchain import GENESIS_HASH
from flight_recorder.recorder import RunLog

# Audit/SIEM engine — normalize, redact, forward.
from mcp_siem_bridge.pipeline import Pipeline
from mcp_siem_bridge.sinks import SinkError

# Policy + auth engine — allow/deny decisions and credential verification.
from mcp_gateway.auth import (
    Authenticator,
    OAuth2Verifier,
    build_jwks_key_store,
)
from mcp_gateway.config import ApiKeyRecord, PolicyConfig, PolicyRule
from mcp_gateway.errors import ConfigError
from mcp_gateway.identity import ClientIdentity
from mcp_gateway.policy import PolicyEngine

# Eval engine — reliability/regression history.
from agent_eval.config import Config as EvalConfig, Pricing
from agent_eval.regression import compare_runs
from agent_eval.runner import run_suite
from agent_eval.store import Store as EvalStore
from agent_eval.suite import suite_from_dict

from ..config import Settings
from ..evals.adapters import build_eval_adapter, labeling_config
from ..evals.suites import builtin_suites


def _credential_from_headers(headers) -> str | None:
    """Pull the raw credential from headers, matching the gateway's own rules
    (``X-API-Key`` wins, else ``Authorization: Bearer``/``ApiKey``)."""

    def _get(name: str):
        getter = getattr(headers, "get", None)
        if getter is not None:
            value = getter(name)
            if value is not None:
                return value
        try:
            for key, value in headers.items():
                if key.lower() == name.lower():
                    return value
        except AttributeError:
            pass
        return None

    api_key = _get("x-api-key")
    if api_key:
        return api_key.strip()
    authorization = _get("authorization")
    if not authorization:
        return None
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() in ("bearer", "apikey") and credential:
        return credential.strip()
    return None


def _project_claim_from_headers(headers, claim: str) -> str | None:
    """Read one claim from the request's bearer JWT (already signature-verified)."""
    import base64
    import json

    token = _credential_from_headers(headers)
    if not token or token.count(".") != 2:
        return None
    try:
        segment = token.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(segment))
    except Exception:
        return None
    value = payload.get(claim)
    return str(value) if value is not None else None


@dataclass
class IngestIdentity:
    """Who authenticated a run-ingest request, and how.

    ``auth_method`` is ``"api_key"`` (a per-project key) or ``"oauth2"`` (a JWT
    from an external IdP). ``project_hint`` is a project slug carried by a JWT's
    optional project claim, used to scope the token to one project.
    """

    client_id: str
    auth_method: str
    roles: tuple[str, ...] = ()
    project_hint: str | None = None


@dataclass
class SpendOutcome:
    """Result of recording usage and re-checking the budget."""

    cost_micro: int
    alerts: list[str]
    allowed: bool
    state: str
    violations: list[dict]
    # Full alert detail (threshold / cap / kill) fired by this charge, for the
    # live feed and audit — richer than the bare ``alerts`` kinds above.
    alert_events: list[dict]


class EngineHub:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._runs: dict[str, RunLog] = {}
        # Ingest authenticator, composed from the gateway engine. Rebuilt from the
        # database whenever project API keys change, and from config when the
        # optional OAuth2/JWKS verifier is configured. Empty until keys are synced.
        self._ingest_api_key_records: tuple[ApiKeyRecord, ...] = ()
        self._ingest_oauth: OAuth2Verifier | None = None
        self._ingest_project_claim: str | None = None
        self._ingest_authenticator = Authenticator(api_keys=())

        # --- cost engine -------------------------------------------------
        cost_db = settings.engines.cost.get("database_path") or settings.engine_path(
            "cost", "ledger.db"
        )
        Path(cost_db).parent.mkdir(parents=True, exist_ok=True)
        self.governor = cg.Governor(
            config=CostConfig(
                database_path=cost_db,
                default_warn_threshold=settings.engines.cost.get(
                    "default_warn_threshold", 0.8
                ),
            )
        )

        # --- recorder engine --------------------------------------------
        log_dir = settings.engines.recorder.get("log_dir") or settings.engine_path("runs")
        os.makedirs(log_dir, exist_ok=True)
        self._recorder_config = {
            "log_dir": log_dir,
            "max_run_bytes": settings.engines.recorder.get("max_run_bytes", 8 * 1024 * 1024),
            "max_runs": settings.engines.recorder.get("max_runs", 5000),
        }
        self.recorder = fr.Recorder(config=self._recorder_config)

        # --- audit/SIEM engine ------------------------------------------
        sinks = settings.engines.siem.get("sinks")
        if not sinks:
            sinks = [
                {
                    "type": "file",
                    "name": "local-audit",
                    "enabled": True,
                    "path": settings.engine_path("siem", "audit.ndjson"),
                }
            ]
        for sink in sinks:
            if sink.get("type") == "file" and sink.get("path"):
                Path(sink["path"]).parent.mkdir(parents=True, exist_ok=True)
        self.siem = Pipeline(
            {
                "version": "1.0",
                # Deliver every event immediately rather than buffering a batch.
                "batching": {"max_events": 1, "max_bytes": 1 << 20, "flush_interval_seconds": 5},
                "redaction": settings.engines.siem.get("redaction", {"enabled": True}),
                "sinks": sinks,
            }
        )

        # --- policy engine ----------------------------------------------
        self.policy = self._build_policy(settings.engines.policy)

        # --- eval engine -------------------------------------------------
        # The eval store's SQLite connection is thread-bound, so each call
        # opens its own against this path (it runs inside ``asyncio.to_thread``).
        self._eval_store_path = settings.engine_path("eval", "history.db")
        # Named suites (built-in + config-defined), resolved by the scheduler,
        # gate, and routes. Seeded with the offline built-ins so a bare hub can
        # run reliability checks; ``set_eval_suites`` adds config-defined ones.
        self._eval_suites: dict[str, dict] = {s["name"]: dict(s) for s in builtin_suites()}

    # ------------------------------------------------------------------ #
    # Policy
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_policy(raw: dict) -> PolicyEngine:
        rules = tuple(
            PolicyRule(
                client_id=r.get("client_id", "*"),
                roles=tuple(r.get("roles", ())),
                allow=tuple(r.get("allow", ())),
                deny=tuple(r.get("deny", ())),
            )
            for r in raw.get("rules", [])
        )
        cfg = PolicyConfig(default=raw.get("default", "allow"), rules=rules)
        return PolicyEngine(cfg, separator="__")

    def policy_allows(self, client_id: str, roles: list[str], server: str, tool: str) -> bool:
        identity = ClientIdentity(
            client_id=client_id, roles=tuple(roles), auth_method="oauth2"
        )
        return bool(self.policy.allows(identity, server, tool))

    # ------------------------------------------------------------------ #
    # Ingest authentication (per-project API keys)
    # ------------------------------------------------------------------ #
    def _rebuild_ingest_authenticator(self) -> None:
        """Compose the api-key records and the optional OAuth2 verifier. Locked."""
        self._ingest_authenticator = Authenticator(
            api_keys=self._ingest_api_key_records, oauth2=self._ingest_oauth
        )

    def set_ingest_keys(self, keys: list[dict]) -> None:
        """Rebuild the ingest authenticator from the active API-key records.

        Each record maps a key digest to its own id (used as the client id) and
        the ``agent`` role. Revoked keys are simply left out of the set. Any
        configured OAuth2 verifier is preserved alongside the keys.
        """
        records = tuple(
            ApiKeyRecord(client_id=k["client_id"], key_sha256=k["key_sha256"], roles=("agent",))
            for k in keys
        )
        with self._lock:
            self._ingest_api_key_records = records
            self._rebuild_ingest_authenticator()

    def configure_ingest_oauth(self, cfg) -> None:
        """Enable (or disable) JWT ingest from an external IdP.

        Builds the gateway's :class:`OAuth2Verifier` — RS256 keys resolve from a
        JWKS file or endpoint (cached, rotation-aware) and HS256 from a shared
        secret in the environment — and composes it into the ingest
        authenticator alongside the per-project API keys.
        """
        verifier: OAuth2Verifier | None = None
        project_claim: str | None = None
        if cfg is not None and getattr(cfg, "enabled", False):
            verifier = self._build_ingest_oauth(cfg)
            project_claim = cfg.project_claim or None
        with self._lock:
            self._ingest_oauth = verifier
            self._ingest_project_claim = project_claim
            self._rebuild_ingest_authenticator()

    @staticmethod
    def _build_ingest_oauth(cfg) -> OAuth2Verifier:
        common = dict(
            issuer=cfg.issuer,
            audience=cfg.audience,
            client_id_claim=cfg.client_id_claim,
            roles_claim=cfg.roles_claim,
            leeway_seconds=cfg.leeway_seconds,
        )
        if cfg.algorithm == "RS256":
            if not (cfg.jwks_url or cfg.jwks_path):
                raise ConfigError(
                    "ingest oauth2 RS256 requires 'jwks_url' or 'jwks_path'"
                )
            # build_jwks_key_store reads cfg.jwks_path / jwks_url / jwks_cache_seconds.
            return OAuth2Verifier(
                algorithm="RS256", key_store=build_jwks_key_store(cfg), **common
            )
        secret = os.environ.get(cfg.hs256_secret_env or "")
        if not secret:
            raise ConfigError(
                "ingest oauth2 HS256 is enabled but its secret env var is empty"
            )
        return OAuth2Verifier(algorithm="HS256", secret=secret, **common)

    def authenticate_ingest(self, headers) -> "IngestIdentity | None":
        """Authenticate a run-ingest request, returning who and how, or ``None``.

        Composes the gateway's :class:`Authenticator`, so header parsing
        (``Authorization: Bearer``/``ApiKey`` and ``X-API-Key``), the
        constant-time api-key digest match, and JWT verification (signature,
        expiry, issuer, audience) are all the gateway's. For a JWT, the optional
        project claim is read from the already-verified token.
        """
        with self._lock:
            authenticator = self._ingest_authenticator
            project_claim = self._ingest_project_claim
        identity, _reason = authenticator.authenticate(headers)
        if identity is None:
            return None
        project_hint = None
        if identity.auth_method == "oauth2" and project_claim:
            project_hint = _project_claim_from_headers(headers, project_claim)
        return IngestIdentity(
            client_id=identity.client_id,
            auth_method=identity.auth_method,
            roles=tuple(identity.roles),
            project_hint=project_hint,
        )

    # ------------------------------------------------------------------ #
    # Cost
    # ------------------------------------------------------------------ #
    def ensure_budget(
        self,
        *,
        budget_id: str,
        name: str,
        scope_id: str,
        limit_micro: int,
        period: str,
        warn_threshold: float,
        latch: bool,
    ) -> None:
        self.governor.set_budget(
            cg.Budget(
                budget_id=budget_id,
                name=name,
                scope_type=cg.ScopeType.PROJECT,
                scope_id=scope_id,
                limit_micro=limit_micro,
                period=cg.Period(period),
                warn_threshold=warn_threshold,
                hard_cap=True,
                latch=latch,
                enabled=limit_micro > 0,
            )
        )

    def _context(self, *, client: str, scope_id: str, agent: str) -> "cg.Context":
        return cg.Context(client=client, project=scope_id, agent=agent)

    def check(
        self, *, client: str, scope_id: str, agent: str, estimated_cost_micro: int = 0
    ) -> "cg.Decision":
        return self.governor.check(
            self._context(client=client, scope_id=scope_id, agent=agent),
            estimated_cost_micro,
        )

    def record_and_check(
        self,
        *,
        client: str,
        scope_id: str,
        agent: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_micro: int | None = None,
    ) -> SpendOutcome:
        ctx = self._context(client=client, scope_id=scope_id, agent=agent)
        result = self.governor.record_llm(
            ctx,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micro=cost_micro,
        )
        decision = self.governor.check(ctx, 0)
        return SpendOutcome(
            cost_micro=result.event.cost_micro,
            alerts=[a.kind.value for a in result.alerts],
            allowed=decision.allowed,
            state=decision.state.value,
            violations=[
                {
                    "budget": v.name,
                    "kind": v.kind.value,
                    "spent_micro": v.spent_micro,
                    "limit_micro": v.limit_micro,
                    "fraction": v.fraction,
                }
                for v in decision.violations
            ],
            alert_events=[
                {
                    "kind": a.kind.value,
                    "message": a.message,
                    "budget": a.name,
                    "scope_id": a.scope_id,
                    "limit_micro": a.limit_micro,
                    "spent_micro": a.spent_micro,
                    "fraction": a.fraction,
                }
                for a in result.alerts
            ],
        )

    def kill(self, scope_id: str, reason: str) -> None:
        self.governor.kill(cg.ScopeType.PROJECT, scope_id, reason=reason)

    def release(self, scope_id: str) -> None:
        self.governor.release(cg.ScopeType.PROJECT, scope_id)

    def budget_status(self, scope_prefix: str) -> list[dict]:
        """Cost-vs-budget for every budget belonging to a tenant."""
        out: list[dict] = []
        for bs in self.governor.status():
            scope_id = bs.budget.scope_id or ""
            if not scope_id.startswith(scope_prefix):
                continue
            out.append(
                {
                    "budget_id": bs.budget.budget_id,
                    "name": bs.budget.name,
                    "scope_id": scope_id,
                    "limit_micro": bs.budget.limit_micro,
                    "spent_micro": bs.spent_micro,
                    "period": bs.budget.period.value,
                    "state": bs.state.value,
                    "fraction": (
                        bs.spent_micro / bs.budget.limit_micro
                        if bs.budget.limit_micro
                        else 0.0
                    ),
                }
            )
        return out

    # ------------------------------------------------------------------ #
    # Recorder (tamper-evident run timeline)
    # ------------------------------------------------------------------ #
    def open_run(self, external_run_id: str, *, input: Any = None, meta: dict | None = None) -> dict:
        with self._lock:
            run = self.recorder.open_run(run_id=external_run_id, force=True)
            self._runs[external_run_id] = run
            return run.run_start(input=input, meta=meta or {})

    def emit(
        self,
        external_run_id: str,
        event_type: str,
        payload: dict | None = None,
        *,
        name: str | None = None,
        tokens: dict | None = None,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
        labels: dict | None = None,
    ) -> dict | None:
        run = self._get_run(external_run_id)
        return run.emit(
            event_type,
            payload=payload,
            name=name,
            tokens=tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            labels=labels,
        )

    def end_run(self, external_run_id: str, *, status: str = "ok", output: Any = None) -> dict | None:
        with self._lock:
            run = self._get_run(external_run_id)
            sealed = run.run_end(status=status, output=output)
            run.close()
            self._runs.pop(external_run_id, None)
            return sealed

    def _get_run(self, external_run_id: str) -> RunLog:
        with self._lock:
            run = self._runs.get(external_run_id)
            if run is None:
                # The recorder records in-process, so a backend restart loses the
                # live RunLog. Rehydrate its chain cursor from the durable on-disk
                # record so a run opened before the restart keeps one unbroken
                # hash chain instead of restarting from the genesis hash.
                run = self._resume_run(external_run_id)
            if run is None:
                raise KeyError(
                    f"run {external_run_id!r} is not open and has no recorded events"
                )
            return run

    def _resume_run(self, external_run_id: str) -> RunLog | None:
        """Reopen an existing run and restore its seq/prev_hash/bytes cursor.

        Must be called with ``self._lock`` held. Returns ``None`` when there is
        no on-disk record to continue (a genuinely unknown run).
        """
        events = fr.load_events(external_run_id, config=self._recorder_config)
        if not events:
            return None
        run = self.recorder.open_run(run_id=external_run_id, force=True)
        # Continue the chain exactly where the persisted record left off.
        run._seq = len(events)
        run._prev_hash = events[-1].get("hash", GENESIS_HASH)
        try:
            run._bytes = os.path.getsize(run.path) if run.path else 0
        except OSError:  # pragma: no cover - file vanished between load and stat
            run._bytes = 0
        # Preserve a prior size-cap truncation so we don't append past it.
        if isinstance(events[-1].get("payload"), dict) and events[-1]["payload"].get("truncated"):
            run._truncated = True
        self._runs[external_run_id] = run
        return run

    def verify_run(self, external_run_id: str) -> dict:
        events = fr.load_events(external_run_id, config=self._recorder_config)
        result = fr.verify_chain(events)
        return {
            "ok": result.ok,
            "event_count": len(events),
            "broken_index": result.broken_index,
            "reason": result.reason,
        }

    def load_run_events(self, external_run_id: str) -> list[dict]:
        return fr.load_events(external_run_id, config=self._recorder_config)

    def summarize_run(self, external_run_id: str) -> dict:
        return fr.summarize(self.load_run_events(external_run_id))

    # ------------------------------------------------------------------ #
    # Audit / SIEM forwarding
    # ------------------------------------------------------------------ #
    def audit(self, event: dict) -> dict:
        """Normalize, redact and forward one audit event; returns the canonical form."""
        with self._lock:
            canonical = self.siem.process_event(event)
            self.siem.flush()
            return canonical

    def siem_status(self) -> dict:
        """Configured sinks, forwarding stats and dead-letter depth (passive)."""
        with self._lock:
            sinks = [
                {"name": s.name, "type": s.type, "enabled": s.enabled}
                for s in self.siem.sinks
            ]
            stats = self.siem.stats
            return {
                "sinks": sinks,
                "stats": {
                    "processed": stats.processed,
                    "batches": stats.batches,
                    "delivered": stats.delivered,
                    "dead_lettered": stats.dead_lettered,
                    "parse_errors": stats.parse_errors,
                },
                "dlq_count": self.siem.dlq.count(),
                "redaction_enabled": bool(self.settings.engines.siem.get("redaction", {}).get("enabled", True)),
            }

    def siem_test_sink(self, name: str) -> dict:
        """Run a sink's own connectivity self-test (an explicit operator action)."""
        with self._lock:
            sink = next((s for s in self.siem.sinks if s.name == name), None)
            if sink is None:
                return {"ok": False, "detail": f"no sink named {name!r}"}
            result = sink.test()
            return {"ok": bool(result.ok), "detail": result.detail}

    def siem_dlq_entries(self, limit: int = 100) -> list[dict]:
        """Summaries of dead-lettered batches awaiting replay (no raw payloads)."""
        with self._lock:
            entries = self.siem.dlq.list_entries()[:limit]
            return [
                {
                    "sink": e.sink,
                    "error": e.error,
                    "attempts": e.attempts,
                    "failed_at": e.failed_at,
                    "event_count": len(e.events),
                }
                for e in entries
            ]

    def siem_dlq_replay(self, sink_filter: str | None = None, limit: int | None = None) -> dict:
        """Re-deliver dead-lettered batches to their original sink."""
        with self._lock:
            sink_map = {s.name: s for s in self.siem.sinks}

            def deliver(name: str, events: list) -> None:
                sink = sink_map.get(name)
                if sink is None:
                    raise SinkError(f"no sink named {name!r} is currently configured")
                sink.emit(events)

            result = self.siem.dlq.replay(deliver, sink_filter=sink_filter, limit=limit)
            return {
                "replayed": result.replayed,
                "failed": result.failed,
                "skipped": result.skipped,
            }

    # ------------------------------------------------------------------ #
    # Eval (reliability/regression)
    # ------------------------------------------------------------------ #
    def set_eval_suites(self, specs: list[dict]) -> None:
        """Register the named suites the scheduler, gate, and routes resolve."""
        with self._lock:
            self._eval_suites = {s["name"]: dict(s) for s in specs if s.get("name")}

    def eval_suite_names(self) -> list[str]:
        with self._lock:
            return list(self._eval_suites)

    def eval_suites_meta(self) -> list[dict]:
        """Name, adapter type, case count, and whether each suite is runnable."""
        with self._lock:
            out = []
            for name, spec in self._eval_suites.items():
                adapter = spec.get("adapter") or {}
                atype = adapter.get("type", "golden")
                # An http suite needs a configured endpoint to actually run.
                runnable = atype != "http" or bool(adapter.get("base_url"))
                out.append({
                    "name": name, "adapter": atype,
                    "cases": len(spec.get("cases") or []), "runnable": runnable,
                })
            return out

    def _resolve_eval_suite(self, suite) -> dict:
        if isinstance(suite, str):
            with self._lock:
                spec = self._eval_suites.get(suite)
            if spec is None:
                raise KeyError(f"unknown eval suite {suite!r}")
            return spec
        return suite  # already a spec dict

    def run_eval(self, suite, label: str | None = None):
        """Run a named (or inline) suite through its adapter; record + diff.

        ``suite`` is a registered suite name or a full spec dict. The adapter is
        built from the spec (golden/http/…); scoring, storage, and the baseline
        regression diff are the eval engine's.
        """
        spec = self._resolve_eval_suite(suite)
        engine_suite = suite_from_dict({
            "name": spec["name"],
            "version": spec.get("version", "1"),
            "cases": spec["cases"],
        })
        adapter = build_eval_adapter(spec.get("adapter") or {}, base_dir=self.settings.data_dir)
        cfg = EvalConfig(
            suite="inline",
            adapter=labeling_config(spec.get("adapter") or {}),
            pricing=Pricing.from_dict(spec.get("pricing") or {}),
            seed=spec.get("seed"),
        )
        result = run_suite(cfg, engine_suite, adapter, label=label)
        store = EvalStore(self._eval_store_path)
        store.save_run(result)
        comparison = None
        baseline = store.get_baseline(engine_suite.name)
        if baseline is not None and baseline.run_id != result.run_id:
            comparison = compare_runs(baseline, result)
        return result, comparison

    def set_eval_baseline(self, suite_name: str, run_id: str) -> None:
        EvalStore(self._eval_store_path).set_baseline(suite_name, run_id)

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        with self._lock:
            for run in list(self._runs.values()):
                try:
                    run.close()
                except Exception:
                    pass
            self._runs.clear()
            try:
                self.siem.close()
            except Exception:
                pass
