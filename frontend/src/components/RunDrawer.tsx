import { useEffect, useState } from "react";
import { api, Decision, RunEvent, Verify } from "../lib/api";
import { microToUsd, shortHash } from "../lib/format";
import { DecisionBadge } from "./StatusBadge";

const GLYPH: Record<string, string> = {
  run_start: "▶", model_response: "✦", tool_call: "⚙", tool_result: "↩",
  error: "⛔", run_end: "■", state: "⇄", log: "·",
};

export default function RunDrawer({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [verify, setVerify] = useState<Verify | null>(null);
  const [verifying, setVerifying] = useState(false);

  useEffect(() => {
    let live = true;
    Promise.all([api.runEvents(runId), api.runDecisions(runId)]).then(([ev, dec]) => {
      if (!live) return;
      setEvents(ev);
      setDecisions(dec);
    });
    return () => { live = false; };
  }, [runId]);

  async function runVerify() {
    setVerifying(true);
    try {
      setVerify(await api.verifyRun(runId));
    } finally {
      setVerifying(false);
    }
  }

  const totalCost = events.reduce((s, e) => s + (e.cost_micro ?? 0), 0);
  const totalTokens = events.reduce((s, e) => s + (e.tokens?.total ?? 0), 0);

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer">
        <header>
          <h2>Run timeline</h2>
          <span className="right" style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            {verify && (
              <span className={`pill-int ${verify.ok ? "ok" : "bad"}`}>
                {verify.ok ? "✓ chain intact" : `✗ broken @ ${verify.broken_index}`}
              </span>
            )}
            <button className="btn" onClick={runVerify} disabled={verifying}>
              {verifying ? "Verifying…" : "Verify integrity"}
            </button>
            <button className="btn ghost" onClick={onClose}>✕</button>
          </span>
        </header>
        <div className="content">
          <div className="kv">
            <div className="item"><div className="k">Events</div><div className="v">{events.length}</div></div>
            <div className="item"><div className="k">Tokens</div><div className="v">{totalTokens.toLocaleString()}</div></div>
            <div className="item"><div className="k">Cost</div><div className="v">{microToUsd(totalCost)}</div></div>
            <div className="item"><div className="k">Decisions</div><div className="v">{decisions.length}</div></div>
          </div>

          {decisions.length > 0 && (
            <div style={{ marginBottom: 18 }}>
              {decisions.map((d, i) => (
                <div key={i} style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 8 }}>
                  <DecisionBadge decision={d.decision} />
                  <span className="mono" style={{ color: "var(--text)" }}>
                    {d.server ? `${d.server}/` : ""}{d.tool}
                  </span>
                  <span style={{ color: "var(--muted-2)", fontSize: 12 }}>{d.reason}</span>
                </div>
              ))}
            </div>
          )}

          <ul className="timeline">
            {events.map((e) => (
              <li key={e.seq}>
                <div className="node">{GLYPH[e.event_type] ?? "·"}</div>
                <div>
                  <div className="ev-type">
                    {e.event_type}
                    {e.name && <span className="mono" style={{ marginLeft: 8 }}>{e.name}</span>}
                  </div>
                  <div className="ev-meta">
                    {e.tokens?.total ? `${e.tokens.total} tokens · ` : ""}
                    {e.cost_micro ? `${microToUsd(e.cost_micro)} · ` : ""}
                    seq {e.seq}
                  </div>
                  <div className="ev-hash">
                    {shortHash(e.prev_hash)} → {shortHash(e.hash)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </>
  );
}
