"""Exit manager for OUR paper positions (shared-account safe).

    uv run python -m reporting.exit_manager            # PREVIEW
    uv run python -m reporting.exit_manager --place    # act

For each opening order WE placed (ledger 'entry' records), once it has FILLED:
  1. Attach a resting GTC stop if none exists — the 'bracket' stop (equity: 2.5xATR
     from fill; option: 50% of premium). Tight, exchange-side adherence.
  2. Exit when the hold elapses (squeeze ~2d, pullback ~5d) OR the signal reverses:
     market-close OUR quantity and cancel OUR resting stop.

Strictly scoped to our ledger ids — never lists/touches any other order, never
reads the commingled positions endpoint, never closes more than we opened.
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from data.cache import ParquetCache
from indicators import ta
from signals.confluence import ConfluenceParams, generate_squeeze
from signals.pullback import generate_pullback

from .broker_state import order_fill, position_closed
from .paper_ledger import child, entries, record
from .tradier import Tradier

ATR_STOP_MULT = 2.5
OPT_STOP_FRAC = 0.50          # stop a long option at 50% of premium


def _dir(side: str) -> int:
    return -1 if side in ("sell_short", "sell", "sell_to_close") else 1


def _trading_days_since(cache, symbol, fill_date) -> int:
    d = cache.read(symbol, "1d")
    if d is None:
        return 0
    return int((d.index > pd.Timestamp(fill_date, tz="UTC")).sum())


def _signal_reversed(cache, e) -> bool:
    """Current per-name signal direction opposite to our entry. Only meaningful for the
    per-name timing strategies (squeeze_2h, pullback_1d). Cross-sectional books like
    momentum_ls have NO per-name reversal signal — their 'signal' is the cross-sectional
    rank — so they never reverse here; they exit on the hold horizon / OCO brackets.
    Falling through to generate_pullback for them would apply the WRONG signal."""
    if e["source"] not in ("squeeze_2h", "pullback_1d"):
        return False
    entry_dir = _dir(e["side"]) if e["asset"] == "equity" else (
        1 if "C" in (e.get("option_symbol") or "") else -1)
    if e["source"] == "squeeze_2h":
        df = cache.read(e["symbol"], "2h")
        sig = generate_squeeze(df, ConfluenceParams(length=13, confluence_window=4))
    else:
        df = cache.read(e["symbol"], "1d")
        sig = generate_pullback(df, trend_ma=100, oversold=15.0, trend_mom=63)
    cur = int(sig.direction.iloc[-3:][lambda s: s != 0].iloc[-1]) if (sig.direction.iloc[-3:] != 0).any() else 0
    return cur == -entry_dir and cur != 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="exit manager (our orders only)")
    ap.add_argument("--place", action="store_true")
    args = ap.parse_args(argv)
    preview = not args.place
    tr = Tradier()
    cache = ParquetCache()
    today = pd.Timestamp.now(tz="UTC")
    print(f"=== EXIT MANAGER ({'PREVIEW' if preview else 'ACT'}) — our positions only ===")

    acted = 0
    for e in entries():
        oid = e["order_id"]
        o = tr._get(f"/v1/accounts/{tr.account}/orders/{oid}").get("order", {})
        if o.get("status") != "filled" or float(o.get("avg_fill_price") or 0) <= 0:
            print(f"  {e['symbol']:5} {e['asset']:6} id {oid}: {o.get('status')} — skip")
            continue
        fill = float(o["avg_fill_price"])

        # option spreads are DEFINED-RISK: no stop; exit on the hold horizon by
        # closing the vertical (reverse both legs). Handled here to avoid the
        # single-leg stop/exit paths (which assume an 'option_symbol').
        if e["asset"] == "option_spread":
            if position_closed(tr, tr.account, oid):
                continue
            held = _trading_days_since(cache, e["symbol"], o.get("create_date", today))
            if held >= e["hold_days"]:
                if preview:
                    print(f"  {e['symbol']:5} spread: CLOSE due (time, held {held}d) — "
                          f"would close vertical {e['long_leg']}/{e['short_leg']}")
                else:
                    legs = [(e["long_leg"], "sell_to_close", e["qty"]),
                            (e["short_leg"], "buy_to_close", e["qty"])]
                    resp = tr.vertical_order(e["symbol"], legs, preview=False, otype="market")
                    xo = resp.get("order", resp)
                    print(f"  {e['symbol']:5} spread: CLOSE (time, held {held}d) -> {xo.get('status', xo)}")
                    if xo.get("id"):
                        record({"kind": "exit", "order_id": xo["id"], "parent": oid,
                                "symbol": e["symbol"], "reason": "time-stop"})
                        acted += 1
            else:
                print(f"  {e['symbol']:5} spread: HOLD (defined risk, held {held}d/"
                      f"{int(e['hold_days'])}d, exp {e.get('expiry')})")
            continue

        d = _dir(e["side"]) if e["asset"] == "equity" else (
            1 if "C" in (e.get("option_symbol") or "") else -1)

        # 1) resting stop if none yet. SKIP momentum_ls: the dollar-neutral L/S book
        # already carries OCO brackets (recorded as 'stop' children) and a per-name ATR
        # stop would break dollar-neutrality as one leg stops out independently.
        if (e["source"] != "momentum_ls"
                and child(oid, "stop") is None and not position_closed(tr, tr.account, oid)):
            try:
                if e["asset"] == "equity":
                    atr = float(ta.atr(cache.read(e["symbol"], "1d"), 14).iloc[-1])
                    stop_px = fill - d * ATR_STOP_MULT * atr
                    side = "sell" if d > 0 else "buy_to_cover"
                    resp = tr.equity_stop(e["symbol"], side, e["qty"], stop_px, preview=preview)
                else:
                    stop_px = fill * OPT_STOP_FRAC
                    resp = tr.option_stop(e["symbol"], e["option_symbol"], e["qty"], stop_px, preview=preview)
                so = resp.get("order", resp)
                print(f"  {e['symbol']:5} {e['asset']:6}: resting stop @ {stop_px:.2f} -> {so.get('status', so)}")
                if not preview and so.get("id"):
                    record({"kind": "stop", "order_id": so["id"], "parent": oid,
                            "symbol": e["symbol"], "stop_price": round(stop_px, 2)})
                    acted += 1
            except Exception as ex:
                print(f"  {e['symbol']:5}: stop ERROR {ex}")

        # 2) time / signal exit
        if position_closed(tr, tr.account, oid):
            continue
        held = _trading_days_since(cache, e["symbol"], o.get("create_date", today))
        time_due = held >= e["hold_days"]
        rev = _signal_reversed(cache, e)
        if time_due or rev:
            reason = "time-stop" if time_due else "signal-reversal"
            st = child(oid, "stop")
            if preview:
                # can't really preview the close while the resting stop holds the
                # shares; just announce intent (the stop is cancelled first on --place)
                print(f"  {e['symbol']:5} {e['asset']:6}: CLOSE due ({reason}, held {held}d) "
                      f"— would cancel stop {st['order_id'] if st else '-'} then market-close {e['qty']}")
                continue
            try:
                if st:
                    try:
                        tr.cancel(st["order_id"])  # cancel OUR resting stop FIRST
                    except Exception as ce:
                        # cancel failed: if the stop FILLED (raced us), the position is
                        # already flat — closing again would open a naked opposite one
                        so = tr._get(f"/v1/accounts/{tr.account}/orders/{st['order_id']}").get("order", {})
                        if order_fill(so)[0]:
                            print(f"  {e['symbol']:5}: stop {st['order_id']} already FILLED — position closed, skip")
                            continue
                        print(f"  {e['symbol']:5}: stop {st['order_id']} not cancelable ({ce}) — proceeding")
                if e["asset"] == "equity":
                    side = "sell" if d > 0 else "buy_to_cover"
                    resp = tr.equity_order(e["symbol"], side, e["qty"], preview=False, otype="market")
                else:
                    resp = tr.option_order(e["symbol"], e["option_symbol"], "sell_to_close",
                                           e["qty"], preview=False, otype="market")
                xo = resp.get("order", resp)
                print(f"  {e['symbol']:5} {e['asset']:6}: CLOSE ({reason}, held {held}d) -> {xo.get('status', xo)}")
                if xo.get("id"):
                    record({"kind": "exit", "order_id": xo["id"], "parent": oid,
                            "symbol": e["symbol"], "reason": reason})
                    acted += 1
            except Exception as ex:
                print(f"  {e['symbol']:5}: close ERROR {ex}")
        elif e["source"] == "momentum_ls":
            print(f"  {e['symbol']:5} equity: HOLD (momentum_ls, held {held}d/"
                  f"{int(e['hold_days'])}d, OCO bracket active)")

    print(f"\n{'preview only' if preview else f'{acted} actions taken'}. "
          "Scoped to our order ids; no other orders touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
