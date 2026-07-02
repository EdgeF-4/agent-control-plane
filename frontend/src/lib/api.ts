// Typed client for the control plane API. The token is kept in localStorage;
// every request is scoped to the caller's tenant on the server.

const TOKEN_KEY = "acp_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...(init.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`/api/v1${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* keep statusText */ }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// --- types -------------------------------------------------------------- //
export interface User { id: string; email: string; name: string; role: string; tenant_id: string; }
export interface Budget {
  budget_id: string; name: string; scope_id: string; limit_micro: number;
  spent_micro: number; period: string; state: string; fraction: number;
}
export interface Run {
  id: string; external_run_id: string; project_id: string; agent_name: string;
  status: string; started_at: string; ended_at: string | null;
  total_input_tokens: number; total_output_tokens: number; total_cost_micro: number; label: string;
}
export interface Decision {
  tool: string; server: string; decision: string; reason: string; ts: string; run_id?: string | null;
}
export interface RunEvent {
  seq: number; event_type: string; name: string | null; ts: string;
  cost_micro: number | null; tokens: Record<string, number> | null;
  latency_ms: number | null; payload_summary: Record<string, unknown>;
  hash: string; prev_hash: string;
}
export interface AuditEntry {
  run_id: string; external_run_id: string; seq: number; event_type: string;
  name: string | null; ts: string; cost_micro: number | null; hash: string; prev_hash: string;
}
export interface EvalRun {
  id: string; eval_run_id: string; suite_name: string; pass_rate: number;
  mean_score: number; passed: number; failed: number; total: number;
  is_baseline: boolean; has_regressions: boolean | null; finished_at: string | null;
}
export interface Verify { ok: boolean; event_count: number; broken_index: number | null; reason: string | null; }
export interface SiemSink { name: string; type: string; enabled: boolean; }
export interface SiemStats {
  processed: number; batches: number; delivered: number;
  dead_lettered: number; parse_errors: number;
}
export interface SiemPreset { type: string; label: string; description: string; config: Record<string, unknown>; }
export interface SiemStatus {
  sinks: SiemSink[]; stats: SiemStats; dlq_count: number;
  redaction_enabled: boolean; presets: SiemPreset[];
}
export interface DlqEntry {
  sink: string; error: string; attempts: number; failed_at: string; event_count: number;
}
export interface SinkTest { ok: boolean; detail: string; }
export interface TimelinePoint {
  seq: number; ts: string; event_type: string; name: string | null;
  cost_micro: number; cumulative_micro: number; cumulative_tokens: number; elapsed_s: number;
}
export interface RunTimeline {
  run_id: string; points: TimelinePoint[]; total_cost_micro: number;
  total_tokens: number; duration_s: number; burn_rate_usd_per_min: number;
}
export interface BudgetAlert {
  project: string; kind: string; message: string; scope_id: string;
  spent_micro: number; limit_micro: number; fraction: number; ts: string; run_id?: string;
}
export interface Overview {
  tenant: { id: string; slug: string; name: string };
  runs: { total: number; running: number; completed: number; killed: number; error: number };
  total_spend_usd: number; audit_event_count: number; recent_denied_decisions: number;
  budgets: Budget[]; recent_runs: Run[]; recent_decisions: Decision[];
  eval: { suite_name: string; pass_rate: number; passed: number; failed: number;
          total: number; has_regressions: boolean | null; finished_at: string | null } | null;
}

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string; user: User }>("/auth/login", {
      method: "POST", body: JSON.stringify({ email, password }),
    }),
  me: () => request<User>("/auth/me"),
  overview: () => request<Overview>("/overview"),
  runs: () => request<Run[]>("/runs"),
  runEvents: (id: string) => request<RunEvent[]>(`/runs/${id}/events`),
  runDecisions: (id: string) => request<Decision[]>(`/runs/${id}/decisions`),
  runTimeline: (id: string) => request<RunTimeline>(`/runs/${id}/timeline`),
  verifyRun: (id: string) => request<Verify>(`/runs/${id}/verify`),
  audit: () => request<AuditEntry[]>("/audit"),
  budgets: () => request<Budget[]>("/budgets"),
  evals: () => request<EvalRun[]>("/evals"),
  runEval: () => request<unknown>("/evals/run", { method: "POST" }),
  siemStatus: () => request<SiemStatus>("/siem/status"),
  siemDlq: () => request<DlqEntry[]>("/siem/dlq"),
  siemTestSink: (name: string) =>
    request<SinkTest>(`/siem/sinks/${encodeURIComponent(name)}/test`, { method: "POST" }),
  siemReplay: (sink?: string) =>
    request<{ replayed: number; failed: number; skipped: number }>(
      `/siem/dlq/replay${sink ? `?sink=${encodeURIComponent(sink)}` : ""}`,
      { method: "POST" }
    ),
};
