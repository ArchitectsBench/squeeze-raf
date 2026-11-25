"use client";
import { useEffect, useState } from "react";
import GexProfile from "../components/GexProfile";
import Sparkline from "../components/Sparkline";
import {
  Intraday, Tape, TapeHistPoint, TradePlan,
  fetchDecision, fetchIntraday, fetchTape, fetchTapeHistory, fetchTradePlan,
} from "../lib/api";

const zone = (s: number) => (s >= 60 ? "var(--up)" : s < 40 ? "var(--down)" : "var(--amber)");
const gpct = (s: number) => Math.max(0, Math.min(100, s));
// classify a verdict reason as positive / negative for chip styling
const negWord = /OPPOSED|stall|fights|resistance|against|illiquid|pending/i;
const VLABEL: Record<string, string> = { STOCK: "stock", LONG_OPTION: "long option",
  DEBIT_SPREAD: "debit spread", CREDIT_SPREAD: "credit spread", CALENDAR: "calendar", IRON_CONDOR: "iron condor" };
const vlabel = (v: string) => VLABEL[v] || v;

function sentence(dec: any, plan: TradePlan | null): string {
  const dir = dec.verdict.direction === "UP" ? "long" : "short";
  const tier = (dec.setup?.tier || "Setup").toString();
  const veh = plan?.recommended ? vlabel(plan.recommended.vehicle) : "the recommended vehicle";
  const g = dec.gex?.available
    ? (dec.gex.regime_sign === "NEG" ? "Negative-gamma regime adds breakout fuel"
       : "Positive-gamma regime caps near-term upside (pinning)") : "";
  return `${tier}-led ${dir}. ${g ? g + " — " : ""}play as ${veh}.`;
}

function flagText(dec: any, plan: TradePlan | null): string | null {
  if (!plan?.recommended || !dec.gex?.available) return null;
  const t = parseFloat(plan.recommended.target), cw = dec.gex.call_wall, pw = dec.gex.put_wall;
  if (dec.verdict.direction === "UP" && dec.gex.regime_sign === "POS" && cw && t > cw)
    return `Target ${plan.recommended.target} sits above the ${cw} call wall. In a pinning regime, reaching it needs a decisive breakout from the gamma zone — treat the runner as conditional.`;
  if (dec.verdict.direction === "DOWN" && dec.gex.regime_sign === "POS" && pw && t < pw)
    return `Target ${plan.recommended.target} sits below the ${pw} put wall — pinning support resists; treat the runner as conditional.`;
  return null;
}

export default function Internals() {
  const [tape, setTape] = useState<Tape | null>(null);
  const [hist, setHist] = useState<TapeHistPoint[]>([]);
  const [intra, setIntra] = useState<Intraday | null>(null);
  const [sym, setSym] = useState("");
  const [dec, setDec] = useState<any>(null);
  const [plan, setPlan] = useState<TradePlan | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchTape().then(setTape).catch(() => {});
    fetchTapeHistory(120).then(setHist).catch(() => {});
    fetchIntraday().then(setIntra).catch(() => {});
    const u = new URLSearchParams(window.location.search).get("symbol");
    if (u) { setSym(u); runDecision(u); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runDecision = (s: string) => {
    if (!s) return;
    const S = s.toUpperCase();
    setBusy(true);
    setPlan(null);
    fetchDecision(S).then(setDec).catch(() => setDec(null)).finally(() => setBusy(false));
    fetchTradePlan(S).then(setPlan).catch(() => setPlan(null));
  };

  const cards: { k: keyof TapeHistPoint; label: string; color: string }[] = [
    { k: "tape", label: "Tape Score", color: "var(--accent)" },
    { k: "breadth", label: "Breadth", color: "#26a69a" },
    { k: "volatility", label: "Volatility", color: "#ab7df8" },
    { k: "credit", label: "Credit", color: "#ffb74d" },
  ];

  return (
    <div className="wrap">
      <div className="header">
        <span className="title">📊 Market Internals</span>
        <a href="/" className="btn" style={{ textDecoration: "none" }}>← Screener</a>
        <span className="spacer" />
        {tape && <span className="muted">as of {tape.asof}</span>}
      </div>

      {tape && (
        <div className="tapebar" style={{ height: 70 }}>
          <div className="tape-gauge" style={{ minWidth: 260 }}>
            <div>
              <div className="tape-score-num" style={{ fontSize: 34, color: zone(tape.score) }}>{tape.score}</div>
              <div className="tape-sub">TAPE SCORE</div>
            </div>
            <div>
              <span className="tape-label" style={{ background: zone(tape.score), color: "#06121a" }}>{tape.label}</span>
              <div className="tape-meter" style={{ marginTop: 8, width: 120 }}><i style={{ left: `${tape.score}%` }} /></div>
              <div className="tape-sub" style={{ marginTop: 4 }}>
                breadth {tape.components.breadth.score} · vol {tape.components.volatility.score} · credit {tape.components.credit.score}
              </div>
            </div>
          </div>
          <div className="tape-stream" style={{ alignItems: "center", padding: "0 16px", color: "var(--muted)", fontSize: 12 }}>
            {tape.score >= 60 ? "Risk-ON: broad tape supports longs; setups can be taken at size."
              : tape.score < 40 ? "Risk-OFF: tape deteriorating; favor shorts / stand aside on longs."
              : "NEUTRAL: mixed tape; lean on per-name confluence + catalyst."}
          </div>
        </div>
      )}

      <h3 style={{ margin: "16px 0 8px" }}>History (120d)</h3>
      <div className="tape-cards" style={{ gridTemplateColumns: "repeat(4,1fr)" }}>
        {cards.map((c) => {
          const series = hist.map((h) => h[c.k] as number);
          const cur = series.length ? series[series.length - 1] : 0;
          return (
            <div className="tape-card" key={c.k}>
              <h4>{c.label} <span style={{ color: zone(cur) }}>{cur}</span></h4>
              <Sparkline data={series} color={c.color} width={200} height={40} />
              <div className="interp">{series.length ? `${series[0]} → ${cur} over ${series.length}d` : "…"}</div>
            </div>
          );
        })}
      </div>

      <h3 style={{ margin: "18px 0 8px" }}>Intraday breadth (5m universe proxy)</h3>
      <div className="tape-card" style={{ maxWidth: 720 }}>
        {intra?.available ? (
          <>
            <div className="row"><span>% names up (vs open)</span><span>{Math.round((intra.pct_up || 0) * 100)}%</span></div>
            <div className="row"><span>advancers / decliners</span><span>{intra.adv} / {intra.dec}</span></div>
            <div className="row"><span>$ADD proxy (cum tick)</span><span>{intra.add_proxy}</span></div>
            <div className="row"><span>TRIN proxy</span><span>{intra.trin_proxy ?? "—"} {intra.trin_proxy && intra.trin_proxy < 1 ? "(strong)" : intra.trin_proxy ? "(weak)" : ""}</span></div>
            <div style={{ marginTop: 8, display: "flex", alignItems: "flex-end", gap: 1, height: 40 }}>
              {(intra.tick_series || []).map((p, i) => {
                const max = Math.max(...(intra.tick_series || []).map((x) => Math.abs(x.v)), 1);
                const h = (Math.abs(p.v) / max) * 18;
                return <div key={i} title={`${p.t}: ${p.v}`} style={{ width: 4,
                  height: Math.max(1, h), marginBottom: 20, background: p.v >= 0 ? "var(--up)" : "var(--down)",
                  alignSelf: p.v >= 0 ? "flex-end" : "flex-start" }} />;
              })}
            </div>
            <div className="interp">latest session {intra.asof} · TICK-proxy bars (green up / red down) · label: {intra.label}</div>
          </>
        ) : <span className="muted">intraday proxy unavailable (no recent 5m data)</span>}
      </div>

      <h3 style={{ margin: "18px 0 8px" }}>🎯 Trade Decision</h3>
      <div className="dpanel">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 14 }}>
          <span style={{ fontSize: 16, fontWeight: 600 }}>
            <span style={{ color: "var(--up)", marginRight: 7 }}>◎</span>
            Trade decision{dec?.symbol ? ` — ${dec.symbol}` : ""}
          </span>
          <span style={{ fontSize: 12, color: "var(--text-3)", fontFamily: "var(--mono)" }}>
            {dec?.gex?.spot ? `spot ${dec.gex.spot}` : ""}
          </span>
        </div>
        <div className="controls" style={{ marginBottom: 18 }}>
          <input type="text" placeholder="ticker e.g. EW" value={sym}
                 onChange={(e) => setSym(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && runDecision(sym)} />
          <button className="btn" onClick={() => runDecision(sym)}>{busy ? "…" : "Decide"}</button>
        </div>

        {dec && dec.verdict ? (
          <>
            <div className="vhead">
              <span className={`vcall ${dec.verdict.call.toLowerCase()}`}>{dec.verdict.call}</span>
              <span className="vconv">conviction <b>{dec.verdict.conviction}</b>/100</span>
              <span className={`pill ${dec.verdict.direction === "UP" ? "up" : "down"}`}>{dec.verdict.direction}</span>
            </div>
            <p className="vsentence">{sentence(dec, plan)}</p>
            <div className="dchips">
              {dec.verdict.reasons.map((r: string, i: number) => (
                <span key={i} className={`dchip ${negWord.test(r) ? "neg" : "pos"}`}>{r}</span>
              ))}
              <span className="dchip eq">= {dec.verdict.conviction}</span>
            </div>

            <div className="dsub">Signal</div>
            <div style={{ marginBottom: 22 }}>
              <div className="sigrow"><span className="lab">tape</span>
                <div className="sgauge"><span style={{ width: `${gpct(dec.tape.score)}%`, background: "var(--muted)" }} /></div>
                <span className="val">{dec.tape.score}/100 · {dec.tape.label}</span></div>
              <div className="sigrow"><span className="lab">setup</span>
                <span style={{ flex: 1, color: "var(--up)", fontSize: 12.5 }}>{dec.setup ? `${dec.setup.tier} · ${dec.setup.dir}` : "—"}</span>
                <span className="val">{dec.setup ? `${dec.setup.conf_n}-timeframe` : ""}</span></div>
              <div className="sigrow"><span className="lab">news</span>
                <div className="sgauge"><span style={{ width: `${gpct((dec.news?.count || 0) * 10)}%`,
                  background: dec.news?.sentiment === "positive" ? "var(--up-dim)" : dec.news?.sentiment === "negative" ? "var(--down)" : "var(--muted)" }} /></div>
                <span className="val">{dec.news?.count || 0} · {dec.news?.sentiment}</span></div>
              <div className="sigrow"><span className="lab">intraday</span>
                <div className="sgauge"><span style={{ width: `${gpct(dec.intraday?.score || 0)}%`, background: "var(--muted)" }} /></div>
                <span className="val">{dec.intraday?.score ?? "—"} · {dec.intraday?.label ?? ""}</span></div>
            </div>

            {dec.gex?.available && (
              <>
                <div className="dsub divtop">Structure · dealer gamma</div>
                <div className="dregime"><span className="muted">regime</span>
                  <span><b style={{ color: dec.gex.regime_sign === "NEG" ? "var(--up)" : "var(--amber)" }}>{dec.gex.regime}</b>
                  {" · spot "}{dec.gex.call_wall ? "" : ""}{dec.gex.flip ? `${dec.gex.flip} flip` : ""}</span></div>
                <div className="gexlegend">
                  <span><i className="sw" style={{ background: "var(--down)" }} />dealer short γ — amplifies moves</span>
                  <span><i className="sw" style={{ background: "var(--up-dim)" }} />dealer long γ — dampens moves</span>
                </div>
                <div className="gchart"><GexProfile gex={dec.gex} /></div>
              </>
            )}

            {flagText(dec, plan) && (
              <div className="dflag"><span style={{ color: "var(--amber)", fontSize: 15 }}>⚠</span>
                <p>{flagText(dec, plan)}</p></div>
            )}

            {plan && plan.recommended && (
              <div className="planbox">
                <div className="ph"><span className="l">Recommended · {vlabel(plan.recommended.vehicle)}</span>
                  <span className="r">{plan.direction} · exp {plan.exp}</span></div>
                <table><tbody>
                  <tr><td className="k">entry</td><td className="v">{plan.recommended.entry}</td></tr>
                  <tr><td className="k">target</td><td className="v" style={{ color: "var(--up)" }}>{plan.recommended.target}</td></tr>
                  <tr><td className="k">stop</td><td className="v" style={{ color: "var(--down)" }}>{plan.recommended.stop}</td></tr>
                  <tr><td className="k">risk / reward</td><td className="v">{String(plan.recommended.risk_reward ?? "—")}</td></tr>
                  <tr><td className="k">max loss</td><td className="v">{String(plan.recommended.max_loss ?? "—")}</td></tr>
                </tbody></table>
                <p className="note">{(plan.recommended.why || []).join(" · ")}</p>
              </div>
            )}
            <div className="muted" style={{ marginTop: 10, fontSize: 11 }}>
              full alternatives → <a href={`/plan?symbol=${(dec.symbol || sym).toUpperCase()}`}>Trade Plan</a>
            </div>
          </>
        ) : dec && !dec.verdict ? (
          <>
            <p className="vsentence muted">
              {dec.note || "No current squeeze setup"} — showing dealer-gamma structure only.
            </p>
            {dec.gex?.available ? (
              <>
                <div className="dsub divtop">Structure · dealer gamma</div>
                <div className="dregime"><span className="muted">regime</span>
                  <span><b style={{ color: dec.gex.regime_sign === "NEG" ? "var(--up)" : "var(--amber)" }}>{dec.gex.regime}</b>
                  {dec.gex.flip ? ` · flip ${dec.gex.flip}` : ""}</span></div>
                <div className="gexlegend">
                  <span><i className="sw" style={{ background: "var(--down)" }} />dealer short γ — amplifies moves</span>
                  <span><i className="sw" style={{ background: "var(--up-dim)" }} />dealer long γ — dampens moves</span>
                </div>
                <div className="gchart"><GexProfile gex={dec.gex} /></div>
                <div className="muted" style={{ fontSize: 11 }}>
                  spot {dec.gex.spot} · call wall {dec.gex.call_wall} (resistance) · put wall {dec.gex.put_wall} (support)
                  {dec.gex.net_gex_musd != null ? ` · net GEX ${dec.gex.net_gex_musd}M` : ""}
                </div>
              </>
            ) : <span className="muted">no options / GEX data for this ticker</span>}
          </>
        ) : (
          <span className="muted">Enter a ticker for a GO / CAUTION / AVOID read with reasons.</span>
        )}
      </div>
    </div>
  );
}
