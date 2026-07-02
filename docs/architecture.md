# Architecture

The control plane is one product with a single job: give a team running automated
agents in production a place to **see what their agents did, what it cost, what was
allowed, and prove none of it was tampered with** — without sending any of that data
to someone else's cloud.

It does not reinvent the hard parts. It composes five focused engines, each of which
owns its own authoritative store, and projects a unified, multi-tenant, queryable view
on top for the dashboard and the API.

## The shape

```
                          ┌──────────────────────────────────────────┐
                          │                Browser                    │
                          │      dark cockpit dashboard (SPA)         │
                          └───────────────────┬───────────────────────┘
                                              │ HTTPS / JSON + WebSocket
                          ┌───────────────────▼───────────────────────┐
                          │            Control Plane API                │
                          │              (FastAPI, async)               │
                          │                                             │
                          │  auth + tenancy   ingest    projections     │
                          │  ───────────────  ────────  ─────────────   │
                          │        │             │            │         │
                          │   ┌────┴────┐   ┌────┴─────┐  ┌───┴────┐    │
                          │   │ engines/ adapters compose the modules │  │
                          │   └──┬───┬───┬───┬───────────┘            │  │
                          └──────┼───┼───┼───┼────────────────────────┘
                                 │   │   │   │
            ┌────────────────────┘   │   │   └─────────────────────────┐
            ▼                        ▼   ▼                             ▼
   ┌─────────────────┐   ┌──────────────────┐   ┌──────────────┐  ┌──────────────┐
   │  cost engine    │   │  recorder engine │   │ audit/SIEM   │  │ gateway eng. │
   │ (spend caps +   │   │ (tamper-evident  │   │ forwarder    │  │ (policy +    │
   │  kill switch +  │   │  hash-chained    │   │ (file/Splunk/│  │  JWT + API-  │
   │  alerts)        │   │  run timeline,   │   │  Elastic/... │  │  key auth)   │
   │  SQLite ledger  │   │  restart-durable)│   │  file sink + │  │  rules in    │
   │                 │   │  JSONL per run   │   │  DLQ         │  │  config      │
   └─────────────────┘   └──────────────────┘   └──────────────┘  └──────────────┘
            └───────── eval engine (reliability/regression + gate, SQLite) ───────┘

                          ┌──────────────────────────────────────────┐
                          │     PostgreSQL — unified projection        │
                          │  tenants · users · projects · runs ·        │
                          │  run_events · policy_decisions ·            │
                          │  api_keys · eval_runs · eval_schedules      │
                          │  (schema owned by Alembic migrations)       │
                          └──────────────────────────────────────────┘
```

## Why this split

Each engine is the source of truth for its own domain, because each one already
guarantees something the projection cannot:

| Engine | Owns | Guarantee it provides |
| --- | --- | --- |
| **cost** | the spend ledger | integer micro-dollar accounting, no float drift; budgets that latch a kill switch and emit threshold/cap/kill alerts |
| **recorder** | the run timeline | append-only JSONL sealed with a SHA-256 hash chain — reordering, editing, or deleting any event is detectable |
| **audit/SIEM** | forwarding | normalization + redaction + reliable delivery (retry + dead-letter) to Splunk/Elastic/Datadog/webhook/file |
| **gateway** | access + identity | deny-by-precedence policy against an identity; JWT minting/verification for users and constant-time API-key auth for agents |
| **eval** | reliability history | suite runs, scoring, and regression vs. a pinned baseline |

The **gateway** engine is composed twice: its `PolicyEngine` decides tool
allow/deny, and its auth machinery backs both human logins (HS256 JWTs via
`encode_jwt_hs256` / `OAuth2Verifier`) and per-project agent keys (its
`Authenticator`, which does the header parsing and constant-time digest match).

PostgreSQL is **not** another source of truth. It is a fast, joinable projection that
the API writes to inside the same request that calls an engine. The dashboard reads
the projection; integrity-sensitive views (the audit trail) can re-verify against the
recorder's hash chain on demand, so the projection can never quietly lie.

## The end-to-end path (phase 1)

A single ingested run exercises every engine:

1. **Open the run.** `POST /api/v1/runs` opens a tamper-evident run in the recorder
   (`Recorder.open_run`) and writes a `runs` row in Postgres, scoped to the caller's
   tenant and project.
2. **Report usage.** `POST /api/v1/runs/{id}/usage` records spend through the cost
   engine (`Governor.record_llm`) and then asks it for a decision
   (`Governor.check`). If a budget hard-caps, the engine returns `deny` and — for a
   latching budget — engages its kill switch.
3. **Enforce.** A denied decision flips the run to `killed`, and the control plane
   records the enforcement as a `policy_decision` and a sealed recorder event.
4. **Audit.** Every step is sealed into the recorder's hash chain *and* forwarded to
   the SIEM pipeline (`Pipeline.process_event`), which redacts and ships it to the
   configured sinks. A redacted projection lands in `audit_entries`.
5. **Show it.** The dashboard streams the run, its cost-vs-budget, the allow/deny
   decisions, and the audit trail live over a WebSocket, and can verify the chain.

## What phase 2 adds

Phase 2 hardens the same slice into something you can run and hand off in
production, without changing the model above:

- **Durable recorder state.** The recorder records in-process, so a backend
  restart used to lose an in-flight run's chain cursor. The hub now rehydrates
  `seq` / `prev_hash` / byte count from the recorder's own on-disk record the
  first time a restarted process touches a run, so events before and after a
  restart form one unbroken chain. (`EngineHub._resume_run`.)
- **Agent authentication.** Agents POST runs with a per-project API key instead
  of an operator token. The key digest (never the plaintext) lives in
  `api_keys`; the hub builds the gateway's `Authenticator` from the active keys
  and rebuilds it on any change. An API-key principal is scoped to exactly one
  project — it cannot open or touch a run in another.
- **Migrations.** Alembic owns the schema and the API brings it to `head` on
  startup (in a worker thread, since env.py drives an async engine). SQLite gets
  batch-mode ALTERs; a test asserts zero drift between the migrations and the
  models.
- **SIEM operations.** `/siem/*` surfaces the live pipeline — configured sinks,
  a per-sink connectivity self-test, delivery stats, and the dead-letter queue
  with one-click replay to the original sink. Sinks stay configured as code.
- **Cost feed.** Per-run cumulative cost/tokens and a burn rate power a chart in
  the run drawer; the cost engine's threshold/cap/kill alerts ride the live feed
  as toasts.
- **Eval scheduling + gate.** A background loop runs due `eval_schedules`; each
  run stores its regression diff (`compare_runs`). A project's `eval_gate_suite`
  makes new runs 409 while that suite's latest run is regressed.
- **Administration.** Tenants (superadmin), users, projects, budgets, gates, and
  API keys are all managed from the dashboard.

## Multi-tenancy

Every row in the projection carries a `tenant_id`. Engine state is partitioned by
encoding tenant and project into each engine's native dimensions — the cost engine's
`Context(client, project, agent)`, the recorder's run id namespace, and SIEM event
`labels`. A request can only ever touch rows and engine scopes that belong to the
tenant on its access token.

## Auth

There are two credential paths, both backed by the gateway engine's own crypto:

- **Operators** log in with a password (bcrypt-hashed) and receive an HS256 JWT
  minted and verified with `encode_jwt_hs256` / `OAuth2Verifier`. Tokens carry
  the subject, tenant, and roles; every protected route resolves them into a
  scoped identity.
- **Agents** present a per-project API key on ingest routes. The gateway's
  `Authenticator` parses the header (`Authorization: Bearer`/`ApiKey` or
  `X-API-Key`) and matches the key's SHA-256 digest in constant time, yielding an
  `agent` principal scoped to that project. The ingest routes accept either
  credential; read routes stay operator-only.

So the same audited token machinery that guards tool calls guards the dashboard,
and agents authenticate without ever holding an operator login.

## Self-host & compliance posture

- **No outbound calls.** Nothing phones home. The only network egress is to the SIEM
  sinks *you* configure. The default sink is a local file.
- **Air-gap friendly.** Every engine is standard-library or runs against a local
  store; the unified store is a Postgres container you own. The bundled eval adapter
  is offline.
- **Secrets stay in `config.json`** (chmod 600), never in environment files or the
  image.
- **The audit trail is tamper-evident**, so "show me everything that agent did, and
  prove it wasn't edited" is a query, not a promise.
