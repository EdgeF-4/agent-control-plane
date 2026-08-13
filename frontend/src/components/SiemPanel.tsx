import { useCallback, useEffect, useState } from "react";
import { api, DlqEntry, SiemPreset, SiemStatus } from "../lib/api";
import { ago } from "../lib/format";

// Sink health + delivery stats + the dead-letter queue for the audit forwarder,
// plus copy-paste config presets for the sinks the bridge supports.
export default function SiemPanel({ isAdmin }: { isAdmin: boolean }) {
  const [status, setStatus] = useState<SiemStatus | null>(null);
  const [dlq, setDlq] = useState<DlqEntry[]>([]);
  const [tests, setTests] = useState<Record<string, { ok: boolean; detail: string } | "running">>({});
  const [showPresets, setShowPresets] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [s, d] = await Promise.all([api.siemStatus(), api.siemDlq()]);
    setStatus(s);
    setDlq(d);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function testSink(name: string) {
    setTests((t) => ({ ...t, [name]: "running" }));
    try {
      const r = await api.siemTestSink(name);
      setTests((t) => ({ ...t, [name]: r }));
    } catch (error) {
      const detail = error instanceof Error
        ? error.message
        : "Sink test failed. Check the sink configuration and backend logs, then retry.";
      setTests((t) => ({ ...t, [name]: { ok: false, detail } }));
    }
  }

  async function replayAll() {
    setBusy(true);
    try {
      await api.siemReplay();
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (!status) {
    return (
      <div className="panel">
        <header><h2>Audit forwarding</h2></header>
        <div className="body"><div className="empty">Loading sinks…</div></div>
      </div>
    );
  }

  const s = status.stats;
  return (
    <div className="panel">
      <header>
        <h2>Audit forwarding</h2>
        <span className="sub">SIEM sinks · retry &amp; dead-letter</span>
        <span className="right">
          <button className="btn ghost" onClick={() => setShowPresets((v) => !v)}>
            {showPresets ? "Hide presets" : "Add a sink"}
          </button>
        </span>
      </header>
      <div className="body">
        <div className="siem-stats">
          <div><span className="n">{s.processed}</span><span className="l">processed</span></div>
          <div><span className="n accent">{s.delivered}</span><span className="l">delivered</span></div>
          <div><span className={`n ${s.dead_lettered ? "red" : ""}`}>{s.dead_lettered}</span><span className="l">dead-lettered</span></div>
          <div><span className="n">{status.redaction_enabled ? "on" : "off"}</span><span className="l">redaction</span></div>
        </div>

        <div className="siem-sinks">
          {status.sinks.map((sink) => {
            const t = tests[sink.name];
            return (
              <div className="sink-row" key={sink.name}>
                <span className={`dot-state ${sink.enabled ? "up" : "off"}`} />
                <div className="sink-id">
                  <div className="sink-name">{sink.name}</div>
                  <div className="mono">{sink.type}{sink.enabled ? "" : " · disabled"}</div>
                </div>
                <div className="sink-health">
                  {t === "running" && <span className="sub">testing…</span>}
                  {t && t !== "running" && (
                    <span className={`pill-int ${t.ok ? "ok" : "bad"}`} title={t.detail}>
                      {t.ok ? "✓ reachable" : "✗ unreachable"}
                    </span>
                  )}
                </div>
                {isAdmin && (
                  <button className="btn" onClick={() => testSink(sink.name)}
                          disabled={t === "running"}>Test</button>
                )}
              </div>
            );
          })}
        </div>

        <div className="dlq">
          <div className="dlq-head">
            <span>Dead-letter queue</span>
            <span className={`pill-int ${status.dlq_count ? "bad" : "ok"}`}>
              {status.dlq_count} pending
            </span>
            {isAdmin && status.dlq_count > 0 && (
              <button className="btn" onClick={replayAll} disabled={busy}>
                {busy ? "Replaying…" : "Replay all"}
              </button>
            )}
          </div>
          {dlq.length === 0 && <div className="empty small">Nothing dead-lettered. Every batch delivered.</div>}
          {dlq.map((e, i) => (
            <div className="dlq-row" key={i}>
              <span className="mono">{e.sink}</span>
              <span className="sub">{e.event_count} event{e.event_count === 1 ? "" : "s"} · {e.attempts} attempts · {ago(e.failed_at)}</span>
              <span className="dlq-err" title={`${e.error} Correct the sink configuration, then replay this batch.`}>
                {e.error} Next: correct the sink configuration, then replay this batch.
              </span>
            </div>
          ))}
        </div>

        {showPresets && (
          <div className="presets">
            <div className="sub" style={{ marginBottom: 8 }}>
              Sinks are configured as code. Paste one of these into
              <span className="mono"> engines.siem.sinks</span> in your
              <span className="mono"> config.json</span> and restart.
            </div>
            {status.presets.map((p) => <PresetCard key={p.type} preset={p} />)}
          </div>
        )}
      </div>
    </div>
  );
}

function PresetCard({ preset }: { preset: SiemPreset }) {
  const [copied, setCopied] = useState(false);
  const snippet = JSON.stringify(preset.config, null, 2);
  async function copy() {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard unavailable */ }
  }
  return (
    <div className="preset">
      <div className="preset-head">
        <div>
          <span className="preset-label">{preset.label}</span>
          <span className="mono" style={{ marginLeft: 8 }}>{preset.type}</span>
        </div>
        <button className="btn ghost" onClick={copy}>{copied ? "Copied ✓" : "Copy"}</button>
      </div>
      <div className="preset-desc">{preset.description}</div>
      <pre className="preset-code">{snippet}</pre>
    </div>
  );
}
