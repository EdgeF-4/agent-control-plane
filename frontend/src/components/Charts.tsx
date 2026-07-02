// Tiny dependency-free SVG charts. The product ships no charting library — the
// bundle stays small and there is nothing extra to audit.

export function AreaChart({
  values,
  width = 560,
  height = 96,
  stroke = "var(--accent)",
}: {
  values: number[];
  width?: number;
  height?: number;
  stroke?: string;
}) {
  if (values.length < 2) {
    return <div className="chart-empty">Not enough data to chart yet.</div>;
  }
  const max = Math.max(...values, 1);
  const n = values.length;
  const pad = 4;
  const w = width - pad * 2;
  const h = height - pad * 2;
  const pts = values.map((v, i) => {
    const x = pad + (i / (n - 1)) * w;
    const y = pad + (h - (v / max) * h);
    return [x, y] as const;
  });
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${(pad + w).toFixed(1)},${(pad + h).toFixed(1)} L${pad},${(pad + h).toFixed(1)} Z`;
  return (
    <svg className="area-chart" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
         width="100%" height={height} role="img" aria-label="cumulative cost">
      <defs>
        <linearGradient id="acp-area-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.28" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill="url(#acp-area-fill)" />
      <path d={line} fill="none" stroke={stroke} strokeWidth="1.6"
            strokeLinejoin="round" strokeLinecap="round" />
      {pts.map(([x, y], i) => <circle key={i} cx={x} cy={y} r="1.9" fill={stroke} />)}
    </svg>
  );
}
