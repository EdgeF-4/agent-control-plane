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
   │  cost engine    │   │  recorder engine │   │ audit/SIEM   │  │ policy engine│
   │ (spend caps +   │   │ (tamper-evident  │   │ forwarder    │  │ (allow/deny) │
   │  kill switch)   │   │  hash-chained    │   │ (file/Splunk/│  │              │
   │                 │   │  run timeline)   │   │  Elastic/... │  │              │
   │  SQLite ledger  │   │  append-only     │   │  file sink + │  │  rules in    │
   │                 │   │  JSONL per run   │   │  DLQ         │  │  config      │
   └─────────────────┘   └──────────────────┘   └──────────────┘  └──────────────┘
            └──────────────── eval engine (reliability/regression, SQLite) ───────┘

                          ┌──────────────────────────────────────────┐
                          │     PostgreSQL — unified projection        │
                          │  tenants · users · projects · runs ·        │
                          │  run_events · policy_decisions ·            │
                          │  audit_entries · eval_runs                  │
                          └──────────────────────────────────────────┘
```

## Why this split

Each engine is the source of truth for its own domain, because each one already
guarantees something the projection cannot:

| Engine | Owns | Guarantee it provides |
| --- | --- | --- |
| **cost** | the spend ledger | integer micro-dollar accounting, no float drift; budgets that latch a kill switch |
| **recorder** | the run timeline | append-only JSONL sealed with a SHA-256 hash chain — reordering, editing, or deleting any event is detectable |
| **audit/SIEM** | forwarding | normalization + redaction + reliable delivery (retry + dead-letter) to Splunk/Elastic/Datadog/webhook/file |
| **policy** | access decisions | deny-by-precedence rule evaluation against an identity |
| **eval** | reliability history | suite runs, scoring, and regression vs. a pinned baseline |

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

## Multi-tenancy

Every row in the projection carries a `tenant_id`. Engine state is partitioned by
encoding tenant and project into each engine's native dimensions — the cost engine's
`Context(client, project, agent)`, the recorder's run id namespace, and SIEM event
`labels`. A request can only ever touch rows and engine scopes that belong to the
tenant on its access token.

## Auth

Passwords are stored as bcrypt hashes. Access tokens are HS256 JWTs minted and
verified with the gateway engine's own crypto (`encode_jwt_hs256` /
`OAuth2Verifier`), so the same audited token machinery that guards tool calls guards
the dashboard. Tokens carry the subject, tenant, and roles; every protected route
resolves them into a scoped identity.

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
