import { useCallback, useEffect, useRef, useState } from "react";
import { api, AuditEntry, Budget, Decision, EvalRun, getToken, Overview, Run, User } from "../lib/api";
import { LiveMessage, useLive } from "../lib/ws";
import { usd } from "../lib/format";
import { AuditPanel, BudgetPanel, DecisionsPanel, EvalPanel, Kpis, RunsPanel } from "./panels";
import RunDrawer from "./RunDrawer";
import SiemPanel from "./SiemPanel";

export default function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [evals, setEvals] = useState<EvalRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  const refreshTimer = useRef<number | null>(null);

  const loadAll = useCallback(async () => {
    const [o, a, e] = await Promise.all([api.overview(), api.audit(), api.evals()]);
    setOverview(o);
    setRuns(o.recent_runs);
    setBudgets(o.budgets);
    setDecisions(o.recent_decisions);
    setAudit(a);
    setEvals(e);
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // Debounced authoritative refresh so KPI counts and audit integrity stay exact.
  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) window.clearTimeout(refreshTimer.current);
    refreshTimer.current = window.setTimeout(() => {
      loadAll();
    }, 500);
  }, [loadAll]);

  const onMessage = useCallback(
    (m: LiveMessage) => {
      if (m.type.startsWith("run.")) {
        const r = m.run as Run & { total_cost_usd?: number };
        setRuns((prev) => {
          const next = prev.filter((x) => x.id !== r.id);
          return [r, ...next].slice(0, 12);
        });
        scheduleRefresh();
      } else if (m.type === "budget.updated") {
        setBudgets(m.budgets as Budget[]);
      } else if (m.type === "policy.decision") {
        setDecisions((prev) => [
          { tool: m.tool as string, server: "", decision: m.decision as string,
            reason: m.reason as string, ts: new Date().toISOString(),
            run_id: m.run_id as string },
          ...prev,
        ].slice(0, 12));
        scheduleRefresh();
      }
    },
    [scheduleRefresh]
  );

  useLive(getToken() ?? "", onMessage);

  if (!overview) {
    return (
      <>
        <TopBar user={user} onLogout={onLogout} spend={0} />
        <div className="wrap"><div className="empty">Loading control plane…</div></div>
      </>
    );
  }

  return (
    <>
      <TopBar user={user} onLogout={onLogout} spend={overview.total_spend_usd} />
      <div className="wrap">
        <div style={{ marginBottom: 16 }}>
          <Kpis o={{ ...overview, budgets, recent_denied_decisions: overview.recent_denied_decisions }} />
        </div>
        <div className="grid">
          <div className="col-8"><RunsPanel runs={runs} onSelect={setSelected} /></div>
          <div className="col-4"><BudgetPanel budgets={budgets} /></div>
          <div className="col-8"><AuditPanel entries={audit} /></div>
          <div className="col-4"><EvalPanel evals={evals} /></div>
          <div className="col-6"><DecisionsPanel decisions={decisions} /></div>
          <div className="col-6"><SiemPanel isAdmin={user.role === "admin"} /></div>
        </div>
      </div>
      {selected && <RunDrawer runId={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function TopBar({ user, onLogout, spend }: { user: User; onLogout: () => void; spend: number }) {
  return (
    <div className="topbar">
      <div className="brand">
        <span className="glyph">⬢</span>
        Agent Control Plane <small>self-hosted</small>
      </div>
      <div className="spacer" />
      <div className="tenant" style={{ marginRight: 4 }}>
        spend <b style={{ color: "var(--accent)" }}>{usd(spend)}</b>
      </div>
      <div className="tenant">
        <b>{user.email}</b> · <b>{user.role}</b>
      </div>
      <button className="btn ghost" onClick={onLogout}>Sign out</button>
    </div>
  );
}
