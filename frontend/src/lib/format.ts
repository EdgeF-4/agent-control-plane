export function usd(value: number | null | undefined): string {
  const n = value ?? 0;
  return n.toLocaleString(undefined, { style: "currency", currency: "USD",
    minimumFractionDigits: n < 1 ? 4 : 2, maximumFractionDigits: 4 });
}

export function microToUsd(micro: number | null | undefined): string {
  return usd((micro ?? 0) / 1_000_000);
}

export function shortHash(h: string | null | undefined): string {
  if (!h) return "—";
  return h.length <= 12 ? h : `${h.slice(0, 8)}…${h.slice(-4)}`;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function pct(fraction: number | null | undefined): string {
  return `${Math.round((fraction ?? 0) * 100)}%`;
}

export function fmtDuration(seconds: number | null | undefined): string {
  const s = Math.round(seconds ?? 0);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
