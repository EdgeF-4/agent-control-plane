import { useEffect } from "react";
import { BudgetAlert } from "../lib/api";

export interface ToastAlert extends BudgetAlert {
  id: number;
}

const GLYPH: Record<string, string> = {
  threshold: "▲", cap: "⛔", kill: "⏻", release: "✓",
};

// Budget alerts stream in over the live feed (threshold / cap / kill / release)
// and surface here as auto-dismissing toasts, so an operator sees a budget cross
// its limit the moment it happens rather than on the next refresh.
export default function AlertToasts({
  alerts,
  onDismiss,
}: {
  alerts: ToastAlert[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="toast-stack">
      {alerts.map((a) => (
        <Toast key={a.id} alert={a} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function Toast({ alert, onDismiss }: { alert: ToastAlert; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const t = window.setTimeout(() => onDismiss(alert.id), 9000);
    return () => window.clearTimeout(t);
  }, [alert.id, onDismiss]);

  const tone = alert.kind === "release" ? "ok" : alert.kind === "threshold" ? "warn" : "bad";
  return (
    <div className={`toast ${tone}`} role="status" onClick={() => onDismiss(alert.id)}>
      <span className="toast-glyph">{GLYPH[alert.kind] ?? "•"}</span>
      <div>
        <div className="toast-title">
          {alert.project} · {alert.kind}
        </div>
        <div className="toast-msg">{alert.message}</div>
      </div>
    </div>
  );
}
