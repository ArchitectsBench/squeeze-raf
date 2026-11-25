export default function Sparkline({
  data, width = 140, height = 34, color = "#4c8dff",
}: { data: number[]; width?: number; height?: number; color?: string }) {
  if (!data.length) return <svg width={width} height={height} />;
  const min = Math.min(...data), max = Math.max(...data), span = max - min || 1;
  const pts = data
    .map((v, i) => `${(i / Math.max(1, data.length - 1)) * width},${height - ((v - min) / span) * (height - 4) - 2}`)
    .join(" ");
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <line x1={0} x2={width} y1={height - ((50 - min) / span) * (height - 4) - 2}
            y2={height - ((50 - min) / span) * (height - 4) - 2} stroke="#2a3245" strokeWidth={0.5} strokeDasharray="2 2" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}
