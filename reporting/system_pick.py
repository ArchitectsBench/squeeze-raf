"""Daily SYSTEM PICK — paper-trade the single top-conviction GO prediction.

    uv run python -m reporting.system_pick            # preview
    uv run python -m reporting.system_pick --place    # act (sandbox only)

Track 2 of the live forward test (config/gates.yaml live_forward_test): execution-
realism evidence on top of the prediction ledger. Reads TODAY'S snapshot from
data/predictions.jsonl (single source of truth — no duplicated signal logic), takes
the highest-conviction GO call, places a $2k market entry at next open with a GTC
OCO bracket (target +3xATR / stop -1.5xATR, 2:1), hold_days=10 time backstop via
exit_manager. Records entry + stop child (bracket) to the paper ledger.

Guards: sandbox-only; one pick per day; skips symbols already open in the ledger;
skips if no GO exists (logged, not forced).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from data.cache import ParquetCache
from indicators import ta

from .broker_state import position_closed
from .paper_ledger import entries, record
from .prediction_ledger import LEDGER as PRED
from .tradier import Tradier

NOTIONAL = 2000.0
ATR_STOP, ATR_TARGET = 1.5, 3.0
HOLD_DAYS = 10
SOURCE = "system_pick"


def _todays_predictions() -> list[dict]:
    if not PRED.is_file():
        return []
    rows = [json.loads(x) for x in PRED.read_text().splitlines() if x.strip()]
    if not rows:
        return []
    asof = max(r["asof"] for r in rows)
    return [r for r in rows if r["asof"] == asof]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="daily top-GO system pick (paper)")
    ap.add_argument("--place", action="store_true")
    args = ap.parse_args(argv)
    preview = not args.place

    preds = _todays_predictions()
    if not preds:
        print("system_pick: no predictions recorded — run prediction_ledger first")
        return 0
    asof = preds[0]["asof"]

    # one pick per as-of day (idempotent across cron re-runs)
    if any(e.get("source") == SOURCE and e.get("asof") == asof for e in entries()):
        print(f"system_pick: already placed for {asof} — skip")
        return 0

    tr = Tradier()
    if not tr.is_sandbox:
        print("system_pick: REFUSING — not a sandbox base")
        return 1

    open_syms = set()
    for e in entries():
        if not position_closed(tr, tr.account, e["order_id"]):
            open_syms.add(e["symbol"])

    gos = sorted((p for p in preds if p["call"] == "GO" and p["symbol"] not in open_syms),
                 key=lambda p: -(p["conviction"] or 0))
    if not gos:
        print(f"system_pick: no eligible GO setups for {asof} — nothing placed")
        return 0
    p = gos[0]
    sym, up = p["symbol"], p["dir"] == "UP"
    cache = ParquetCache()
    px = float(cache.read(sym, "1d")["close"].iloc[-1])
    atr = float(ta.atr(cache.read(sym, "1d"), 14).iloc[-1])
    qty = max(1, int(NOTIONAL / px))
    side = "buy" if up else "sell_short"
    d = 1 if up else -1
    tp = round(px + d * ATR_TARGET * atr, 2)
    sl = round(px - d * ATR_STOP * atr, 2)
    print(f"system_pick {asof}: {sym} {p['dir']} conviction {p['conviction']} "
          f"({p['tier']}, tape {p['tape']}, news {p['news']}) — {side} {qty} ~${px:.2f}, "
          f"OCO TP {tp} / SL {sl}, hold {HOLD_DAYS}d")
    if preview:
        print("(preview — use --place to act)")
        return 0

    o = tr.equity_order(sym, side, qty, preview=False, otype="market").get("order", {})
    if not o.get("id"):
        print(f"system_pick: entry FAILED: {o}")
        return 1
    eid = o["id"]
    record({"ts": time.strftime("%Y-%m-%d"), "order_id": eid, "symbol": sym,
            "asset": "equity", "side": side, "qty": float(qty), "option_symbol": None,
            "ref_price": px, "kind": "entry", "source": SOURCE, "asof": asof,
            "hold_days": HOLD_DAYS, "conviction": (p["conviction"] or 0) / 100.0})
    print(f"entry {eid} recorded")

    # Bracket ONLY if the entry actually FILLED (rare for after-close market orders —
    # they queue to next open). Tradier REJECTS closing orders on unowned shares and
    # the rejection carries an order id: recording it as a stop left DELL naked for a
    # week (2026-07-10..17). Unfilled -> record intended levels; bracket_guard arms
    # the real OCO at 9:45 ET right after the fill.
    from .broker_state import order_fill
    fill = None
    for _ in range(10):
        od = tr._get(f"/v1/accounts/{tr.account}/orders/{eid}").get("order", {})
        filled, fpx = order_fill(od)
        if filled and fpx:
            fill = fpx
            break
        time.sleep(3)
    base = fill or px
    tp = round(base + d * ATR_TARGET * atr, 2)
    sl = round(base - d * ATR_STOP * atr, 2)
    if fill is None:
        record({"kind": "stop_intent", "parent": eid, "symbol": sym, "tp": tp, "sl": sl,
                "note": "entry unfilled at placement; bracket_guard arms after open"})
        print(f"entry queued for next open — bracket intent recorded (TP {tp} / SL {sl}); "
              "bracket_guard arms it at 9:45 ET")
        return 0
    close_side = "sell" if up else "buy_to_cover"
    oo = tr.oco_equity(sym, close_side, qty, tp, sl, preview=False).get("order", {})
    time.sleep(2)
    ver = (tr._get(f"/v1/accounts/{tr.account}/orders/{oo['id']}").get("order", {})
           if oo.get("id") else {})
    if ver.get("status") in ("open", "pending", "partially_filled"):
        record({"kind": "stop", "order_id": oo["id"], "parent": eid, "symbol": sym,
                "bracket": "oco", "tp": tp, "sl": sl,
                "note": f"system_pick {ATR_TARGET}x/{ATR_STOP}xATR GTC"})
        print(f"OCO {oo['id']} VERIFIED {ver.get('status')}: TP {tp} / SL {sl}")
    else:
        from .notify import push
        push(f"system_pick: {sym} OCO NOT live ({ver.get('status')}) — bracket_guard "
             "will arm at 9:45 ET", priority="high")
        print(f"system_pick: OCO not accepted ({ver.get('status')}) — NOT recorded; "
              "bracket_guard will arm after fill")
    return 0


if __name__ == "__main__":
    sys.exit(main())
