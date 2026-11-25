"""Paper forward-testing via the Tradier sandbox (P9 --paper, for real this time).

    uv run python -m reporting.paper_trade            # PREVIEW (validate, no fills)
    uv run python -m reporting.paper_trade --place    # actually submit PAPER orders

Generates CURRENT recommendations and routes them to the Tradier SANDBOX:
- STOCK (default): daily pullback 'buy-range' + 2h squeeze-only -> equity orders.
- OPTIONS (--with-options, observe-only — tested NEGATIVE): naked call/put -> option orders.

Safety: previews by default; refuses live placement unless the base is the sandbox.
Nothing here is graduated — NOT EVEN the 2h squeeze, which has a real Stage-1 event-study
edge (+8bps/fire, t=3.3) but FAILS the timed-strategy gauntlet (OOS Sharpe < 0, research
~0.75). This is forward-test evidence-gathering on a non-graduated signal, not deployment.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from data.cache import ParquetCache
from data.universe import load_universe
from signals.confluence import ConfluenceParams, generate_squeeze
from signals.pullback import generate_pullback

from .paper_ledger import record
from .tradier import Tradier, TradierError, nearest_option

RECENT = 2            # a signal is "current" if it fired within this many bars
EQUITY_NOTIONAL = 2000.0
OPT_DTE_TARGET = 30
HOLD_DAYS = {"squeeze_2h": 2, "pullback_1d": 5}   # validated holds, for the exit manager


def current_recs(cache, universe):
    """Return current per-name directional recs from the validated stock signals."""
    recs = []
    for sym in universe.tickers:
        d1 = cache.read(sym, "1d")
        d2 = cache.read(sym, "2h")
        if d1 is None or d2 is None or len(d1) < 250:
            continue
        spot = float(d1["close"].iloc[-1])
        # daily pullback buy-range + trend-strength filter (momentum must agree)
        pb = generate_pullback(d1, trend_ma=100, oversold=15.0, trend_mom=63)
        recent = pb.direction.iloc[-RECENT:]
        if (recent != 0).any():
            ts = recent[recent != 0].index[-1]
            recs.append({"symbol": sym, "dir": int(pb.direction.loc[ts]), "source": "pullback_1d",
                         "conviction": round(float(pb.conviction.loc[ts]), 2),
                         "signal_date": ts.date(), "spot": spot})
            continue
        # 2h squeeze-only (fire within the last few 2h bars)
        sq = generate_squeeze(d2, ConfluenceParams(length=13, confluence_window=4))
        rec2 = sq.direction.iloc[-3:]
        if (rec2 != 0).any():
            ts = rec2[rec2 != 0].index[-1]
            recs.append({"symbol": sym, "dir": int(sq.direction.loc[ts]), "source": "squeeze_2h",
                         "conviction": round(float(sq.conviction.loc[ts]), 2),
                         "signal_date": ts.date(), "spot": spot})
    return recs


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    ap = argparse.ArgumentParser(description="Tradier sandbox paper forward-test")
    ap.add_argument("--place", action="store_true", help="submit (default: preview only)")
    ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--with-options", action="store_true",
                    help="ALSO place observe-only option legs (default: equity-only)")
    args = ap.parse_args(argv)
    preview = not args.place

    universe = load_universe("broad")
    cache = ParquetCache()
    recs = current_recs(cache, universe)[: args.max]
    asof = max(cache.read("SPY", "1d").index[-1], cache.read("SPY", "2h").index[-1])
    print(f"=== PAPER FORWARD-TEST (Tradier sandbox) — as of {asof.date()} ===")
    print(f"mode: {'PREVIEW (validate only)' if preview else 'PLACE paper orders'} | "
          f"{len(recs)} current recs\n")
    if not recs:
        print("(no current signals)")
        return 0
    print(pd.DataFrame(recs).assign(dir=lambda x: x["dir"].map({1: "LONG", -1: "SHORT"})).to_string(index=False))

    try:
        tr = Tradier()
    except TradierError as e:
        print(f"\n[Tradier not configured] {e}\n-> add sandbox creds to .env to place paper orders.")
        return 0
    if not tr.is_sandbox:
        print("\nREFUSING: TRADIER_BASE is not the sandbox. Aborting (paper only).")
        return 1
    bal = tr.balances().get("balances", {})
    print(f"\nsandbox account {tr.account}: total equity ${bal.get('total_equity', '?')}\n")

    print("--- STOCK orders (validated edges) ---")
    for r in recs:
        side = "buy" if r["dir"] > 0 else "sell_short"
        qty = max(1, int(EQUITY_NOTIONAL / r["spot"]))
        try:
            resp = tr.equity_order(r["symbol"], side, qty, preview=preview)
            o = resp.get("order", resp)
            print(f"  {r['symbol']:5} {side:10} x{qty:<4} -> {o.get('status', o)}")
            if not preview and o.get("id"):
                record({"kind": "entry", "ts": str(asof.date()), "order_id": o["id"],
                        "symbol": r["symbol"], "asset": "equity", "side": side, "qty": qty,
                        "source": r["source"], "hold_days": HOLD_DAYS.get(r["source"], 5),
                        "conviction": r["conviction"], "ref_price": r["spot"]})
        except TradierError as e:
            print(f"  {r['symbol']:5} {side:10} -> ERROR {e}")

    if args.with_options:
        print("\n--- OPTIONS orders (OBSERVE-ONLY — tested negative) ---")
        for r in recs:
            sym, kind = r["symbol"], ("call" if r["dir"] > 0 else "put")
            try:
                exps = tr.expirations(sym)
                if not exps:
                    print(f"  {sym}: no expirations")
                    continue
                target = pd.Timestamp(asof.date()) + pd.Timedelta(days=OPT_DTE_TARGET)
                exp = min(exps, key=lambda e: abs((pd.Timestamp(e) - target).days))
                opt = nearest_option(tr.chain(sym, exp), r["spot"], kind)  # ~ATM
                if opt is None:
                    print(f"  {sym}: no {kind}s for {exp}")
                    continue
                resp = tr.option_order(sym, opt["symbol"], "buy_to_open", 1, preview=preview)
                o = resp.get("order", resp)
                print(f"  {sym:5} buy {kind:4} {opt['symbol']} (exp {exp}, K{opt['strike']}) "
                      f"-> {o.get('status', o)}")
                if not preview and o.get("id"):
                    record({"kind": "entry", "ts": str(asof.date()), "order_id": o["id"],
                            "symbol": sym, "asset": "option", "side": "buy_to_open", "qty": 1,
                            "option_symbol": opt["symbol"], "source": r["source"],
                            "hold_days": HOLD_DAYS.get(r["source"], 5), "conviction": r["conviction"],
                            "ref_price": r["spot"]})
            except TradierError as e:
                print(f"  {sym}: option ERROR {e}")

    print(f"\n{'PREVIEW only — re-run with --place to submit paper orders.' if preview else 'Paper orders submitted to sandbox.'}")
    print("Reminder: forward-test evidence only; no signal graduated; options tested negative.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
