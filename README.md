# Agent Control Plane

[![License](https://img.shields.io/github/license/EdgeF-4/agent-control-plane)](LICENSE)

A self-hosted governance and audit console for teams running automated agents.
It combines run visibility, enforced budgets, policy decisions, and a
tamper-evident event record without requiring a hosted control-plane service.

> **Release status:** this repository is an integration source snapshot, not a
> standalone distribution. Both installation paths require five adjacent
> engine source checkouts, and two of those dependencies are not publicly
> retrievable as of 10 August 2026. A stranger cannot install this product from
> this repository alone. See [Required engine sources](#required-engine-sources).

![Dashboard](docs/dashboard.png)

## Who it is for

This is aimed at platform and compliance teams that must answer four questions:
what is running, what it costs, whether each action was allowed, and whether the
event record changed later. Runtime data stays on infrastructure controlled by
the operator. The default runtime sink is a local file; configured remote log
sinks are the only intentional runtime egress.

## What it does

- **Live observability:** every agent run streams into a dark cockpit
  dashboard over a WebSocket: status, tokens, cost, a per-run cost timeline with
  a burn-rate curve, and budget alerts that pop the moment a cap is crossed.
- **Cost control with teeth:** per-project budgets in integer micro-dollars
  (no float drift). Cross a hard cap and the kill switch latches: further work
  is denied until someone with the right role releases it.
- **Policy decisions:** every tool call is checked against deny-by-precedence
  rules and recorded as an allow/deny decision with a reason.
- **A tamper-evident audit trail:** every event in a run is sealed into a
  SHA-256 hash chain. Reorder, edit, insert, or delete a single event and
  verification reports exactly where the chain broke. An operator who can
  replace the whole record can recompute the chain, so this is not
  non-repudiation. The chain survives a backend restart mid-run: it is
  rehydrated from the durable record, so the hashes stay continuous.
- **Agents authenticate themselves:** each project mints its own API keys, so
  the agents and SDKs that report runs never touch an operator login. Only the
  key's hash is stored; the plaintext is shown once.
- **Audit forwarding you can watch:** see every configured SIEM sink, test its
  reachability, watch delivery stats, and replay anything that dead-lettered,
  right from the dashboard. Copy-paste presets for Splunk, Elasticsearch,
  Datadog, a webhook, or a local file.
- **Reliability with a gate:** run a regression suite against a pinned
  baseline, on a schedule, and read the case-by-case diff. Put an *eval gate* on
  a project and new runs are refused while that suite is regressed. The installed
  engine suite runs offline after its source package is installed.
- **Admin without leaving the cockpit:** create tenants, users, and projects,
  set budgets and gates, and manage API keys from an admin view.
- **Real migrations:** the schema is owned by Alembic and brought to head on
  startup, so it evolves cleanly in production instead of relying on first-run
  table creation.

Reporting a run from an agent is a few lines. See the dependency-free
[quickstart SDK](examples/sdk/).

## How it's built

The control plane doesn't reinvent the hard parts. It composes five focused
engines, each of which owns its own authoritative store, and projects a single,
multi-tenant, queryable view on top for the dashboard and API.

```
     Operators (dashboard, JWT)          Agents & SDKs (per-project API key)
                     │                                   │
                     └───────────────┬───────────────────┘
                  loopback HTTP / JSON + WebSocket
                     TLS at an operator proxy
                       ┌─────────────▼───────────────────┐
                       │       Control Plane API           │  FastAPI, async
                       │  auth · tenancy · api-key ingest · │
                       │  projections · live feed ·         │
                       │  Alembic migrations · eval sched   │
                       └──┬────┬────┬────┬────┬─────────────┘
              ┌───────────┘    │    │    │    └───────────┐
              ▼                ▼    ▼    ▼                ▼
        ┌──────────┐   ┌────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
        │  cost    │   │  recorder  │ │ audit/   │ │ gateway  │ │  eval    │
        │ caps +   │   │ hash-chain │ │ SIEM     │ │ policy + │ │ regress  │
        │ kill sw  │   │ run log    │ │ forward  │ │ auth     │ │ + gate   │
        │ (SQLite) │   │ (JSONL)    │ │ + DLQ    │ │ (rules)  │ │ (SQLite) │
        └──────────┘   └────────────┘ └──────────┘ └──────────┘ └──────────┘
                                     │
                       ┌─────────────▼──────────────┐
                       │   PostgreSQL: unified       │
                       │   projection (tenants, users,│
                       │   projects, runs, events,    │
                       │   decisions, api_keys, evals,│
                       │   eval_schedules)            │
                       └──────────────────────────────┘
```

Each engine guarantees something the projection cannot: integer-exact cost
accounting, a verifiable hash chain, and reliable redacted forwarding. They stay
the source of truth. PostgreSQL is a fast, joinable view the API writes to inside
the same request that calls an engine, and integrity-sensitive views re-verify
against the hash chain on demand. The full design is in
[docs/architecture.md](docs/architecture.md).

**Stack:** Python 3.12 · FastAPI (async) · SQLAlchemy 2.0 · PostgreSQL ·
React + TypeScript + Vite · Docker Compose.

## Self-host & compliance posture

- **No product telemetry is configured.** The default runtime sink is a local
  file. A remote log sink creates intentional egress to its configured endpoint.
- **Isolatable runtime.** After images and dependencies are assembled, the
  default runtime can operate with local stores. The source build itself is not
  standalone or air-gap complete because it needs the five engine sources.
- **One secret source.** `config.json` is ignored and mounted read-only. The
  setup passes database fields to the database container environment at runtime;
  it does not write an environment file or bake those values into an image.
- **Multi-tenant from the first row.** Every record carries a tenant id, and a
  request can only ever touch its own tenant's data and engine scopes.
- **Tamper-evident by construction.** Verification detects partial record
  changes. The linked threat model states why this is not non-repudiation.

The full write-up covers data residency, egress, the tamper-evidence threat
model and its limits, retention, and RBAC. It is in
[docs/self-hosting-compliance.md](docs/self-hosting-compliance.md).

## Required engine sources

The control plane composes these source packages. They are not declared as
public-index dependencies and are not vendored in this repository:

```text
cost-governor
mcp-gateway
mcp-siem-bridge
agent-flight-recorder
agent-eval
```

Place all five directories under one parent and set `ENGINES_SRC` to that
parent. The setup scripts now stop with an exact missing-directory list. Until
all five sources have an owner-approved public or packaged route, the quick
starts below are reproducible only for authorized source holders.

## Quick start (Docker, source holders)

```bash
export ENGINES_SRC=/absolute/path/to/engine-checkouts
cp config.example.json config.json     # then edit secrets
chmod 600 config.json
make up                                  # builds and starts the stack in the background
make smoke                               # 2 checks: API health + dashboard
```

- Dashboard: <http://localhost:8801>
- API + docs: <http://localhost:8800/docs>

Sign in with the `bootstrap` admin from your `config.json`. `make up` reads the
Postgres credentials from `config.json` so they match what the API uses. No
secrets are committed. API and dashboard ports bind to loopback only. If a port
is already occupied, set `ACP_API_PORT` and `ACP_WEB_PORT` before `make up` and
use the same values for `make smoke`.

## Quick start (local, source holders)

```bash
export ENGINES_SRC=/absolute/path/to/engine-checkouts
make dev                                 # venv with the engines + backend (editable)
cp config.local.example.json config.local.json
chmod 600 config.local.json              # then replace the two sample secrets
. .venv/bin/activate
export ACP_CONFIG=$PWD/config.local.json # a SQLite-backed config for local runs
python -m app.cli seed                   # optional: a demonstrable dataset
python -m app.cli serve                  # API + dashboard on :8800
```

## The end-to-end path

A single ingested run exercises every engine. This run blows its budget mid-way:

```bash
TOKEN=$(curl -s localhost:8800/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"admin@acme.test","password":"…"}' | jq -r .access_token)

curl -s localhost:8800/api/v1/runs/ingest -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{
    "project_slug": "alpha",
    "agent_name": "pilot",
    "usage": [
      {"model": "assistant", "input_tokens": 9000, "output_tokens": 4000, "cost_usd": 3.10},
      {"model": "assistant", "input_tokens": 9000, "output_tokens": 4000, "cost_usd": 3.40}
    ]
  }'
```

The cost engine records both charges; the second one crosses the project's \$5
cap, so the decision flips to `deny`, the kill switch latches, and the run is
marked `killed`. Every step is sealed into the hash chain and forwarded to your
log sink. `GET /api/v1/runs/{id}/verify` confirms the chain is intact.

## Testing

```bash
export ENGINES_SRC=/absolute/path/to/engine-checkouts
make dev             # required once for the backend and five engine packages
make test            # backend: auth, multi-tenancy, the e2e slice, tamper-evidence
make build           # type-check and build the dashboard
```

The suite ingests runs, trips a real budget cap, asserts the kill switch and the
allow/deny decisions, tampers with an on-disk record and confirms verification
catches it, keeps the hash chain intact across a simulated restart, authenticates
an agent with a project API key, checks migrations build the schema with no
drift, drives the SIEM DLQ and replay, streams budget alerts over the WebSocket,
runs and gates evals, and exercises the tenant/user/project admin routes.

## Project layout

```
backend/    FastAPI app: config, data model, auth, engine adapters, routers, tests
backend/alembic/  migration history (schema is brought to head on startup)
frontend/   React + TypeScript cockpit dashboard
deploy/     Dockerfiles + nginx config
scripts/    dev-setup, vendor-engines, up, publish
examples/   dependency-free quickstart SDK
docs/       architecture, self-host/compliance guarantees, diagram
```

## License

Apache-2.0. See [LICENSE](LICENSE).
