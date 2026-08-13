import { AuditEntry, Budget, Decision, Overview, Run } from "../lib/api";
import { ago, microToUsd, pct, shortHash, usd } from "../lib/format";
import { DecisionBadge, StatusBadge } from "./StatusBadge";

export function Kpis({ o }: { o: Overview }) {
  return (
    <div className="kpis">
      <div className="kpi">
        <div className="label">Agent runs</div>
        <div className="value">{o.runs.total}</div>
        <div className="meta">
          {o.runs.running} running · {o.runs.completed} completed
          {o.runs.killed > 0 && <span style={{ color: "var(--red)" }}> · {o.runs.killed} killed</span>}
        </div>
      </div>
      <div className="kpi">
        <div className="label">Spend this period</div>
        <div className="value accent">{usd(o.total_spend_usd)}</div>
        <div className="meta">{o.budgets.length} budget{o.budgets.length === 1 ? "" : "s"} enforced</div>
      </div>
      <div className="kpi">
        <div className="label">Audit events</div>
        <div className="value">{o.audit_event_count}</div>
        <div className="meta">hash-chained &amp; verifiable</div>
      </div>
      <div className="kpi">
        <div className="label">Denied decisions</div>
        <div className={`value ${o.recent_denied_decisions ? "red" : ""}`}>{o.recent_denied_decisions}</div>
        <div className="meta">policy &amp; budget enforcement</div>
      </div>
    </div>
  );
}

export function BudgetPanel({ budgets }: { budgets: Budget[] }) {
  return (
    <div className="panel">
      <header>
        <h2>Cost vs. budget</h2>
        <span className="sub">live from the cost engine</span>
      </header>
      <div className="body flush">
        {budgets.length === 0 && <div className="empty">No budgets yet.</div>}
        {budgets.map((b) => {
          const frac = b.limit_micro ? b.spent_micro / b.limit_micro : 0;
          const cls = frac >= 1 ? "over" : frac >= 0.8 ? "warn" : "";
          const project = b.scope_id.split("/").pop();
          return (
            <div className="budget" key={b.budget_id}>
              <div className="row">
                <span className="name">{project}</span>
                <span className="spend">
                  {microToUsd(b.spent_micro)} / {microToUsd(b.limit_micro)} · {pct(frac)}
                </span>
              </div>
              <div className={`bar ${cls}`}>
                <span style={{ width: `${Math.min(100, frac * 100)}%` }} />
              </div>
              {b.state === "deny" && (
                <div className="tag">⛔ cap reached. Next: ask an administrator to release or raise this budget before retrying work.</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function RunsPanel({ runs, onSelect }: { runs: Run[]; onSelect: (id: string) => void }) {
  return (
    <div className="panel">
      <header>
        <h2>Agent runs</h2>
        <span className="right" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span className="live-dot" />
          <span className="sub">live</span>
        </span>
      </header>
      <div className="body flush">
        <table>
          <thead>
            <tr>
              <th>Status</th>
              <th>Run</th>
              <th>Agent</th>
              <th style={{ textAlign: "right" }}>Tokens</th>
              <th style={{ textAlign: "right" }}>Cost</th>
              <th style={{ textAlign: "right" }}>Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr><td colSpan={6}><div className="empty">No runs yet.</div></td></tr>
            )}
            {runs.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => onSelect(r.id)}>
                <td><StatusBadge status={r.status} /></td>
                <td>
                  <div style={{ fontWeight: 600 }}>{r.label || r.external_run_id.split("__").pop()}</div>
                  <div className="mono">{r.external_run_id}</div>
                </td>
                <td>{r.agent_name || "—"}</td>
                <td className="num">{(r.total_input_tokens + r.total_output_tokens).toLocaleString()}</td>
                <td className="num">{microToUsd(r.total_cost_micro)}</td>
                <td className="num" style={{ color: "var(--muted)" }}>{ago(r.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function DecisionsPanel({ decisions }: { decisions: Decision[] }) {
  return (
    <div className="panel">
      <header><h2>Policy decisions</h2><span className="sub">allow / deny</span></header>
      <div className="body flush">
        <table>
          <tbody>
            {decisions.length === 0 && (
              <tr><td colSpan={3}><div className="empty">No decisions yet.</div></td></tr>
            )}
            {decisions.map((d, i) => (
              <tr key={i}>
                <td style={{ width: 90 }}><DecisionBadge decision={d.decision} /></td>
                <td>
                  <div className="mono" style={{ color: "var(--text)" }}>
                    {d.server ? `${d.server}/` : ""}{d.tool}
                  </div>
                  <div className="sub" style={{ color: "var(--muted-2)", fontSize: 12 }}>{d.reason}</div>
                </td>
                <td className="num" style={{ color: "var(--muted)", width: 80 }}>{ago(d.ts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function AuditPanel({ entries }: { entries: AuditEntry[] }) {
  return (
    <div className="panel">
      <header>
        <h2>Audit trail</h2>
        <span className="sub">tamper-evident · SHA-256 chained</span>
      </header>
      <div className="body flush">
        <table>
          <thead>
            <tr><th>Event</th><th>Run</th><th>Hash</th><th style={{ textAlign: "right" }}>When</th></tr>
          </thead>
          <tbody>
            {entries.length === 0 && (
              <tr><td colSpan={4}><div className="empty">No audit events yet.</div></td></tr>
            )}
            {entries.slice(0, 12).map((e, i) => (
              <tr key={i}>
                <td>
                  <span style={{ fontWeight: 600 }}>{e.event_type}</span>
                  {e.name && <span className="mono" style={{ marginLeft: 6 }}>{e.name}</span>}
                </td>
                <td className="mono">{e.external_run_id.split("__").pop()}</td>
                <td className="mono" title={e.hash}>{shortHash(e.hash)}</td>
                <td className="num" style={{ color: "var(--muted)" }}>{ago(e.ts)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
