"""BRACKET GUARD — no open position may lack a LIVE stop at the broker.

    uv run python -m reporting.bracket_guard            # preview
    uv run python -m reporting.bracket_guard --place    # act (sandbox only)

Born 2026-07-17: system_pick's after-close OCO was REJECTED by Tradier (can't
place a closing order on unfilled shares), the rejection carried an order id, we
recorded it as a live stop, and DELL ran a week naked through its intended stop.
A RECORDED stop is not a LIVE stop — this guard checks the broker.

For every open, filled equity entry: verify its recorded stop child is alive
(open/pending). If missing/rejected/canceled: place a fresh OCO at the recorded
tp/sl (or 3x/1.5x ATR from fill if none recorded), VERIFY acceptance before
recording, and push an ops alert either way. Runs at 9:45 ET via launchd
(com.squeeze.bracketguard) — right after entries from the previous evening fill
at the open — and again in the nightly chain.
"""

from __future__ import annotations

import argparse
import sys
import time

from data.cache import ParquetCache
from indicators import ta

from .broker_state import order_fill, position_closed
from .notify import push
from .paper_ledger import child, entries, load, record
from .tradier import Tradier

ALIVE = ("open", "pending", "partially_filled")
ATR_STOP, ATR_TARGET = 1.5, 3.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="verify every open position has a live stop")
    ap.add_argument("--place", action="store_true")
    args = ap.parse_args(argv)
    preview = not args.place
    tr = Tradier()
    if not tr.is_sandbox:
        print("bracket_guard: REFUSING — not sandbox")
        return 1
    cache = ParquetCache()
    naked = fixed = 0
    for e in entries():
        if e.get("asset") != "equity":
            continue
        oid = e["order_id"]
        o = tr._get(f"/v1/accounts/{tr.account}/orders/{oid}").get("order", {})
        if o.get("status") != "filled" or position_closed(tr, tr.account, oid):
            continue
        # a PENDING exit (e.g. queued market close after a breached stop) means the
        # position is on its way out — arming a stop now would double-close
        pending_exit = False
        for r in load():
            if r.get("kind") == "exit" and str(r.get("parent")) == str(oid) and r.get("order_id"):
                xo = tr._get(f"/v1/accounts/{tr.account}/orders/{r['order_id']}").get("order", {})
                if xo.get("status") in ALIVE:
                    pending_exit = True
                    break
        if pending_exit:
            print(f"  {e['symbol']:5} exit pending — skip")
            continue
        # ALL stop children (a rejected one may be followed by a live replacement)
        stops = [r for r in load() if r.get("kind") == "stop"
                 and str(r.get("parent")) == str(oid) and r.get("order_id")]
        st = stops[-1] if stops else None
        alive = False
        for s_ in stops:
            so = tr._get(f"/v1/accounts/{tr.account}/orders/{s_['order_id']}").get("order", {})
            if so.get("status") in ALIVE or order_fill(so)[0]:
                alive = True
                break
        if alive:
            continue
        naked += 1
        fill = float(o.get("avg_fill_price") or 0)
        up = e["side"] in ("buy",)
        d = 1 if up else -1
        intent = st or child(oid, "stop_intent")   # system_pick pre-records levels
        if intent and intent.get("tp") and intent.get("sl"):
            tp, sl = float(intent["tp"]), float(intent["sl"])
        else:
            atr = float(ta.atr(cache.read(e["symbol"], "1d"), 14).iloc[-1])
            tp = round(fill + d * ATR_TARGET * atr, 2)
            sl = round(fill - d * ATR_STOP * atr, 2)
        print(f"  {e['symbol']:5} NAKED (stop {'dead: ' + (st and so.get('status') or 'none') if st else 'missing'}) "
              f"-> {'would place' if preview else 'placing'} OCO TP {tp} / SL {sl}")
        if preview:
            continue
        close_side = "sell" if up else "buy_to_cover"
        oo = tr.oco_equity(e["symbol"], close_side, int(e["qty"]), tp, sl, preview=False).get("order", {})
        time.sleep(2)
        ver = (tr._get(f"/v1/accounts/{tr.account}/orders/{oo['id']}").get("order", {})
               if oo.get("id") else {})
        if ver.get("status") in ALIVE:
            record({"kind": "stop", "order_id": oo["id"], "parent": oid, "symbol": e["symbol"],
                    "bracket": "oco", "tp": tp, "sl": sl, "note": "bracket_guard re-arm"})
            push(f"bracket_guard: re-armed {e['symbol']} OCO TP {tp} / SL {sl}",
                 priority="default")
            fixed += 1
        else:
            push(f"bracket_guard: {e['symbol']} OCO placement FAILED "
                 f"({ver.get('status')}) — position UNPROTECTED, manual attention needed",
                 priority="urgent")
    print(f"bracket_guard: {naked} naked position(s), {fixed} re-armed"
          + (" (preview)" if preview else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
