/**
 * Report a run to the Agent Control Plane in a few lines.
 *
 *   ACP_URL=http://localhost:8800 ACP_API_KEY=acp_... npm run quickstart
 *
 * Mint the API key in the dashboard under Admin -> Projects -> Keys.
 */

import { ControlPlane, ControlPlaneError, withRun } from "./control-plane.js";

const url = process.env.ACP_URL ?? "http://localhost:8800";
const apiKey = process.env.ACP_API_KEY;

if (!apiKey) {
  console.error("Set ACP_API_KEY (mint one under Admin -> Projects -> Keys).");
  process.exit(1);
}

const cp = new ControlPlane(url, apiKey);

try {
  await withRun(cp, { agentName: "nightly-report", label: "weekly digest" }, async (run) => {
    const outcome = await run.reportUsage({
      model: "assistant-large",
      inputTokens: 1200,
      outputTokens: 350,
      costUsd: 0.12,
    });
    console.log(`usage recorded — cost $${outcome.cost_usd.toFixed(4)}, budget ${outcome.state}`);
    if (!outcome.allowed) console.log("  budget kill switch tripped — run was stopped");

    const decision = await run.recordToolCall({
      tool: "search",
      server: "web",
      arguments: { q: "market size 2026" },
    });
    console.log(`tool call 'search' -> ${decision.decision} (${decision.reason})`);
  });
  console.log("done — open the dashboard to see the run end to end");
} catch (error) {
  if (error instanceof ControlPlaneError) {
    console.error(`control plane refused the call: ${error.message}`);
    process.exit(1);
  }
  throw error;
}
