import { useCallback, useEffect, useState } from "react";
import { api, EvalDiff, EvalRun, EvalSchedule } from "../lib/api";
import { ago, pct } from "../lib/format";

const STATUS_TONE: Record<string, string> = {
  regression: "bad", fixed: "ok", added: "blue", removed: "gold",
  unchanged_pass: "muted", unchanged_fail: "muted",
};

// Reliability: latest suite result, regression diff vs. the pinned baseline, and
// the schedules that re-run the suite on a cadence.
export default function EvalsPanel({
  evals,
  isAdmin,
  onChanged,
}: {
  evals: EvalRun[];
  isAdmin: boolean;
  onChanged: () => void;
}) {
  const [schedules, setSchedules] = useState<EvalSchedule[]>([]);
  const [diff, setDiff] = useState<EvalDiff | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [busy, setBusy] = useState(false);
  const [interval, setIntervalMin] = useState(1440);

  const loadSchedules = useCallback(async () => {
    setSchedules(await api.evalSchedules());
  }, []);
  useEffect(() => { loadSchedules(); }, [loadSchedules]);

  const latest = evals[0];

  async function runNow() {
    setBusy(true);
    try {
      await api.runEval();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function toggleDiff() {
    if (showDiff) { setShowDiff(false); return; }
    if (latest) setDiff(await api.evalDiff(latest.id));
    setShowDiff(true);
  }

  async function addSchedule() {
    await api.createEvalSchedule({ interval_minutes: interval, enabled: true });
    await loadSchedules();
  }

  return (
    <div className="panel">
      <header>
        <h2>Reliability</h2>
        <span className="sub">offline regression suite</span>
        {isAdmin && (
          <span className="right">
            <button className="btn" onClick={runNow} disabled={busy}>
              {busy ? "Running…" : "Run now"}
            </button>
          </span>
        )}
      </header>
      <div className="body">
        {!latest && <div className="empty">No eval runs yet.</div>}
        {latest && (
          <>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
              <div style={{ fontSize: 30, fontWeight: 700,
                            color: latest.pass_rate >= 1 ? "var(--accent)" : "var(--gold)" }}>
                {pct(latest.pass_rate)}
              </div>
              <div style={{ color: "var(--muted)" }}>{latest.passed}/{latest.total} cases pass</div>
              <div style={{ marginLeft: "auto" }}>
                {latest.has_regressions === true
                  ? <span className="pill-int bad">▼ regressions. Next: view the diff, correct the regressed cases, then rerun the suite.</span>
                  : <span className="pill-int ok">✓ no regressions</span>}
              </div>
            </div>
            <div style={{ color: "var(--muted-2)", fontSize: 12, marginTop: 8 }}>
              suite “{latest.suite_name}” · {evals.length} run{evals.length === 1 ? "" : "s"} recorded
              <button className="linklike" onClick={toggleDiff}>
                {showDiff ? "hide diff" : "view diff"}
              </button>
            </div>

            {showDiff && diff && (
              <div className="diff">
                {!diff.comparison && <div className="empty small">No baseline pinned yet — pin one to see regressions.</div>}
                {diff.comparison && (
                  <>
                    <div className="diff-summary">
                      <span className="bad">{diff.comparison.summary.regressions} regressed</span>
                      <span className="ok">{diff.comparison.summary.fixes} fixed</span>
                      <span>Δ pass {(diff.comparison.summary.pass_rate_delta * 100).toFixed(0)}%</span>
                    </div>
                    {diff.comparison.case_deltas.map((c) => (
                      <div className="diff-row" key={c.case_id}>
                        <span className={`dot-state ${c.status === "regression" ? "off" : "up"}`} />
                        <span className="mono">{c.case_id}</span>
                        <span className={`case-status ${STATUS_TONE[c.status] ?? ""}`}>
                          {c.status.replace("_", " ")}
                        </span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
          </>
        )}

        <div className="schedules">
          <div className="sched-head">
            <span>Schedules</span>
            {isAdmin && (
              <span className="right sched-add">
                <input type="number" min={1} value={interval}
                       onChange={(e) => setIntervalMin(Math.max(1, Number(e.target.value)))} />
                <span className="sub">min</span>
                <button className="btn" onClick={addSchedule}>Add</button>
              </span>
            )}
          </div>
          {schedules.length === 0 && <div className="empty small">No schedules. The suite runs on demand.</div>}
          {schedules.map((s) => (
            <div className="sched-row" key={s.id}>
              <span className={`dot-state ${s.enabled ? "up" : "off"}`} />
              <div className="sched-id">
                <div className="mono">{s.suite_name}</div>
                <div className="sub">every {s.interval_minutes}m · next {ago(s.next_run_at)}</div>
              </div>
              {isAdmin && (
                <span className="sched-actions">
                  <button className="btn" onClick={async () => { await api.runEvalSchedule(s.id); onChanged(); await loadSchedules(); }}>Run</button>
                  <button className="btn ghost" onClick={async () => { await api.deleteEvalSchedule(s.id); await loadSchedules(); }}>✕</button>
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
