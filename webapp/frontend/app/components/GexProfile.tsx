"use client";
import { memo } from "react";
// Dependency-free annotated gamma-exposure profile (matches the redesign mockup):
// net dealer γ by strike (teal + / red −), shaded regime zones (red below flip, teal above,
// amber pinning zone between walls), labelled spot / γ-flip / call-wall / put-wall, strike axis.
const C = { teal: "#2dd1a4", red: "#f06464", amber: "#e6a23c", blue: "#5aa0f0",
  t3: "#59627a", line: "#2b3548" };

function GexProfile({ gex }: { gex: any }) {
  const prof = (gex?.profile || []) as { strike: number; gex: number }[];
  const spot = gex?.spot;
  if (!prof.length || !spot) return null;
  const pts = prof.filter((p) => p.strike >= spot * 0.82 && p.strike <= spot * 1.18);
  if (!pts.length) return null;

  const W = 680, H = 250, L = 26, R = 16, TOP = 52, BOT = 208, MID = (TOP + BOT) / 2;
  const flip = gex.flip, cw = gex.call_wall, pw = gex.put_wall;
  const dom = [...pts.map((p) => p.strike), spot, flip, cw, pw].filter((v): v is number => v != null);
  const minK = Math.min(...dom), maxK = Math.max(...dom), span = maxK - minK || 1;
  const x = (k: number) => L + ((k - minK) / span) * (W - L - R);
  const maxAbs = Math.max(...pts.map((p) => Math.abs(p.gex)), 1e-6);
  const bw = Math.max(2, ((W - L - R) / pts.length) * 0.62);
  const hh = (v: number) => (Math.abs(v) / maxAbs) * (MID - TOP - 6);

  const vlabel = (k: number | null, color: string, dash: string | undefined, lbl: string, ty: number, anchor = "middle") =>
    k == null ? null : (
      <g key={lbl}>
        <line x1={x(k)} x2={x(k)} y1={TOP} y2={BOT} stroke={color} strokeWidth={dash ? 1.3 : 2} strokeDasharray={dash} />
        <line x1={x(k)} x2={x(k)} y1={ty + 3} y2={TOP} stroke={color} strokeWidth={1} />
        <text x={x(k)} y={ty} fontSize={11} fontWeight={600} fill={color} textAnchor={anchor as any}>{lbl}</text>
      </g>
    );

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img">
      {/* regime zones */}
      {flip != null && <rect x={x(minK)} y={TOP} width={Math.max(0, x(flip) - x(minK))} height={BOT - TOP} fill="rgba(240,100,100,0.07)" />}
      {flip != null && <rect x={x(flip)} y={TOP} width={Math.max(0, x(maxK) - x(flip))} height={BOT - TOP} fill="rgba(45,209,164,0.07)" />}
      {pw != null && cw != null && <rect x={x(pw)} y={TOP} width={Math.max(0, x(cw) - x(pw))} height={BOT - TOP} fill="rgba(230,162,60,0.07)" />}
      {/* baseline */}
      <line x1={L} x2={W - R} y1={MID} y2={MID} stroke={C.line} strokeWidth={1} strokeDasharray="2 3" />
      <text x={L + 2} y={MID - 4} fontSize={9} fill={C.t3}>0 net γ</text>
      <text x={L + 2} y={TOP - 2} fontSize={9} fill={C.t3}>net dealer γ</text>
      {/* bars */}
      {pts.map((p, i) => {
        const up = p.gex >= 0; const h = hh(p.gex);
        return <rect key={i} x={x(p.strike) - bw / 2} width={bw} y={up ? MID - h : MID}
                     height={Math.max(1, h)} rx={1.5} fill={up ? C.teal : C.red} />;
      })}
      {/* markers + labels */}
      {vlabel(cw, C.teal, "2 2", `call wall ${cw}`, 40)}
      {vlabel(flip, C.amber, "4 3", `flip ${flip}`, 28, "end")}
      {vlabel(spot, C.blue, undefined, `spot ${spot}`, 16)}
      {pw != null && <g><line x1={x(pw)} x2={x(pw)} y1={TOP} y2={BOT} stroke={C.red} strokeWidth={1.3} strokeDasharray="2 2" />
        <text x={x(pw)} y={BOT + 26} fontSize={9} fill={C.red} textAnchor="middle">PW {pw}</text></g>}
      {/* strike axis */}
      {[0, 0.25, 0.5, 0.75, 1].map((f, i) => {
        const k = Math.round(minK + f * span);
        return <g key={i}><line x1={x(k)} x2={x(k)} y1={BOT} y2={BOT + 4} stroke={C.line} />
          <text x={x(k)} y={BOT + 16} fontSize={10} fill={C.t3} textAnchor="middle">{k}</text></g>;
      })}
      <text x={W - R} y={BOT + 16} fontSize={10} fill={C.t3} textAnchor="end">strike →</text>
    </svg>
  );
}

// memoized: parents re-render on every chart-hover mousemove; this only depends on gex
export default memo(GexProfile);
