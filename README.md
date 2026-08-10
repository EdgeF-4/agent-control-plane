# Agent Control Plane

[![License](https://img.shields.io/github/license/EdgeF-4/agent-control-plane)](LICENSE)

A self-hosted governance and audit console for teams running automated agents.
It combines run visibility, enforced budgets, policy decisions, and a
tamper-evident event record without requiring a hosted control-plane service.

> **Visibility recommendation:** keep this repository private. It is an
> integration source snapshot, not a standalone distribution. Both installation
> paths require five adjacent engine source checkouts, and two dependencies are
> not publicly retrievable as of 10 August 2026. Reachable Git history also
> contains personal author metadata, machine-attribution text, and private
> release identifiers. A stranger cannot install this product from this
> repository alone, and a normal commit cannot repair reachable history. See
> [Required engine sources](#required-engine-sources) and
> [Limits and who should not use it](#limits-and-who-should-not-use-it).

![Dashboard](docs/dashboard.png)

## Who it is for

This is aimed at platform and compliance teams that must answer four questions:
what is running, what it costs, whether each action was allowed, and whether the
event record changed later. Runtime data stays on infrastructure controlled by
the operator. The default runtime sink is a local file; configured remote log
sinks are the only intentional runtime egress.

## Limits and who should not use it

- **Not a public standalone package.** Five sibling engine source trees are
  required, and two have no verified public retrieval route. Do not adopt this
  repository unless you already hold all five exact source trees.
- **Not a workflow orchestrator.** It records and governs work reported through
  its API. It does not schedule arbitrary jobs, run containers, or recover an
  interrupted business workflow.
- **Not a security boundary.** Policy enforcement covers callers that use this
  control plane. It does not inspect or block traffic that bypasses it.
- **Not high-availability by default.** The supplied Compose stack is a
  single-host reference deployment. Multi-host failover, backup automation, and
  operator TLS termination are left to the deployer.
- **Not non-repudiation or certification.** The hash chain detects partial
  changes, but a privileged operator can replace and recompute a whole record.
  The compliance document describes how to add an external anchor.

Teams that need a five-minute public install, managed hosting, arbitrary job
orchestration, or a certified compliance product should not use this snapshot.

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

### Design decision and trade

The control plane keeps each engine as the source of truth and uses PostgreSQL
as a query projection instead of folding all five domains into one database.
That preserves the engines' domain guarantees and keeps integrity checks tied
to their authoritative records. The cost is operational coupling: deployment,
testing, upgrades, and incident recovery must coordinate six source packages
and several stores. That trade is acceptable for an owner-controlled integrated
product, but it is the reason this repository is not an honest standalone
public distribution today.

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

### Commands behind the capability claims

Run `make dev` once with all five engine sources, then run these commands from
the repository root. Each command names the tests that demonstrate the matching
README claim. These commands cannot be independently verified from the public
snapshot until every engine source has a public or packaged route.

```bash
# Live run frames, cost timeline, and budget alerts
.venv/bin/python -m pytest -q backend/tests/test_cost_feed.py \
  backend/tests/test_e2e_slice.py::test_live_websocket_receives_run_frames

# Budget latch and policy allow/deny decisions
.venv/bin/python -m pytest -q \
  backend/tests/test_e2e_slice.py::test_budget_cap_kills_the_run \
  backend/tests/test_units.py::test_policy_allows_and_denies

# Tamper detection and chain continuation after restart
.venv/bin/python -m pytest -q \
  backend/tests/test_e2e_slice.py::test_audit_trail_is_tamper_evident \
  backend/tests/test_e2e_slice.py::test_run_survives_backend_restart_midflight

# Project-scoped and revoked API keys
.venv/bin/python -m pytest -q backend/tests/test_api_keys.py

# Sink health, dead letters, and replay
.venv/bin/python -m pytest -q backend/tests/test_siem.py

# Eval scheduling, regression comparison, and the run gate
.venv/bin/python -m pytest -q backend/tests/test_evals_phase2.py

# Tenant, user, project, and role boundaries
.venv/bin/python -m pytest -q backend/tests/test_admin.py

# Migration completeness, repeatability, and model drift
.venv/bin/python -m pytest -q backend/tests/test_migrations.py
```

## Troubleshooting

### `Missing required engine source directories`

The value of `ENGINES_SRC` does not contain all five directories listed under
[Required engine sources](#required-engine-sources), or one lacks
`pyproject.toml`. Point `ENGINES_SRC` at their common parent and run `make dev`
again. Public users cannot fix a source that has no retrieval route. Do not
create placeholder packages.

### `No config.json`

The Docker path was started before local configuration existed. Run
`cp config.example.json config.json`, replace every sample secret, run
`chmod 600 config.json`, and then run `make up` again.

### `Missing .venv`

The test target was called before development setup. Confirm all five engine
directories are present, set `ENGINES_SRC`, run `make dev`, then run
`make test` again.

### A local port is already in use

Choose unused loopback ports and use the same values for startup and smoke:

```bash
export ACP_API_PORT=18800 ACP_WEB_PORT=18801
make up
make smoke
```

### `make smoke` cannot reach a service

Inspect startup state with `docker compose ps` and backend diagnostics with
`docker compose logs backend`. Fix the first reported startup error, run
`make up` again, and rerun `make smoke`. Do not treat a passing dashboard check
as proof that authenticated API capabilities work.

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
