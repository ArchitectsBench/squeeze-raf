"use client";
import { useEffect, useState } from "react";
import { Tape, fetchTape } from "../lib/api";
import TapeDetail from "./TapeDetail";

const zoneColor = (s: number) => (s >= 60 ? "var(--up)" : s < 40 ? "var(--down)" : "#ffb74d");

export default function TapeBar() {
  const [t, setT] = useState<Tape | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const load = () => fetchTape().then(setT).catch(() => {});
    load();
    const id = setInterval(load, 300000); // 5 min
    return () => clearInterval(id);
  }, []);

  if (!t) {
    return <div className="tapebar"><div className="tape-gauge"><span className="tape-sub">loading internals…</span></div></div>;
  }
  const col = zoneColor(t.score);
  const { breadth: b, volatility: v, credit: c } = t.components;
  const chips: { l: string; v: string; cls: string }[] = [
    { l: "%>200MA", v: `${Math.round(b.pct_above_200 * 100)}%`, cls: b.pct_above_200 > 0.5 ? "up" : "down" },
    { l: "%>50MA", v: `${Math.round(b.pct_above_50 * 100)}%`, cls: b.pct_above_50 > 0.5 ? "up" : "down" },
    { l: "McClellan", v: b.mcclellan_osc.toFixed(0), cls: b.mcclellan_osc > 0 ? "up" : "down" },
    { l: "A/D", v: b.ad_slope > 0 ? "rising" : "falling", cls: b.ad_slope > 0 ? "up" : "down" },
    { l: "new hi−lo", v: `${(b.net_high_low * 100).toFixed(1)}%`, cls: b.net_high_low >= 0 ? "up" : "down" },
    { l: "VIX", v: v.vix.toFixed(1), cls: v.vix < 20 ? "up" : "down" },
    { l: "VIX term", v: `${v.term_ratio.toFixed(2)} ${v.regime.includes("contango") ? "ctgo" : "bckwn"}`, cls: v.term_ratio < 1 ? "up" : "down" },
    { l: "HY spread", v: `${c.hy_spread.toFixed(2)}${c.hy_rising_20d ? " ↑" : " ↓"}`, cls: c.hy_rising_20d ? "down" : "up" },
    { l: "2s10s", v: c.curve_2s10s.toFixed(2), cls: c.curve_2s10s > 0 ? "up" : "down" },
  ];
  const stream = [...chips, ...chips]; // duplicated for seamless scroll
  return (
    <>
      <div className="tapebar">
        <div className="tape-gauge" onClick={() => setOpen(true)} title="Market internals — click for detail">
          <div>
            <div className="tape-score-num" style={{ color: col }}>{t.score}</div>
            <div className="tape-sub">as of {t.asof}</div>
          </div>
          <div>
            <span className="tape-label" style={{ background: col, color: "#06121a" }}>{t.label}</span>
            <div className="tape-meter" style={{ marginTop: 6 }}>
              <i style={{ left: `${Math.min(100, Math.max(0, t.score))}%` }} />
            </div>
          </div>
        </div>
        <div className="tape-stream">
          <div className="tape-track">
            {stream.map((ch, i) => (
              <span className="tape-chip" key={i}><b>{ch.l}</b><span className={ch.cls}>{ch.v}</span></span>
            ))}
          </div>
        </div>
      </div>
      {open && <TapeDetail tape={t} onClose={() => setOpen(false)} />}
    </>
  );
}
