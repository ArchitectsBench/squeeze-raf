"use client";
import { Tape } from "../lib/api";

const scoreCol = (s: number) => (s >= 60 ? "var(--up)" : s < 40 ? "var(--down)" : "#ffb74d");

export default function TapeDetail({ tape, onClose }: { tape: Tape; onClose: () => void }) {
  const { breadth: b, volatility: v, credit: c } = tape.components;
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="btn close" onClick={onClose}>✕</button>
        <h3>
          Market Internals — Tape {tape.score}{" "}
          <span className="muted">({tape.label} · as of {tape.asof})</span>
        </h3>
        <div className="tape-cards">
          <div className="tape-card">
            <h4>Breadth <span style={{ color: scoreCol(b.score) }}>{b.score}</span></h4>
            <div className="row"><span>% above 200-MA</span><span>{Math.round(b.pct_above_200 * 100)}%</span></div>
            <div className="row"><span>% above 50-MA</span><span>{Math.round(b.pct_above_50 * 100)}%</span></div>
            <div className="row"><span>McClellan Osc</span><span>{b.mcclellan_osc}</span></div>
            <div className="row"><span>A/D slope (10d)</span><span>{b.ad_slope > 0 ? "rising" : "falling"}</span></div>
            <div className="row"><span>net new hi−lo</span><span>{(b.net_high_low * 100).toFixed(1)}%</span></div>
            <div className="interp">
              Participation {b.pct_above_200 > 0.6 ? "broad / healthy" : b.pct_above_200 < 0.4 ? "narrow / weak" : "mixed"}.
            </div>
          </div>
          <div className="tape-card">
            <h4>Volatility <span style={{ color: scoreCol(v.score) }}>{v.score}</span></h4>
            <div className="row"><span>VIX</span><span>{v.vix}</span></div>
            <div className="row"><span>VIX 3-month</span><span>{v.vix3m}</span></div>
            <div className="row"><span>term ratio</span><span>{v.term_ratio}</span></div>
            <div className="row"><span>regime</span><span>{v.regime}</span></div>
            <div className="interp">{v.term_ratio < 1 ? "Contango → calm / risk-on." : "Backwardation → stress / risk-off."}</div>
          </div>
          <div className="tape-card">
            <h4>Credit / macro <span style={{ color: scoreCol(c.score) }}>{c.score}</span></h4>
            <div className="row"><span>HY spread (OAS)</span><span>{c.hy_spread}%</span></div>
            <div className="row"><span>HY 20-day</span><span>{c.hy_rising_20d ? "widening ↑" : "tightening ↓"}</span></div>
            <div className="row"><span>2s10s curve</span><span>{c.curve_2s10s}</span></div>
            <div className="interp">{c.hy_rising_20d ? "Spreads widening → de-risking." : "Spreads tight → risk-on."}</div>
          </div>
        </div>
        <div className="muted" style={{ marginTop: 10, fontSize: 11 }}>
          Tape = {Math.round(tape.weights.breadth * 100)}% breadth + {Math.round(tape.weights.volatility * 100)}% vol
          + {Math.round(tape.weights.credit * 100)}% credit. Display-only context — does not change setup logic.
        </div>
      </div>
    </div>
  );
}
