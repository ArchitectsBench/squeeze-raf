"use client";
import { memo, useEffect, useRef, useState } from "react";
import { Bar, News, fetchChart, fetchDecision, fetchNews, fetchTradePlan } from "../lib/api";
import GexProfile from "./GexProfile";
import PayoffDiagram from "./PayoffDiagram";

// ordered display of vehicle-specific fields (only present ones render)
const PLAN_FIELDS: [string, string][] = [
  ["legs", "Legs"], ["strike", "Strike"], ["entry", "Entry"],
  ["net_debit", "Net debit"], ["net_credit", "Net credit"], ["cost_per_contract", "Cost/contract"],
  ["breakeven", "Breakeven"], ["target", "Target"], ["stop", "Stop"],
  ["max_loss", "Max loss"], ["max_profit", "Max profit"], ["risk_reward", "R/R"], ["width", "Width"],
];

const PlanPanel = memo(function PlanPanel({ plan }: { plan: any }) {
  const rec = plan?.recommended;
  if (!rec) return null;
  const alts = (plan.alternatives || []) as any[];
  const val = (v: any) => (typeof v === "number" ? String(v) : String(v ?? ""));
  return (
    <div style={{ marginTop: 8, border: "1px solid var(--border)", borderRadius: 8, padding: "8px 12px" }}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <b>How to enter · {rec.vehicle}</b>
        {plan.exp && <span className="muted" style={{ fontSize: 11 }}>exp {plan.exp}</span>}
        {plan.direction && <span className="muted" style={{ fontSize: 11 }}>{plan.direction}</span>}
      </div>
      <table style={{ fontSize: 12, marginTop: 4, fontVariantNumeric: "tabular-nums" }}>
        <tbody>
          {PLAN_FIELDS.filter(([k]) => rec[k] !== undefined && rec[k] !== null).map(([k, label]) => (
            <tr key={k}>
              <td className="muted" style={{ paddingRight: 12 }}>{label}</td>
              <td style={{ fontWeight: k === "legs" ? 700 : 500 }}>{val(rec[k])}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rec.why?.length ? (
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>{rec.why.join(" · ")}</div>
      ) : null}
      {rec.payoff_legs?.length && plan?.inputs?.spot ? (
        <PayoffDiagram legs={rec.payoff_legs} spot={plan.inputs.spot} vehicle={rec.vehicle} />
      ) : rec.vehicle === "CALENDAR" ? (
        <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
          (no expiry payoff for calendars — value at front expiry depends on back-month IV)
        </div>
      ) : null}
      {alts.length ? (
        <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
          <b>Alternatives:</b>{" "}
          {alts.map((a) => `${a.vehicle}${a.legs ? ` (${a.legs})` : ""}`).join(" · ")}
        </div>
      ) : null}
    </div>
  );
});

const STATE_COLOR: Record<number, string> = { 3: "#ef5350", 2: "#ffb74d", 1: "#8b97ad", 0: "#26a69a" };
const SENT_COLOR: Record<string, string> = { positive: "#26a69a", negative: "#ef5350", neutral: "#8b97ad" };
const MULTIDAY = ["1d", "2d", "3d", "4d", "1w"];
const TFS = ["5min", "15min", "30min", "1h", "2h", "3h", "4h", "1d", "2d", "3d", "4d", "1w"];
const UP = "#26a69a", DOWN = "#ef5350", AXIS = "#8b97ad", GRID = "#1b2433", BLUE = "#4c8dff", AMBER = "#e6a23c";
const CALL_COLOR: Record<string, string> = { GO: UP, AVOID: DOWN, CAUTION: AMBER };
const vol = (v: number) => (v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "k" : String(v));

type Level = { label: string; value: number; color: string; dash?: string };

export default function ChartModal({ symbol, tf, onClose }: { symbol: string; tf: string; onClose: () => void }) {
  const [curTf, setCurTf] = useState(tf);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [news, setNews] = useState<News | null>(null);
  const [dec, setDec] = useState<any>(null);       // /api/decision (verdict + gex)
  const [plan, setPlan] = useState<any>(null);     // /api/trade_plan
  const [hov, setHov] = useState<number | null>(null);
  const [my_, setMy_] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => setCurTf(tf), [symbol, tf]);
  useEffect(() => {
    setBars(null);
    fetchChart(symbol, curTf).then((d) => setBars(d.bars)).catch(() => setBars([]));
  }, [symbol, curTf]);
  useEffect(() => {
    setNews(null); setDec(null); setPlan(null);
    fetchNews(symbol).then(setNews).catch(() => setNews(null));
    fetchDecision(symbol).then(setDec).catch(() => setDec(null));
    fetchTradePlan(symbol).then(setPlan).catch(() => setPlan(null));
  }, [symbol]);

  const W = 860, H = 468, ML = 8, MR = 50;
  const priceTop = 14, priceBot = 286, priceH = priceBot - priceTop;
  const dotY = priceBot + 8, timeY = 303, momTop = 316, momBot = 386, momMid = (momTop + momBot) / 2;
  const rvTop = 396, rvBot = 458;   // RVOL pane (1.0 = normal volume)
  const isDaily = MULTIDAY.includes(curTf);
  const fmt = (t: string) => {
    if (isDaily) return t.slice(5, 10);
    const d = new Date(t.replace(" ", "T"));
    return d.toLocaleString("en-US", { timeZone: "America/New_York", month: "numeric", day: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false });
  };

  // interpretation levels: trade-plan entry/target/stop + dealer walls / gamma flip
  const rec = plan?.recommended;
  const stk = rec?.vehicle === "STOCK" ? rec
    : plan?.alternatives?.find((a: any) => a.vehicle === "STOCK");
  const gex = dec?.gex;
  const levels: Level[] = [];
  if (stk) {
    if (typeof stk.entry === "number") levels.push({ label: "entry", value: stk.entry, color: BLUE });
    if (typeof stk.target === "number") levels.push({ label: `target${stk.target_note ? " · " + stk.target_note : ""}`, value: stk.target, color: UP });
    if (typeof stk.stop === "number") levels.push({ label: `stop${stk.stop_note ? " · " + stk.stop_note : ""}`, value: stk.stop, color: DOWN });
  }
  // option-vehicle levels: breakeven + strike(s) of the recommended structure
  if (rec && rec.vehicle !== "STOCK") {
    if (typeof rec.breakeven === "number") levels.push({ label: `breakeven (${rec.vehicle})`, value: rec.breakeven, color: AMBER });
    if (typeof rec.strike === "number") levels.push({ label: "strike", value: rec.strike, color: AXIS, dash: "5 3" });
  }
  if (gex?.available) {
    if (gex.call_wall != null) levels.push({ label: "call wall", value: gex.call_wall, color: AMBER, dash: "4 3" });
    if (gex.put_wall != null && gex.put_wall !== gex.call_wall)
      levels.push({ label: "put wall", value: gex.put_wall, color: AMBER, dash: "4 3" });
    if (gex.flip != null) levels.push({ label: "γ-flip", value: gex.flip, color: AXIS, dash: "2 3" });
  }
  const v = dec?.verdict;

  let body = <div className="muted">loading…</div>;
  let readout: React.ReactNode = null;
  if (bars && bars.length) {
    // include visible levels in the y-domain so entry/stop/walls never clip off-pane
    const lvlVals = levels.map((l) => l.value);
    const rawLo = Math.min(...bars.map((b) => b.l));
    const rawHi = Math.max(...bars.map((b) => b.h));
    const pad = (rawHi - rawLo || 1) * 0.25;   // include levels only if near the price range
    const lo = Math.min(rawLo, ...lvlVals.filter((x) => x > rawLo - pad && x < rawHi + pad));
    const hi = Math.max(rawHi, ...lvlVals.filter((x) => x > rawLo - pad && x < rawHi + pad));
    const span = hi - lo || 1;
    const cw = (W - ML - MR) / (bars.length + 5);   // +5 bar-widths of right gap so the latest candle isn't on the edge
    const x = (i: number) => ML + i * cw + cw / 2;
    const y = (p: number) => priceTop + (1 - (p - lo) / span) * priceH;
    const priceAt = (yy: number) => lo + (1 - (yy - priceTop) / priceH) * span;
    const mmax = Math.max(...bars.map((b) => Math.abs(b.mom)), 1e-6);
    const myF = (m: number) => momMid - (m / mmax) * (momBot - momMid - 3);
    const maPts = bars.map((b, i) => (b.ma == null ? null : `${x(i)},${y(b.ma)}`)).filter(Boolean).join(" ");
    const last = bars[bars.length - 1];
    const hb = hov != null ? bars[hov] : last;
    const hbi = hov != null ? hov : bars.length - 1;
    const d = hb.c - hb.o, dpct = hb.o ? (d / hb.o) * 100 : 0;
    // signed RVOL (+ = up-close volume) and its 5-bar EMA "pressure"
    const signed = bars.map((b) => (b.rvol == null ? null : (b.c >= b.o ? 1 : -1) * b.rvol));
    const press: (number | null)[] = [];
    {
      let ema: number | null = null;
      const alpha = 2 / 6;
      for (const v of signed) {
        if (v != null) ema = ema == null ? v : alpha * v + (1 - alpha) * ema;
        press.push(ema);
      }
    }
    const pNow = press[hbi], pPrev = hbi > 0 ? press[hbi - 1] : null;
    const forming = pNow != null && pPrev != null
      ? (pNow >= 0 ? (pNow > pPrev ? "↑ building (upside)" : "↺ fading")
                   : (pNow < pPrev ? "↓ building (downside)" : "↺ fading"))
      : null;
    const vis = levels.filter((l) => l.value >= lo && l.value <= hi);

    const onMove = (e: React.MouseEvent) => {
      const svg = svgRef.current; if (!svg) return;
      const r = svg.getBoundingClientRect();
      const vx = ((e.clientX - r.left) / r.width) * W;
      let idx = Math.round((vx - ML - cw / 2) / cw);
      idx = Math.max(0, Math.min(bars.length - 1, idx));
      setHov(idx); setMy_(((e.clientY - r.top) / r.height) * H);
    };

    readout = (
      <div style={{ fontFamily: "var(--mono)", fontSize: 12, margin: "2px 0 6px", color: "var(--text)" }}>
        <b>{fmt(hb.t)}</b>{"  "}O <b>{hb.o.toFixed(2)}</b>{"  "}H <b>{hb.h.toFixed(2)}</b>
        {"  "}L <b>{hb.l.toFixed(2)}</b>{"  "}C <b>{hb.c.toFixed(2)}</b>
        {"  "}Vol <b>{vol(hb.v)}</b>
        {hb.rvol != null && <>
          {"  "}RVOL <b style={{ color: hb.rvol >= 2 ? DOWN : hb.rvol >= 1.5 ? AMBER : "inherit" }}>{hb.rvol.toFixed(2)}x</b>
          {forming && <span style={{ color: pNow! >= 0 ? UP : DOWN }}> {forming}</span>}
        </>}
        {"  "}<span style={{ color: d >= 0 ? UP : DOWN }}>Δ {d >= 0 ? "+" : ""}{d.toFixed(2)} ({dpct >= 0 ? "+" : ""}{dpct.toFixed(2)}%)</span>
      </div>
    );

    body = (
      <svg ref={svgRef} width="100%" viewBox={`0 0 ${W} ${H}`} style={{ background: "#0b0e14", borderRadius: 6 }}
           onMouseMove={onMove} onMouseLeave={() => { setHov(null); setMy_(null); }}>
        {[0, 1, 2, 3, 4].map((k) => {
          const p = lo + (span * k) / 4, yy = y(p);
          return <g key={k}><line x1={ML} x2={W - MR} y1={yy} y2={yy} stroke={GRID} strokeWidth={0.5} />
            <text x={W - MR + 4} y={yy + 3} fontSize={9} fill={AXIS}>{p.toFixed(2)}</text></g>;
        })}
        {maPts && <polyline points={maPts} fill="none" stroke={BLUE} strokeWidth={1.2} />}
        {bars.map((b, i) => {
          const xx = x(i), up = b.c >= b.o, col = up ? UP : DOWN;
          return (
            <g key={i}>
              <line x1={xx} x2={xx} y1={y(b.h)} y2={y(b.l)} stroke={col} strokeWidth={1} />
              <rect x={xx - cw * 0.3} width={Math.max(1, cw * 0.6)} y={y(Math.max(b.o, b.c))}
                height={Math.max(1, Math.abs(y(b.o) - y(b.c)))} fill={col} />
              <rect x={xx - cw * 0.35} width={Math.max(1, cw * 0.7)} y={myF(Math.max(0, b.mom))}
                height={Math.abs(myF(b.mom) - myF(0))} fill={b.mom >= 0 ? UP : DOWN} opacity={0.7} />
              <circle cx={xx} cy={dotY} r={2} fill={STATE_COLOR[b.state] ?? UP} />
            </g>
          );
        })}
        {/* interpretation levels: plan entry/target/stop (solid) + walls/flip (dashed) */}
        {vis.map((l) => (
          <g key={l.label}>
            <line x1={ML} x2={W - MR} y1={y(l.value)} y2={y(l.value)} stroke={l.color}
                  strokeWidth={l.dash ? 1 : 1.3} strokeDasharray={l.dash} opacity={0.9} />
            <text x={ML + 4} y={y(l.value) - 3} fontSize={9} fontWeight={600} fill={l.color}>
              {l.label} {l.value.toFixed(2)}
            </text>
          </g>
        ))}
        <line x1={ML} x2={W - MR} y1={momMid} y2={momMid} stroke="#2a3245" strokeWidth={0.5} />
        <text x={ML + 2} y={momTop + 9} fontSize={8} fill={AXIS}>momentum</text>
        {/* DIRECTIONAL RVOL pane: signed by candle direction (up-close volume above the
            axis in teal, down-close below in red) + a 5-bar EMA "pressure" line showing
            whether participation is FORMING for upside (>0, rising) or downside. */}
        {(() => {
          const rvMax = Math.max(2.5, ...signed.map((v) => Math.abs(v ?? 0)));
          const mid = (rvTop + rvBot) / 2;
          const half = (rvBot - rvTop) / 2 - 3;
          const ry = (v: number) => mid - (Math.max(-rvMax, Math.min(v, rvMax)) / rvMax) * half;
          return (
            <g>
              <text x={ML + 2} y={rvTop + 8} fontSize={8} fill={AXIS}>RVOL ±dir</text>
              <line x1={ML} x2={W - MR} y1={mid} y2={mid} stroke={AXIS} strokeWidth={0.7} />
              {[1, -1].map((s) => (
                <g key={s}>
                  <line x1={ML} x2={W - MR} y1={ry(s)} y2={ry(s)} stroke="#2a3245" strokeWidth={0.6} strokeDasharray="3 3" />
                  <text x={W - MR + 4} y={ry(s) + 3} fontSize={8} fill={AXIS}>{s > 0 ? "+1x" : "-1x"}</text>
                </g>
              ))}
              {bars.map((b, i) => {
                const v = signed[i];
                if (v == null) return null;
                const mag = Math.abs(v);
                const col = v >= 0 ? UP : DOWN;
                return (
                  <rect key={i} x={x(i) - cw * 0.3} width={Math.max(1, cw * 0.6)}
                        y={Math.min(ry(v), mid)} height={Math.max(1, Math.abs(ry(v) - mid))}
                        fill={col} opacity={mag >= 2 ? 1 : mag >= 1.5 ? 0.8 : 0.45} />
                );
              })}
              {/* pressure line: segments colored by sign — above 0 = volume forming for upside */}
              {press.map((p, i) => {
                if (i === 0 || p == null || press[i - 1] == null) return null;
                return <line key={i} x1={x(i - 1)} y1={ry(press[i - 1]!)} x2={x(i)} y2={ry(p)}
                             stroke={p >= 0 ? "#7fe0c3" : "#f59a9a"} strokeWidth={1.6} />;
              })}
            </g>
          );
        })()}
        <line x1={ML} x2={W - MR} y1={y(last.c)} y2={y(last.c)} stroke={BLUE} strokeWidth={0.5} strokeDasharray="3 3" />
        <rect x={W - MR} y={y(last.c) - 7} width={MR} height={14} fill={BLUE} />
        <text x={W - MR + 4} y={y(last.c) + 3} fontSize={9} fontWeight={700} fill="#0b0e14">{last.c.toFixed(2)}</text>
        {[0, 0.2, 0.4, 0.6, 0.8, 1].map((f, k) => {
          const i = Math.round(f * (bars.length - 1));
          return <text key={k} x={x(i)} y={timeY} fontSize={9} fill={AXIS} textAnchor="middle">{fmt(bars[i].t)}</text>;
        })}
        {hov != null && <line x1={x(hov)} x2={x(hov)} y1={priceTop} y2={rvBot} stroke="#3a4658" strokeWidth={0.7} strokeDasharray="2 2" />}
        {my_ != null && my_ > priceTop && my_ < priceBot && (
          <g><line x1={ML} x2={W - MR} y1={my_} y2={my_} stroke="#3a4658" strokeWidth={0.7} strokeDasharray="2 2" />
            <rect x={W - MR} y={my_ - 7} width={MR} height={14} fill="#2a3245" />
            <text x={W - MR + 4} y={my_ + 3} fontSize={9} fill="var(--text)">{priceAt(my_).toFixed(2)}</text></g>
        )}
      </svg>
    );
  } else if (bars) {
    body = <div className="muted">no data for {symbol} · {curTf}</div>;
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="btn close" onClick={onClose}>✕</button>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <h3 style={{ margin: 0 }}>{symbol} · {curTf}</h3>
          {v && (
            <a href={`/internals?symbol=${symbol}`} style={{ textDecoration: "none" }}
               title={(v.reasons || []).join("; ")}>
              <span style={{ fontWeight: 700, fontSize: 11, padding: "2px 8px", borderRadius: 4,
                background: CALL_COLOR[v.call], color: "#06121a" }}>
                {v.call} {Math.round(v.conviction)} {v.direction === "UP" ? "▲" : "▼"}
              </span>
            </a>
          )}
          {rec && (
            <span className="muted" style={{ fontSize: 11 }}>
              {rec.vehicle === "STOCK"
                ? `plan: entry ${rec.entry} → target ${rec.target} / stop ${rec.stop}${rec.risk_reward ? ` · R/R ${rec.risk_reward}` : ""}`
                : `plan: ${rec.vehicle}${rec.legs ? ` — ${rec.legs}` : ""}`}
            </span>
          )}
          <span className="spacer" />
          <a href={`/plan?symbol=${symbol}`} className="btn" style={{ textDecoration: "none", fontSize: 11 }}>full plan →</a>
        </div>
        {v?.reasons?.length ? (
          <div className="muted" style={{ fontSize: 11, margin: "3px 0" }}>{v.reasons.join(" · ")}</div>
        ) : null}
        <div style={{ display: "flex", gap: 4, margin: "6px 0", flexWrap: "wrap" }}>
          {TFS.map((t) => (
            <button key={t} className="btn" onClick={() => setCurTf(t)}
                    style={{ fontSize: 10, padding: "2px 7px",
                             ...(t === curTf ? { background: "var(--accent)", color: "#fff", borderColor: "var(--accent)" } : {}) }}>
              {t}
            </button>
          ))}
        </div>
        {readout}
        {body}
        <div className="muted" style={{ fontSize: 10, display: "flex", gap: 14, margin: "6px 0 2px", flexWrap: "wrap" }}>
          <span><i style={{ color: BLUE }}>—</i> 20-MA / entry</span>
          <span><i style={{ color: UP }}>—</i> target</span>
          <span><i style={{ color: DOWN }}>—</i> stop</span>
          <span><i style={{ color: AMBER }}>┅</i> dealer walls (levels only — regime not predictive)</span>
          <span>squeeze dot: <i style={{ color: "#ef5350" }}>●</i> tight … <i style={{ color: "#26a69a" }}>●</i> fired</span>
          <span>RVOL±dir: <i style={{ color: UP }}>▮</i> up-close vol / <i style={{ color: DOWN }}>▮</i> down-close vol (brighter = ≥1.5x/2x) · line = 5-bar pressure: above 0 &amp; rising = volume forming for UPSIDE</span>
        </div>
        <PlanPanel plan={plan} />
        {gex?.available && (
          <div style={{ marginTop: 8 }}>
            <div className="muted" style={{ marginBottom: 2 }}>
              Dealer gamma · {gex.regime_sign === "POS" ? "positive (pinning)" : "negative (amplify)"} ·
              net {gex.net_gex_musd}M$ · walls {gex.put_wall} / {gex.call_wall} · flip {gex.flip ?? "·"}
            </div>
            <GexProfile gex={gex} />
          </div>
        )}
        <div style={{ marginTop: 10 }}>
          <div className="muted" style={{ marginBottom: 4 }}>
            📰 Recent news {news ? `· ${news.count} · net ${news.sentiment} (last 7d)` : "…"}
          </div>
          <div style={{ maxHeight: 150, overflow: "auto", fontSize: 12 }}>
            {news?.items?.length ? news.items.map((it, i) => (
              <div key={i} style={{ padding: "4px 0", borderBottom: "1px solid var(--border)",
                                    opacity: (it as any).fresh === false ? 0.5 : 1 }}>
                <span style={{ display: "inline-block", width: 7, height: 7, borderRadius: 4,
                  background: SENT_COLOR[it.sentiment ?? "neutral"], marginRight: 6 }} />
                <a href={it.url} target="_blank" rel="noreferrer">{it.title}</a>
                <span className="muted"> · {it.publisher} · {it.published?.slice(0, 10)}</span>
              </div>
            )) : <span className="muted">no recent headlines</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
