"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { TradeRow, Trades, fetchTrades } from "../lib/api";

const POLL_MS = 60000;

const fmt = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined ? "·" : v.toFixed(d);
const pnlColor = (v?: number) =>
  v === undefined ? "var(--muted)" : v >= 0 ? "var(--up)" : "var(--down)";

function Tile({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ padding: "10px 16px", border: "1px solid var(--border, #223)",
                  borderRadius: 8, minWidth: 130 }}>
      <div className="muted" style={{ fontSize: 11 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: color || "inherit",
                    fontVariantNumeric: "tabular-nums" }}>{value}</div>
    </div>
  );
}

function Table({ rows, open }: { rows: TradeRow[]; open: boolean }) {
  if (!rows.length) return <p className="muted">(none)</p>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="matrix" style={{ fontVariantNumeric: "tabular-nums" }}>
        <thead>
          <tr>
            <th className="sym">Symbol</th><th>Source</th><th>Side</th><th>Qty</th>
            <th>Fill</th><th>{open ? "Now" : "Exit"}</th><th>P&L $</th><th>P&L %</th>
            {open && <th>TP</th>}{open && <th>SL</th>}
            <th>{open ? "Opened / hold" : "Opened"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const basis = r.fill * r.qty * (r.asset.startsWith("option") ? 100 : 1);
            const pct = r["pnl$"] !== undefined && basis > 0 ? (r["pnl$"]! / basis) * 100 : undefined;
            return (
              <tr key={r.id}>
                <td className="sym"><b>{r.symbol}</b><br />
                  <span className="px">{r.asset}</span></td>
                <td>{r.src ?? "·"}</td>
                <td style={{ color: r.side.includes("sell") || r.side.includes("short")
                             ? "var(--down)" : "var(--up)" }}>{r.side}</td>
                <td>{r.qty}</td>
                <td>${fmt(r.fill)}</td>
                <td>{r.cur !== undefined ? `$${fmt(r.cur)}` : r.state}</td>
                <td style={{ color: pnlColor(r["pnl$"]), fontWeight: 700 }}>
                  {r["pnl$"] !== undefined ? `${r["pnl$"]! >= 0 ? "+" : ""}${fmt(r["pnl$"])}` : "·"}
                </td>
                <td style={{ color: pnlColor(r["pnl$"]) }}>
                  {pct !== undefined ? `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%` : "·"}
                </td>
                {open && <td>{r.tp ? `$${fmt(r.tp)}` : "·"}</td>}
                {open && <td style={{ color: r.sl ? "var(--down)" : undefined }}>
                  {r.sl ? `$${fmt(r.sl)}` : "⚠ none"}</td>}
                <td className="muted">{r.ts ?? "·"}{open && r.hold_days ? ` / ${r.hold_days}d` : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function TradesPage() {
  const [t, setT] = useState<Trades | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    try {
      setT(await fetchTrades(refresh));
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);
  useEffect(() => {
    load();
    const id = setInterval(() => load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const open = useMemo(() => (t?.rows ?? []).filter((r) => !r.realized && r.state !== "CLOSED"
    && r["pnl$"] !== undefined), [t]);
  const pending = useMemo(() => (t?.rows ?? []).filter((r) => !r.realized && r.state !== "CLOSED"
    && r["pnl$"] === undefined), [t]);
  const closed = useMemo(() => (t?.rows ?? []).filter((r) => r.realized || r.state === "CLOSED"), [t]);

  return (
    <div className="wrap">
      <div className="header">
        <span className="title">💼 Paper Trades</span>
        <a href="/" className="btn" style={{ textDecoration: "none" }}>⚡ Matrix</a>
        <a href="/internals" className="btn" style={{ textDecoration: "none" }}>📊 Internals</a>
        {t && <span className="muted">
          {t.sandbox ? "sandbox" : "LIVE"} {t.account} · computed {t.computed_at}</span>}
        {err && <span style={{ color: "var(--down)" }}>API error: {err}</span>}
        <span className="spacer" />
        <button className="btn" onClick={() => load(true)}>↻ refresh</button>
      </div>

      {t && (
        <div style={{ display: "flex", gap: 12, margin: "12px 0", flexWrap: "wrap" }}>
          <Tile label="OPEN POSITIONS" value={String(open.length)} />
          <Tile label="UNREALIZED" value={`${t.unrealized >= 0 ? "+" : ""}$${t.unrealized.toFixed(2)}`}
                color={pnlColor(t.unrealized)} />
          <Tile label="REALIZED" value={`${t.realized >= 0 ? "+" : ""}$${t.realized.toFixed(2)}`}
                color={pnlColor(t.realized)} />
          <Tile label="TOTAL" value={`${t.total >= 0 ? "+" : ""}$${t.total.toFixed(2)}`}
                color={pnlColor(t.total)} />
        </div>
      )}

      <h3 style={{ margin: "14px 0 6px" }}>Open</h3>
      <Table rows={open} open={true} />
      {pending.length > 0 && (<>
        <h3 style={{ margin: "14px 0 6px" }}>Pending / unfilled</h3>
        <Table rows={pending} open={true} />
      </>)}
      <h3 style={{ margin: "18px 0 6px" }}>Closed (realized)</h3>
      <Table rows={closed} open={false} />
      <p className="muted" style={{ marginTop: 10 }}>
        Ledger-scoped: only orders this system placed. ⚠ none in SL = no bracket recorded.
      </p>
    </div>
  );
}
