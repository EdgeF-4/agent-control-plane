# TypeScript SDK

A single-file, dependency-free client for the Agent Control Plane — `fetch` and
nothing else. It mirrors the [Python quickstart SDK](../sdk/): open a run, report
what it spent, record the tool calls it made, and close it out, authenticating
with a per-project ingest key so an agent never touches an operator login.

Works anywhere `fetch` is global: Node 18+ and modern browsers.

## Use it

Copy `control-plane.ts` into your project (or `npm run build` and import from
`dist/`). Mint a key in the dashboard under **Admin → Projects → Keys**.

```ts
import { ControlPlane, withRun } from "./control-plane.js";

const cp = new ControlPlane("https://control-plane.internal", process.env.ACP_API_KEY!);

await withRun(cp, { agentName: "nightly-report", label: "weekly digest" }, async (run) => {
  const outcome = await run.reportUsage({
    model: "assistant-large",
    inputTokens: 1200,
    outputTokens: 350,
    costUsd: 0.12,
  });
  if (!outcome.allowed) console.log("budget kill switch tripped");

  const decision = await run.recordToolCall({
    tool: "search",
    server: "web",
    arguments: { q: "market size" },
  });
  console.log(`search -> ${decision.decision}`);
});
// the run closes automatically
```

Prefer to manage the lifecycle yourself? Skip `withRun`:

```ts
const run = await cp.openRun({ agentName: "pilot" });
await run.reportUsage({ model: "assistant", inputTokens: 900, outputTokens: 300, costUsd: 0.08 });
await run.close({ status: "completed" });
```

Every call throws `ControlPlaneError` (with `.status` and `.detail`) if the plane
refuses it — a revoked key, a budget kill switch, or a blocked eval gate.

## Run the quickstart

```bash
npm install                 # dev-only: TypeScript, for building
ACP_URL=http://localhost:8800 ACP_API_KEY=acp_... npm run quickstart
```

`npm run typecheck` type-checks without emitting; `npm run build` emits `dist/`.

## The curl equivalent

```bash
RUN=$(curl -s $ACP_URL/api/v1/runs -H "authorization: Bearer $ACP_API_KEY" \
  -H 'content-type: application/json' -d '{"agent_name":"pilot"}' | jq -r .id)

curl -s $ACP_URL/api/v1/runs/$RUN/usage -H "authorization: Bearer $ACP_API_KEY" \
  -H 'content-type: application/json' \
  -d '{"model":"assistant","input_tokens":900,"output_tokens":300,"cost_usd":0.08}'

curl -s $ACP_URL/api/v1/runs/$RUN/complete -H "authorization: Bearer $ACP_API_KEY" \
  -H 'content-type: application/json' -d '{"status":"completed"}'
```
