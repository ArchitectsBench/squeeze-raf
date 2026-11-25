"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import ChartModal from "./components/ChartModal";
import TapeBar from "./components/TapeBar";
import { Cell, Matrix, Sentiment, Ticker, Verdict, fetchDecisions, fetchMatrix, fetchNewsSummary } from "./lib/api";

const POLL_MS = 30000;
const CALL_COLOR: Record<string, string> = { GO: "var(--up)", AVOID: "var(--down)", CAUTION: "#ffb74d" };
const SENT_COLOR: Record<Sentiment, string> = {
  positive: "var(--up)", negative: "var(--down)", neutral: "var(--muted)",
};

function ConfBar({ t }: { t: Ticker }) {
  const pct = Math.min(100, (t.conf_score / 4) * 100);
  const col = t.conf_dir === "UP" ? "var(--up)" : "var(--down)";
  return (
    <div className="conf">
      <div className="label">{t.conf_dir === "UP" ? "▲" : "▼"} {t.conf_score} · {t.conf_n}tf</div>
      <div className="confbar"><div style={{ width: `${pct}%`, background: col }} /></div>
    </div>
  );
}

function CellView({ c, onClick }: { c?: Cell; onClick?: () => void }) {
  // empty cells are STILL clickable — chart data exists for every symbol/tf even
  // when no setup is active (was: dead cells read as "chart not opening")
  if (!c) return <div className="cell empty" onClick={onClick}
                      style={{ cursor: "pointer" }} title="open chart">·</div>;
  const dir = c.dir === "UP" ? "up" : "down";
  const tier = c.tier === "ALERT" ? "alert" : "watch";
  return (
    <div className={`cell ${dir} ${tier}`} onClick={onClick}
         title={`${c.tier} ${c.dir} · state ${c.state} · coil ${c.coil} · mom ${c.mom} · $${c.price}`}>
      <span className="mk">{c.tier === "ALERT" ? "●" : "○"}{c.dir === "UP" ? "▲" : "▼"}</span>
      <span className="meta">c{c.coil}</span>
    </div>
  );
}

export default function Page() {
  const [mx, setMx] = useState<Matrix | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [tier, setTier] = useState<"ALL" | "ALERT" | "WATCH">("ALL");
  const [dir, setDir] = useState<"ALL" | "UP" | "DOWN">("ALL");
  const [minConf, setMinConf] = useState(0);
  const [sel, setSel] = useState<{ symbol: string; tf: string } | null>(null);
  const [news, setNews] = useState<Record<string, { count: number; sentiment: Sentiment }>>({});
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  // both default ON: opposed-news and AVOID are the backtest-confirmed loser buckets
  const [hideOpposed, setHideOpposed] = useState(true);
  const [hideAvoid, setHideAvoid] = useState(true);

  // OPPOSED = news sentiment runs against the ticker's confluence direction
  // (backtest: these lose significantly). nothing to oppose if neutral / no news.
  const isOpposed = (t: Ticker): boolean => {
    const s = news[t.symbol]?.sentiment;
    if (!s || s === "neutral") return false;
    return (t.conf_dir === "UP" && s === "negative") || (t.conf_dir === "DOWN" && s === "positive");
  };

  const load = useCallback(async (refresh = false) => {
    try {
      const m = await fetchMatrix(refresh);
      setMx(m);
      setErr(null);
      const ns = await fetchNewsSummary(m.tickers.map((t) => t.symbol));
      setNews(ns);
      fetchDecisions().then((d) => d && setVerdicts(d.verdicts)).catch(() => {});
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(() => load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  // verdict-gated ordering (backtest: GO 55.7% win vs AVOID 45.1% — rank-orders outcomes)
  const VERDICT_RANK: Record<string, number> = { GO: 0, CAUTION: 1, AVOID: 3 };
  const rows = useMemo(() => {
    if (!mx) return [];
    const filtered = mx.tickers.filter((t) => {
      if (q && !t.symbol.toLowerCase().includes(q.toLowerCase())) return false;
      if (t.conf_score < minConf) return false;
      if (hideOpposed && isOpposed(t)) return false;
      if (hideAvoid && verdicts[t.symbol]?.call === "AVOID") return false;
      const cells = Object.values(t.cells);
      if (tier !== "ALL" && !cells.some((c) => c.tier === tier)) return false;
      if (dir !== "ALL" && !cells.some((c) => c.dir === dir)) return false;
      return true;
    });
    // confluence is display-only (failed its pre-registered backtest gate 2026-07-02)
    // — rank by verdict + conviction alone
    return filtered.sort((a, b) => {
      const va = verdicts[a.symbol], vb = verdicts[b.symbol];
      const ra = va ? VERDICT_RANK[va.call] : 2, rb = vb ? VERDICT_RANK[vb.call] : 2;
      if (ra !== rb) return ra - rb;
      if ((vb?.conviction ?? 0) !== (va?.conviction ?? 0)) return (vb?.conviction ?? 0) - (va?.conviction ?? 0);
      return a.symbol.localeCompare(b.symbol);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mx, q, tier, dir, minConf, hideOpposed, hideAvoid, news, verdicts]);

  return (
    <div className="wrap">
      <TapeBar />
      <div className="header">
        <span className="title">⚡ Squeeze Confluence Matrix</span>
        <a href="/internals" className="btn" style={{ textDecoration: "none" }}>📊 Internals</a>
        <a href="/trades" className="btn" style={{ textDecoration: "none" }}>💼 Trades</a>
        {mx && <span className="muted">{rows.length}/{mx.tickers.length} tickers · computed {mx.computed_at}</span>}
        {err && <span style={{ color: "var(--down)" }}>API error: {err} — is the backend on :8000?</span>}
        <span className="spacer" />
        <div className="controls">
          <input type="text" placeholder="search…" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="toggle">
            {(["ALL", "ALERT", "WATCH"] as const).map((t) => (
              <button key={t} className={tier === t ? "active" : ""} onClick={() => setTier(t)}>{t}</button>
            ))}
          </div>
          <div className="toggle">
            {(["ALL", "UP", "DOWN"] as const).map((d) => (
              <button key={d} className={dir === d ? "active" : ""} onClick={() => setDir(d)}>{d}</button>
            ))}
          </div>
          <label className="muted">min conf {minConf}
            <input type="range" min={0} max={4} step={0.5} value={minConf}
                   onChange={(e) => setMinConf(Number(e.target.value))} />
          </label>
          <button className="btn" onClick={() => setHideOpposed((v) => !v)}
                  title="Hide setups whose news sentiment opposes the trade direction (backtest: these lose)"
                  style={hideOpposed ? { background: "var(--accent)", color: "#fff", borderColor: "var(--accent)" } : {}}>
            🚫 {hideOpposed ? "opposed hidden" : "hide opposed"}
          </button>
          <button className="btn" onClick={() => setHideAvoid((v) => !v)}
                  title="Hide AVOID-verdict setups (backtest: 45.1% win, −40bps — the loser bucket)"
                  style={hideAvoid ? { background: "var(--accent)", color: "#fff", borderColor: "var(--accent)" } : {}}>
            ⛔ {hideAvoid ? "AVOID hidden" : "hide AVOID"}
          </button>
          <button className="btn" onClick={() => load(true)}>↻ refresh</button>
        </div>
      </div>

      <div style={{ overflow: "auto", maxHeight: "calc(100vh - 130px)" }}>
        <table className="matrix">
          <thead>
            <tr>
              <th className="sym">Ticker</th>
              <th>Verdict</th>
              <th>Conf</th>
              <th>News</th>
              {mx?.timeframes.map((tf) => (
                <th className="tf" key={tf}>{tf}<br /><span style={{ fontSize: 9 }}>{mx.asof[tf]?.slice(5)}</span></th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.symbol}
                  style={verdicts[t.symbol]?.call === "AVOID" ? { opacity: 0.45 } : {}}>
                <td className="sym" onClick={() => setSel({ symbol: t.symbol, tf: "1d" })}
                    style={{ cursor: "pointer" }} title="open 1d chart">
                  <b>{t.symbol}</b><br />
                  <span className="px">${Object.values(t.cells)[0]?.price}</span>
                </td>
                <td>
                  {verdicts[t.symbol] ? (
                    <a href={`/internals?symbol=${t.symbol}`} style={{ textDecoration: "none", cursor: "pointer" }}
                       title={`conviction ${verdicts[t.symbol].conviction}/100 — ${verdicts[t.symbol].reasons.join("; ")}`}>
                      <span style={{ fontWeight: 700, fontSize: 11, padding: "2px 6px", borderRadius: 4,
                        background: CALL_COLOR[verdicts[t.symbol].call], color: "#06121a" }}>
                        {verdicts[t.symbol].call}
                      </span>
                    </a>
                  ) : <span className="muted">·</span>}
                </td>
                <td><ConfBar t={t} /></td>
                <td>
                  {news[t.symbol] && news[t.symbol].count > 0 ? (
                    <span title={`${news[t.symbol].count} recent headlines · ${news[t.symbol].sentiment}`}
                          style={{ cursor: "pointer" }}
                          onClick={() => setSel({ symbol: t.symbol, tf: "1d" })}>
                      <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4,
                        background: SENT_COLOR[news[t.symbol].sentiment], marginRight: 4 }} />
                      📰{news[t.symbol].count}
                    </span>
                  ) : <span className="muted">·</span>}
                </td>
                {mx!.timeframes.map((tf) => (
                  <td key={tf}>
                    <CellView c={t.cells[tf]} onClick={() => setSel({ symbol: t.symbol, tf })} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="legend">
        <span><span className="swatch" style={{ background: "var(--up)" }} />●▲ ALERT up (trend+momentum)</span>
        <span><span className="swatch" style={{ background: "var(--up-dim)" }} />○▲ WATCH up (coil, momentum pending)</span>
        <span><span className="swatch" style={{ background: "var(--down)" }} />●▼ ALERT down</span>
        <span><span className="swatch" style={{ background: "var(--down-dim)" }} />○▼ WATCH down</span>
        <span>c# = coil length · click a cell for the chart</span>
      </div>

      {sel && <ChartModal symbol={sel.symbol} tf={sel.tf} onClose={() => setSel(null)} />}
    </div>
  );
}
