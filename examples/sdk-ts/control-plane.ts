/**
 * A tiny, dependency-free client for the Agent Control Plane.
 *
 * `fetch` and nothing else — copy this one file into your agent (Node 18+ or a
 * browser) and you can open a run, report what it spent, record the tool calls
 * it made, and close it out. It authenticates with a per-project ingest key
 * (create one in the dashboard under Admin -> Projects -> Keys), so an agent
 * never needs an operator's login.
 *
 *   import { ControlPlane, withRun } from "./control-plane.js";
 *
 *   const cp = new ControlPlane("https://control-plane.internal", "acp_...");
 *   await withRun(cp, { agentName: "nightly-report", label: "weekly digest" }, async (run) => {
 *     await run.reportUsage({ model: "assistant-large", inputTokens: 1200, outputTokens: 350, costUsd: 0.12 });
 *     await run.recordToolCall({ tool: "search", server: "web", arguments: { q: "market size" } });
 *   });
 *   // the run closes automatically; open the dashboard to see it end to end
 */

export class ControlPlaneError extends Error {
  readonly status: number;
  readonly detail: string;
  constructor(status: number, detail: string) {
    super(`HTTP ${status}: ${detail}`);
    this.name = "ControlPlaneError";
    this.status = status;
    this.detail = detail;
  }
}

export interface UsageOutcome {
  cost_usd: number;
  allowed: boolean;
  state: string;
  alerts: string[];
  violations: unknown[];
  run_status: string;
}

export interface Decision {
  server: string;
  tool: string;
  decision: "allow" | "deny";
  reason: string;
}

export interface OpenRunOptions {
  agentName?: string;
  label?: string;
  meta?: Record<string, unknown>;
  /** Only needed for an operator token; a project key already implies the project. */
  projectSlug?: string;
}

export interface UsageReport {
  model: string;
  inputTokens?: number;
  outputTokens?: number;
  /** Give an explicit cost, or omit and let the engine price it. */
  costUsd?: number | null;
  provider?: string;
  name?: string | null;
  latencyMs?: number | null;
}

export interface ToolCall {
  tool: string;
  server?: string;
  arguments?: Record<string, unknown>;
}

export class ControlPlane {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly timeoutMs: number;

  constructor(baseUrl: string, apiKey: string, options: { timeoutMs?: number } = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.apiKey = apiKey;
    this.timeoutMs = options.timeoutMs ?? 10_000;
  }

  /** POST JSON and return the parsed body, raising ControlPlaneError on !2xx. */
  async postJson(path: string, body: unknown): Promise<any> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await fetch(this.baseUrl + path, {
        method: "POST",
        // Bearer and X-API-Key are both accepted; Bearer keeps it simple.
        headers: { "content-type": "application/json", authorization: `Bearer ${this.apiKey}` },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      const text = await response.text();
      if (!response.ok) throw new ControlPlaneError(response.status, text);
      return text ? JSON.parse(text) : {};
    } finally {
      clearTimeout(timer);
    }
  }

  /** Open a run. The API key already implies the project. */
  async openRun(options: OpenRunOptions = {}): Promise<Run> {
    const data = await this.postJson("/api/v1/runs", {
      project_slug: options.projectSlug ?? "",
      agent_name: options.agentName ?? "",
      label: options.label ?? "",
      meta: options.meta ?? {},
    });
    return new Run(this, data.id as string);
  }
}

export class Run {
  status = "running";
  constructor(private readonly client: ControlPlane, readonly id: string) {}

  /**
   * Report one model call's usage. The control plane prices it (or takes your
   * costUsd), enforces the budget, and seals it into the audit log. The returned
   * outcome tells you if the budget kill switch just tripped.
   */
  async reportUsage(usage: UsageReport): Promise<UsageOutcome> {
    const outcome = (await this.client.postJson(`/api/v1/runs/${this.id}/usage`, {
      provider: usage.provider ?? "local",
      model: usage.model,
      input_tokens: usage.inputTokens ?? 0,
      output_tokens: usage.outputTokens ?? 0,
      cost_usd: usage.costUsd ?? null,
      name: usage.name ?? null,
      latency_ms: usage.latencyMs ?? null,
    })) as UsageOutcome;
    this.status = outcome.run_status ?? this.status;
    return outcome;
  }

  /** Record a tool call and get the allow/deny policy decision back. */
  async recordToolCall(call: ToolCall): Promise<Decision> {
    return (await this.client.postJson(`/api/v1/runs/${this.id}/tool-call`, {
      server: call.server ?? "tools",
      tool: call.tool,
      arguments: call.arguments ?? {},
    })) as Decision;
  }

  /** Close the run (completed or error) and seal the final event. */
  async close(options: { status?: "completed" | "error"; output?: Record<string, unknown> | null } = {}): Promise<any> {
    const result = await this.client.postJson(`/api/v1/runs/${this.id}/complete`, {
      status: options.status ?? "completed",
      output: options.output ?? null,
    });
    this.status = (result.status as string) ?? options.status ?? "completed";
    return result;
  }
}

/**
 * Run a function with an open run, closing it automatically — the equivalent of
 * the Python client's context manager. Closes as `error` if the body throws (and
 * only if a budget kill switch hasn't already closed the run).
 */
export async function withRun(
  cp: ControlPlane,
  options: OpenRunOptions,
  fn: (run: Run) => Promise<void>,
): Promise<Run> {
  const run = await cp.openRun(options);
  try {
    await fn(run);
    if (run.status === "running") await run.close({ status: "completed" });
    return run;
  } catch (error) {
    if (run.status === "running") await run.close({ status: "error" });
    throw error;
  }
}
