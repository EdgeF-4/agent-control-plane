# Self-hosting & compliance guarantees

This is the write-up I'd want if I were putting this in front of a security team.
It states plainly what the control plane guarantees, how, and — just as
important — where each guarantee stops.

## Data residency: everything stays on your box

The control plane and all five engines run on infrastructure you own. Every store
is local:

| Store | What it holds | Where |
| --- | --- | --- |
| PostgreSQL | the unified projection (tenants, users, projects, runs, events, decisions, api_keys, evals, schedules) | a container/host you run |
| cost ledger | integer micro-dollar spend, budgets, kill switches | local SQLite |
| recorder | the hash-chained run timelines | append-only JSONL files |
| SIEM sink | the forwarded audit stream | a local file by default |
| eval history | suite runs and baselines | local SQLite |

No managed service sees your agent data. There is no telemetry, no license
call-home, no usage beacon.

## Egress: one destination, and you choose it

The only outbound network traffic the control plane makes is to the **SIEM sink
you configure**. Out of the box that sink is a local file, so a default install
makes **zero** outbound calls. Point it at Splunk, Elasticsearch, Datadog, or a
webhook and it will talk to exactly that endpoint and nothing else — with retry
and a dead-letter queue so a sink outage never drops an audit event or blocks a
run. This makes an air-gapped deployment a first-class configuration, not a
workaround: the reliability suite is offline, the engines are standard-library or
local-store, and nothing needs a public index at runtime.

## Tamper-evidence: what the hash chain proves, and what it doesn't

Every event in a run is sealed with a SHA-256 hash computed over its canonical
JSON, including the previous event's hash. This links a run into a chain, so:

- **Editing** a field in any event changes its hash.
- **Reordering, inserting, or deleting** an event breaks the `prev_hash` linkage.
- Verification (`GET /api/v1/runs/{id}/verify`) walks the chain and reports the
  exact index where it broke.

Because the recorder records in-process, a backend restart mid-run does not break
the chain: the continuation cursor is rehydrated from the durable on-disk record,
so events recorded before and after a restart verify as one chain.

**The limit, stated honestly:** this is tamper-*evident*, not tamper-*proof*.
Anyone who can rewrite the log file can also recompute a fresh, internally
consistent chain. What the chain buys you is that *casual or partial* tampering —
editing one record, dropping an inconvenient event, reordering steps — is
detectable. For non-repudiation, periodically anchor the final hash of a run
somewhere the same operator can't silently rewrite (a WORM bucket, a notary, a
second host). The chain makes that anchor cheap: you only need to pin one hash.

## Secrets

Runtime secrets — the database password, the JWT signing key, sink credentials —
live only in `config.json`, which is `chmod 600` and git-ignored. They are never
written to environment files, baked into an image, or committed. Sinks are
configured as code in that same file, so egress destinations and their
credentials are reviewable and are never mutated at runtime by the dashboard.

Agent credentials never sit in the clear: a per-project API key is shown once at
creation and only its SHA-256 digest is stored. Revoking a key removes it from
the authenticator immediately.

## Access control & tenant isolation

- **Roles.** `member` (read the cockpit), `admin` (manage users, projects,
  budgets, gates, keys within their tenant), and `superadmin` (manage tenants).
- **Tenant isolation is enforced on every row.** Each record carries a
  `tenant_id`, and every route resolves the caller's token to a tenant and scopes
  its queries to it. Engine state is partitioned the same way — the cost engine's
  scope id is namespaced `tenant/project`, so two tenants' identically-named
  projects never share a ledger.
- **Agent keys are project-scoped.** A key can only open and report runs for the
  one project it belongs to; pointing it elsewhere is refused.

## Cost governance

Budgets are integer micro-dollars (no floating-point drift). A hard cap denies
further spend the moment it's crossed and — for a latching budget — engages a
kill switch that stays engaged until an operator with the right role releases it.
Threshold, cap, and kill events are both recorded and pushed to the live feed, so
a runaway agent is visible and stopped, not just logged after the fact.

## Reliability gating

A project can pin an *eval gate* to a suite. While that suite's latest run is
regressed against its baseline, new runs are refused — so a known-bad change
can't be promoted into production traffic on that project until reliability is
restored.

## Schema & upgrades

The schema is owned by Alembic migrations and brought to `head` on startup, so
upgrades are a reviewable, ordered history rather than implicit table creation. A
test asserts the migrations match the models with zero drift.

## What this is not

- It is **not** a WAF or a network firewall; it governs the agents that report to
  it, not arbitrary traffic.
- The hash chain is evidence, not cryptographic non-repudiation on its own (see
  above) — anchor the final hash off-box when you need that.
- It does **not** phone home or auto-update; you own the upgrade cadence.
