# Agent Control Plane

A self-hosted control plane for governing automated workloads across projects,
teams, and model providers.

I built this because observability alone was not enough. Once automation can
spend money, call tools, or touch production data, I want one place that can
answer four questions: what is running, what did it cost, was it allowed, and
can I verify the record afterward?

## What it does

- Ingests runs through small Python and TypeScript clients or OAuth2 JWTs.
- Enforces project budgets with integer micro-dollar accounting and a latching
  kill switch.
- Records tool policy decisions and a SHA-256 hash chain for each run.
- Anchors run heads into an append-only Merkle log, with optional S3-compatible
  storage.
- Runs regression suites and can block new work when a pinned baseline regresses.
- Streams live status and budget alerts to a React dashboard.
- Keeps tenants, projects, users, API keys, and data isolated through RBAC.
- Forwards redacted audit events to local files or configured SIEM endpoints.

## Quick start

The full stack needs Docker with Compose and the five engine source trees used
by this repository. Put those trees under one directory and set `ENGINES_SRC`
to it if they are not next to this checkout.

```bash
cp config.example.json config.json
chmod 600 config.json
# Replace every change-me value in config.json before starting.
export ENGINES_SRC=/path/to/engine-checkouts
make up
```

Then open:

- Dashboard: <http://localhost:8801>
- API documentation: <http://localhost:8800/docs>
- Health check: <http://localhost:8800/healthz>

For local development instead of containers:

```bash
export ENGINES_SRC=/path/to/engine-checkouts
make dev
cp config.example.json config.local.json
# Point database.url and engine data paths at writable local locations.
. .venv/bin/activate
export ACP_CONFIG=$PWD/config.local.json
python -m app.cli seed
python -m app.cli serve
```

`config.json` and `config.local.json` are ignored. The application reads secrets
at runtime and does not need a committed environment file.

## Architecture

```text
 Operators                       Workloads and SDKs
 dashboard + JWT                 API key or OAuth2 JWT
       |                                  |
       +----------------+-----------------+
                        |
                 HTTPS + WebSocket
                        |
              +---------v----------+       +-------------+
              | Control Plane API  |<----->| Redis bus   |
              | auth, RBAC, ingest |       | optional    |
              +--+---+---+---+---+-+       +-------------+
                 |   |   |   |   |
       +---------+   |   |   |   +----------+
       |             |   |   |              |
 +-----v----+ +------v+ +v-------+ +--------v-+ +------v-----+
 | budgets | | run    | | policy | | SIEM and | | evals and  |
 | and cost| | record | | checks | | anchors  | | gate       |
 +-----+---+ +----+---+ +----+---+ +-----+----+ +------+-----+
       |          |          |           |             |
       +----------+----------+-----------+-------------+
                              |
                    PostgreSQL projection
```

The engines remain authoritative for their integrity-sensitive records.
PostgreSQL provides the joined operational view used by the API and dashboard.
See [docs/architecture.md](docs/architecture.md) for data flow and trust
boundaries.

## A typical run

1. A project authenticates with its API key or an accepted JWT.
2. The API checks the project gate and records the run.
3. Usage events update the budget ledger using integer micro-dollars.
4. Tool calls receive an allow or deny decision under deny-first policy rules.
5. Each event extends the run's hash chain and can be forwarded to a SIEM sink.
6. The dashboard receives live updates, while later verification recomputes the
   chain from the durable record.

Dependency-free examples live in [examples/sdk/](examples/sdk/) and
[examples/sdk-ts/](examples/sdk-ts/).

## Honest limitations

- This repository is an integration layer, not a standalone monorepo. Local and
  image builds require the five engine source trees named by
  `scripts/vendor-engines.sh`.
- The audit chain is tamper-evident, not tamper-proof. Off-box anchoring improves
  the evidence, but storage policy and access control still matter.
- Operator login is JWT-based. There is no turnkey enterprise SSO flow.
- Redis is optional; without it, live delivery is limited to one API process.
- Configured JWKS, HTTP evaluation, SIEM, and S3-compatible endpoints can create
  outbound traffic. There is no telemetry or hosted control service.
- The supplied bootstrap credentials are examples and must be replaced before
  any non-local deployment.
- This is governance infrastructure, not a compliance certification or a
  substitute for a security review.

The deployment and data-residency notes are in
[docs/self-hosting-compliance.md](docs/self-hosting-compliance.md).

## Development

```bash
make test
cd frontend && npm run build
```

The backend suite covers authentication, tenant boundaries, budgets, policy
decisions, migrations, hash-chain verification, anchoring, SIEM replay, OAuth2
ingest, live delivery, and evaluation gates. The frontend build runs TypeScript
checking before Vite produces the dashboard bundle.

## Repository map

```text
backend/     FastAPI application, migrations, engine adapters, and tests
frontend/    React and TypeScript dashboard
deploy/      Container images and nginx configuration
examples/    Minimal Python and TypeScript clients
docs/        Architecture and self-hosting notes
scripts/     Local setup, engine staging, stack startup, and release audit
```

## License

MIT. See [LICENSE](LICENSE).
