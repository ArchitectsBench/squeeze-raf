"use client";
import { useEffect, useState } from "react";
import { TradePlan, fetchTradePlan } from "../lib/api";
import GexProfile from "./GexProfile";

const LABEL: Record<string, string> = {
  STOCK: "Stock", LONG_OPTION: "Long option", DEBIT_SPREAD: "Debit spread",
  CREDIT_SPREAD: "Credit spread", CALENDAR: "Calendar", IRON_CONDOR: "Iron condor",
};
const label = (v: string) => LABEL[v] || v;
const cost = (s: any) =>
  s.net_debit != null ? `$${s.net_debit} dr`
  : s.net_credit != null ? `$${s.net_credit} cr`
  : s.cost_per_contract != null ? `$${s.cost_per_contract}` : "—";

export default function TradePlanView({ symbol, compact = false }: { symbol: string; compact?: boolean }) {
  const [p, setP] = useState<TradePlan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!symbol) return;
    setP(null); setErr(null);
    fetchTradePlan(symbol).then(setP).catch((e) => setErr(String(e)));
  }, [symbol]);

  if (err) return <div className="muted">trade plan error: {err}</div>;
  if (!p) return <div className="muted">building trade plan…</div>;
  if (p.error) return <div className="muted">{p.error}</div>;
  const r = p.recommended || {};
  const i = p.inputs || {};

  const recCard = (
    <div className="tape-card" style={{ maxWidth: 760 }}>
      <h4>Recommended: <span style={{ color: "var(--accent)" }}>{label(r.vehicle)}</span>
        <span className="muted" style={{ fontWeight: 400 }}>{p.direction} · exp {p.exp}</span></h4>
      {r.legs && <div style={{ fontSize: 13, margin: "2px 0 6px" }}>{r.legs}</div>}
      <div className="row"><span>entry</span><span>{r.entry ?? "—"}</span></div>
      <div className="row"><span>target</span><span>{r.target ?? "—"}</span></div>
      <div className="row"><span>stop</span><span>{r.stop ?? "—"}</span></div>
      <div className="row"><span>risk / reward</span><span>{String(r.risk_reward ?? "—")}</span></div>
      <div className="row"><span>max loss</span><span>{String(r.max_loss ?? "—")}</span></div>
      {r.max_profit != null && <div className="row"><span>max profit</span><span>{r.max_profit}</span></div>}
      {r.breakeven != null && <div className="row"><span>breakeven</span><span>{r.breakeven}</span></div>}
      {r.p_profit_est != null && <div className="row"><span>P(profit) est</span><span>{Math.round(r.p_profit_est * 100)}%</span></div>}
      <ul style={{ fontSize: 12, margin: "6px 0 0", paddingLeft: 18 }}>
        {(r.why || []).map((w: string, k: number) => <li key={k}>{w}</li>)}
      </ul>
    </div>
  );

  if (compact) return recCard;

  return (
    <div>
      {recCard}
      <h4 style={{ margin: "14px 0 4px" }}>Inputs</h4>
      <div className="muted" style={{ fontSize: 12 }}>
        spot {i.spot} · IV {i.iv} · HV {i.hv} · IV/HV {i.iv_hv} · term {i.term_ratio} · exp-move ±{i.exp_move}
        · DTE {i.dte} · earnings {i.earnings_in_days ?? "—"}d {i.earnings_in_window ? "(in window ⚠)" : ""}
      </div>
      {p.gex?.available && (
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          dealer gamma: <b style={{ color: p.gex.regime_sign === "NEG" ? "var(--up)" : "#ffb74d" }}>
          {p.gex.regime_sign === "NEG" ? "NEGATIVE (amplify — squeeze tailwind)" : "POSITIVE (pinning — may stall)"}</b>
          {" · call wall (resistance/target) "}{p.gex.call_wall} ({p.gex.call_wall_dist_pct}%)
          {" · put wall (support) "}{p.gex.put_wall} ({p.gex.put_wall_dist_pct}%)
        </div>
      )}
      {p.gex?.available && p.gex.profile && (
        <div style={{ marginTop: 8, maxWidth: 560 }}><GexProfile gex={p.gex} /></div>
      )}
      <h4 style={{ margin: "14px 0 4px" }}>Alternatives</h4>
      <table className="matrix" style={{ maxWidth: 820 }}>
        <thead><tr>
          <th className="sym">vehicle</th><th style={{ minWidth: 220 }}>structure</th>
          <th>cost</th><th>max loss</th><th>max profit</th><th>R/R</th>
        </tr></thead>
        <tbody>
          {(p.alternatives || []).map((a, idx) => (
            <tr key={idx}>
              <td className="sym">{label(a.vehicle)}</td>
              <td style={{ textAlign: "left", padding: "4px 8px" }}>{a.legs || "—"}</td>
              <td>{cost(a)}</td><td>{String(a.max_loss ?? "—")}</td>
              <td>{String(a.max_profit ?? "—")}</td><td>{String(a.risk_reward ?? "—")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="muted" style={{ marginTop: 10, fontSize: 11 }}>
        Decision-support from delayed (sandbox) chain data — verify live before trading. Vehicle selection
        not yet backtested (P8: options overlays lost on small squeeze moves).
      </div>
    </div>
  );
}
