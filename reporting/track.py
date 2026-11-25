"""Track ONLY our paper orders (shared-account safe).

    uv run python -m reporting.track

Reads the local ledger of order ids WE placed, fetches each order by id (read-only),
and computes per-order P&L from OUR fill price + a live quote. Closed positions (those
with a recorded 'exit') are marked REALIZED at the exit fill; open ones are marked to
the live quote. Multileg debit spreads are valued from both legs. It never lists all
account orders, never reads the commingled positions endpoint, and never cancels or
modifies anything. Appends a dated aggregate snapshot to data/paper_pnl.jsonl.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from .broker_state import closing_fill
from .paper_ledger import child, entries
from .tradier import Tradier

SNAP = Path(__file__).resolve().parent.parent / "data" / "paper_pnl.jsonl"
_SIGN = {"buy": 1, "buy_to_open": 1, "buy_to_cover": 1, "debit_call_vertical": 1,
         "sell": -1, "sell_short": -1}


def _quote_price(tr, symbol) -> float | None:
    q = tr.quote(symbol).get("quotes", {}).get("quote")
    if not q:
        return None
    q = q[0] if isinstance(q, list) else q
    bid, ask, last = q.get("bid"), q.get("ask"), q.get("last")
    if bid and ask and bid > 0 and ask > 0:
        return (float(bid) + float(ask)) / 2.0
    return float(last) if last else None


def _spread_value(tr, e) -> float | None:
    lo = _quote_price(tr, e["long_leg"])
    sh = _quote_price(tr, e["short_leg"])
    return None if lo is None or sh is None else lo - sh


def position_rows(tr: Tradier) -> list[dict]:
    """Per-position P&L rows (shared by the CLI report and the web /api/trades)."""
    rows = []
    for e in entries():
        oid = e["order_id"]
        asset = e.get("asset")
        try:
            o = tr._get(f"/v1/accounts/{tr.account}/orders/{oid}").get("order", {})
        except Exception as ex:
            rows.append({"id": oid, "symbol": e["symbol"], "status": f"err:{ex}"})
            continue
        side = e.get("side") or o.get("side")
        # spreads: Tradier reports multileg quantity as the leg sum (2); use our 1 spread
        qty = float(e["qty"]) if asset == "option_spread" else (
            float(o.get("quantity", e.get("qty", 0))) or float(e.get("qty", 0)))
        fill = float(o.get("avg_fill_price") or 0.0)
        mult = 100.0 if asset in ("option", "option_spread") else 1.0
        # a close by the resting stop / OCO bracket counts as realized, same as a
        # time/signal exit (closing_fill scans BOTH 'exit' and 'stop' children)
        exit_fill = closing_fill(tr, tr.account, oid)
        closed = exit_fill is not None
        st = child(oid, "stop")
        row = {"id": oid, "symbol": e["symbol"], "asset": asset, "src": e.get("source"),
               "conv": e.get("conviction"), "side": side, "qty": qty,
               "state": "CLOSED" if closed else o.get("status"), "fill": round(fill, 2),
               "ts": e.get("ts"), "hold_days": e.get("hold_days"),
               "tp": (st or {}).get("tp"), "sl": (st or {}).get("sl") or (st or {}).get("stop_price")}
        if fill > 0 or asset == "option_spread":
            if asset == "option_spread":
                fill = fill or float(e.get("net_debit", 0))
                # multileg exits report net as a negative number -> flip to net received
                cur = (-exit_fill) if closed else _spread_value(tr, e)
            else:
                cur = exit_fill if closed else _quote_price(
                    tr, e.get("option_symbol") if asset == "option" else e["symbol"])
            if cur is not None and fill > 0:
                pnl = _SIGN.get(side, 1) * (cur - fill) * qty * mult
                row["fill"] = round(fill, 2)
                row["cur"] = round(cur, 2)
                row["pnl$"] = round(pnl, 2)
                row["realized"] = closed
        rows.append(row)
    return rows


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    if not entries():
        print("ledger empty — no orders placed by this system yet.")
        return 0
    tr = Tradier()
    rows = position_rows(tr)
    print(f"=== PAPER P&L — OUR {len(rows)} positions only (sandbox {tr.account}) ===")
    df = pd.DataFrame(rows).drop(columns=["ts", "hold_days", "tp", "sl"], errors="ignore")
    groups = [("STOCK (validated edge)", "equity"),
              ("OPTIONS (observe-only)", "option"),
              ("SPREADS (momentum)", "option_spread")]
    for title, a in groups:
        g = df[df["asset"] == a]
        if not g.empty:
            print(f"\n--- {title} ---")
            print(g.to_string(index=False))

    has = "pnl$" in df
    realized = float(df[df.get("realized") == True]["pnl$"].sum()) if has else 0.0   # noqa: E712
    openpos = df[df.get("realized") != True] if has else df.iloc[0:0]                # noqa: E712
    unreal = float(openpos["pnl$"].sum()) if has else 0.0
    n_open = int(openpos["pnl$"].notna().sum()) if has else 0
    n_closed = int((df.get("realized") == True).sum()) if has else 0                 # noqa: E712
    print(f"\nrealized (closed {n_closed}): ${realized:+.2f} | "
          f"unrealized (open {n_open}): ${unreal:+.2f} | TOTAL ${realized + unreal:+.2f}")

    SNAP.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAP, "a") as f:
        f.write(json.dumps({"date": str(pd.Timestamp.now(tz="UTC").date()),
                            "n_orders": len(df), "realized": round(realized, 2),
                            "unrealized": round(unreal, 2),
                            "total": round(realized + unreal, 2)}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
