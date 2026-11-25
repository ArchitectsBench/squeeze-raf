"use client";
// Expiration payoff diagram (thinkorswim-style): P&L at expiry vs underlying price
// for the recommended structure, from the backend's machine-readable payoff_legs.
// Options are $ per 1 contract (x100); stock is $ per 100 shares (comparable scale).
// Memoized — parents re-render on chart-hover mousemoves.
import { memo, useMemo, useState } from "react";

const UP = "#26a69a", DOWN = "#ef5350", AXIS = "#8b97ad", GRID = "#1b2433", BLUE = "#4c8dff";

type Leg = { type: "call" | "put" | "stock"; side: 1 | -1; strike?: number; premium?: number; basis?: number };

function pnlAt(S: number, legs: Leg[]): number {
  let p = 0;
  for (const l of legs) {
    if (l.type === "stock") p += l.side * (S - (l.basis ?? S)) * 100;
    else {
      const intrinsic = l.type === "call" ? Math.max(S - (l.strike ?? 0), 0) : Math.max((l.strike ?? 0) - S, 0);
      p += l.side * (intrinsic - (l.premium ?? 0)) * 100;
    }
  }
  return p;
}

function PayoffDiagram({ legs, spot, vehicle }: { legs: Leg[]; spot: number; vehicle: string }) {
  const [hx, setHx] = useState<number | null>(null);   // hovered price

  const { xs, ys, lo, hi, yMin, yMax, breakevens } = useMemo(() => {
    const ks = legs.map((l) => l.strike ?? l.basis ?? spot);
    const span = Math.max(Math.max(...ks.map((k) => Math.abs(k - spot))), spot * 0.08);
    const lo = Math.max(0.01, Math.min(spot, ...ks) - span * 0.8);
    const hi = Math.max(spot, ...ks) + span * 0.8;
    const N = 240;
    const xs: number[] = [], ys: number[] = [];
    for (let i = 0; i <= N; i++) {
      const S = lo + ((hi - lo) * i) / N;
      xs.push(S); ys.push(pnlAt(S, legs));
    }
    const breakevens: number[] = [];
    for (let i = 1; i < ys.length; i++) {
      if ((ys[i - 1] <= 0 && ys[i] > 0) || (ys[i - 1] >= 0 && ys[i] < 0)) {
        const f = ys[i] !== ys[i - 1] ? (0 - ys[i - 1]) / (ys[i] - ys[i - 1]) : 0;
        breakevens.push(xs[i - 1] + f * (xs[i] - xs[i - 1]));
      }
    }
    const yMin = Math.min(...ys, 0), yMax = Math.max(...ys, 0);
    return { xs, ys, lo, hi, yMin, yMax, breakevens };
  }, [legs, spot]);

  const W = 680, H = 220, L = 54, R = 14, TOP = 16, BOT = 190;
  const ySpan = yMax - yMin || 1;
  const x = (S: number) => L + ((S - lo) / (hi - lo)) * (W - L - R);
  const y = (p: number) => TOP + (1 - (p - yMin) / ySpan) * (BOT - TOP);
  const zero = y(0);
  const pts = xs.map((S, i) => `${x(S)},${y(ys[i])}`).join(" ");
  const above = xs.map((S, i) => `${x(S)},${Math.min(y(ys[i]), zero)}`).join(" ");
  const below = xs.map((S, i) => `${x(S)},${Math.max(y(ys[i]), zero)}`).join(" ");
  const hpnl = hx != null ? pnlAt(hx, legs) : null;
  const fmt$ = (v: number) => `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(0)}`;

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = (e.target as SVGElement).closest("svg")!.getBoundingClientRect();
    const S = lo + (((e.clientX - r.left) / r.width) * W - L) / (W - L - R) * (hi - lo);
    setHx(S >= lo && S <= hi ? S : null);
  };

  return (
    <div>
      <div className="muted" style={{ fontSize: 11, margin: "6px 0 2px" }}>
        Payoff at expiration · {vehicle} · {legs.some((l) => l.type !== "stock") ? "per contract" : "per 100 sh"}
        {hx != null && hpnl != null && (
          <b style={{ color: hpnl >= 0 ? UP : DOWN, marginLeft: 10 }}>
            @{hx.toFixed(2)} → {fmt$(hpnl)}
          </b>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ background: "#0b0e14", borderRadius: 6 }}
           onMouseMove={onMove} onMouseLeave={() => setHx(null)}>
        {/* profit/loss shading against the zero line */}
        <polygon points={`${x(lo)},${zero} ${above} ${x(hi)},${zero}`} fill={UP} opacity={0.13} />
        <polygon points={`${x(lo)},${zero} ${below} ${x(hi)},${zero}`} fill={DOWN} opacity={0.13} />
        {/* y gridlines + labels */}
        {[yMin, yMin + ySpan / 2, yMax].map((p, k) => (
          <g key={k}>
            <line x1={L} x2={W - R} y1={y(p)} y2={y(p)} stroke={GRID} strokeWidth={0.5} />
            <text x={L - 4} y={y(p) + 3} fontSize={9} fill={AXIS} textAnchor="end">{fmt$(p)}</text>
          </g>
        ))}
        <line x1={L} x2={W - R} y1={zero} y2={zero} stroke={AXIS} strokeWidth={1} />
        {/* payoff curve */}
        <polyline points={pts} fill="none" stroke="#e8ecf4" strokeWidth={1.8} />
        {/* spot marker */}
        <line x1={x(spot)} x2={x(spot)} y1={TOP} y2={BOT} stroke={BLUE} strokeWidth={1.2} strokeDasharray="4 3" />
        <text x={x(spot)} y={TOP - 3} fontSize={10} fontWeight={600} fill={BLUE} textAnchor="middle">
          spot {spot.toFixed(2)}
        </text>
        {/* breakevens */}
        {breakevens.map((b, k) => (
          <g key={k}>
            <circle cx={x(b)} cy={zero} r={3.5} fill="#e6a23c" />
            <text x={x(b)} y={zero + 14} fontSize={9.5} fontWeight={600} fill="#e6a23c" textAnchor="middle">
              BE {b.toFixed(2)}
            </text>
          </g>
        ))}
        {/* strike ticks + x labels */}
        {legs.filter((l) => l.strike != null).map((l, k) => (
          <g key={k}>
            <line x1={x(l.strike!)} x2={x(l.strike!)} y1={BOT} y2={BOT + 4} stroke={AXIS} strokeWidth={1} />
            <text x={x(l.strike!)} y={BOT + 14} fontSize={9} fill={AXIS} textAnchor="middle">{l.strike}</text>
          </g>
        ))}
        {[lo, (lo + hi) / 2, hi].map((S, k) => (
          <text key={k} x={x(S)} y={H - 2} fontSize={9} fill={AXIS}
                textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"}>{S.toFixed(0)}</text>
        ))}
        {/* hover crosshair */}
        {hx != null && hpnl != null && (
          <g>
            <line x1={x(hx)} x2={x(hx)} y1={TOP} y2={BOT} stroke="#3a4658" strokeWidth={0.7} strokeDasharray="2 2" />
            <circle cx={x(hx)} cy={y(hpnl)} r={3} fill={hpnl >= 0 ? UP : DOWN} />
          </g>
        )}
      </svg>
    </div>
  );
}

export default memo(PayoffDiagram);
