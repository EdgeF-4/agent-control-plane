import { useCallback, useEffect, useRef, useState } from "react";
import { api, AuditEntry, Budget, BudgetAlert, Decision, EvalRun, getToken, Overview, Run, User } from "../lib/api";
import { LiveMessage, useLive } from "../lib/ws";
import { usd } from "../lib/format";
import { AuditPanel, BudgetPanel, DecisionsPanel, Kpis, RunsPanel } from "./panels";
import RunDrawer from "./RunDrawer";
import SiemPanel from "./SiemPanel";
import EvalsPanel from "./EvalsPanel";
import AlertToasts, { ToastAlert } from "./AlertToasts";
import AdminPage from "./AdminPage";

export default function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [evals, setEvals] = useState<EvalRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<ToastAlert[]>([]);
  const [view, setView] = useState<"cockpit" | "admin">("cockpit");

  const refreshTimer = useRef<number | null>(null);
  const alertId = useRef(0);

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
      } else if (m.type === "budget.alert") {
        const id = ++alertId.current;
        setAlerts((prev) => [{ ...(m as unknown as BudgetAlert), id }, ...prev].slice(0, 6));
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

  const isAdmin = user.role === "admin";

  return (
    <>
      <TopBar
        user={user}
        onLogout={onLogout}
        spend={overview?.total_spend_usd ?? 0}
        view={view}
        onToggleView={() => setView((v) => (v === "admin" ? "cockpit" : "admin"))}
        isAdmin={isAdmin}
      />
      {view === "admin" ? (
        <AdminPage user={user} />
      ) : !overview ? (
        <div className="wrap"><div className="empty">Loading control plane…</div></div>
      ) : (
        <div className="wrap">
          <div style={{ marginBottom: 16 }}>
            <Kpis o={{ ...overview, budgets, recent_denied_decisions: overview.recent_denied_decisions }} />
          </div>
          <div className="grid">
            <div className="col-8"><RunsPanel runs={runs} onSelect={setSelected} /></div>
            <div className="col-4"><BudgetPanel budgets={budgets} /></div>
            <div className="col-8"><AuditPanel entries={audit} /></div>
            <div className="col-4"><EvalsPanel evals={evals} isAdmin={isAdmin} onChanged={loadAll} /></div>
            <div className="col-6"><DecisionsPanel decisions={decisions} /></div>
            <div className="col-6"><SiemPanel isAdmin={isAdmin} /></div>
          </div>
        </div>
      )}
      {selected && <RunDrawer runId={selected} onClose={() => setSelected(null)} />}
      <AlertToasts alerts={alerts} onDismiss={(id) => setAlerts((p) => p.filter((a) => a.id !== id))} />
    </>
  );
}

function TopBar({ user, onLogout, spend, view, onToggleView, isAdmin }: {
  user: User; onLogout: () => void; spend: number;
  view: "cockpit" | "admin"; onToggleView: () => void; isAdmin: boolean;
}) {
  return (
    <div className="topbar">
      <div className="brand">
        <span className="glyph">⬢</span>
        Agent Control Plane <small>self-hosted</small>
      </div>
      <div className="spacer" />
      {isAdmin && (
        <button className="btn ghost" onClick={onToggleView} style={{ marginRight: 8 }}>
          {view === "admin" ? "← Cockpit" : "Admin"}
        </button>
      )}
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
