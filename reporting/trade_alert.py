"""High-conviction TRADE CARD alerts — for manual entry in an external account.

    uv run python -m reporting.trade_alert            # preview (prints, no push)
    uv run python -m reporting.trade_alert --push     # send to ntfy

After the nightly prediction snapshot, any GO setup with conviction >= MIN_CONVICTION
is pushed as a complete order card: direction, conviction + reasons, stock
entry/target/stop with R/R, and the recommended option structure with exact legs.
Fires POST-CLOSE for NEXT-OPEN entry — the timing every backtest validated.

Guards: max MAX_ALERTS/day, one alert per (symbol, asof) ever (sent-log), skips
symbols already open in the paper ledger (you'd already have been alerted).
Footer reminds: signal is NOT graduated — discretionary, small size.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from reporting.notify import push
from reporting.prediction_ledger import LEDGER as PRED

SENT = Path(__file__).resolve().parent.parent / "data" / "trade_alerts_sent.jsonl"
# 65 = the VALIDATED GO bucket (verdict_backtest: GO>=65 won 55.7%). The earlier 70
# was an unregistered conservative overlay; 2026-07-09 aligned cards with the same
# bucket the forward-test scorecard grades.
MIN_CONVICTION = 65.0
MAX_ALERTS = 3


def _todays(preds: list[dict]) -> list[dict]:
    if not preds:
        return []
    asof = max(r["asof"] for r in preds)
    return [r for r in preds if r["asof"] == asof]


def _card(p: dict, plan: dict) -> str:
    arrow = "▲ LONG" if p["dir"] == "UP" else "▼ SHORT"
    lines = [f"{p['symbol']} {arrow} · {p.get('tf', '1d')} setup · hold ~10d · "
             f"conviction {p['conviction']:.0f} "
             f"({p['tier']}, tape {p['tape']:.0f}"
             f"{', news ' + p['news'] if p.get('news') and p['news'] != 'neutral' else ''})"]
    rec = plan.get("recommended") or {}
    alts = {a.get("vehicle"): a for a in plan.get("alternatives", [])}
    stk = rec if rec.get("vehicle") == "STOCK" else alts.get("STOCK", {})
    if stk:
        lines.append(f"STOCK: entry ~{stk.get('entry')} → target {stk.get('target')} / "
                     f"stop {stk.get('stop')}"
                     + (f" (R/R {stk.get('risk_reward')})" if stk.get("risk_reward") else ""))
    if rec.get("vehicle") and rec["vehicle"] != "STOCK":
        opt = f"OPTION ({rec['vehicle']}): {rec.get('legs', '')}"
        for k, lbl in (("entry", ""), ("breakeven", "BE"), ("max_loss", "max loss $")):
            if rec.get(k) is not None:
                opt += f" · {lbl}{rec[k]}" if lbl else f" · {rec[k]}"
        lines.append(opt)
        if plan.get("exp"):
            lines.append(f"exp {plan['exp']}")
    lines.append("Enter near NEXT OPEN (validated timing) · paper twin runs in sandbox")
    lines.append("⚠ non-graduated signal — discretionary, small size, hard stop")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="high-conviction trade-card alerts")
    ap.add_argument("--push", action="store_true", help="send to ntfy (default: print only)")
    ap.add_argument("--min-conviction", type=float, default=MIN_CONVICTION)
    args = ap.parse_args(argv)

    if not PRED.is_file():
        print("trade_alert: no predictions recorded yet")
        return 0
    preds = _todays([json.loads(x) for x in PRED.read_text().splitlines() if x.strip()])
    gos = sorted((p for p in preds if p["call"] == "GO"
                  and (p["conviction"] or 0) >= args.min_conviction),
                 key=lambda p: -(p["conviction"] or 0))
    if not gos:
        # NIGHTLY DIGEST: silence must be information, not mystery. Push a compact
        # summary of what was graded and the best near-miss, so a 63-conviction
        # setup (e.g. DELL 2026-07-08, +4.2% next day) is visible for discretion.
        graded = [p for p in preds if p.get("conviction") is not None]
        best = max(graded, key=lambda p: p["conviction"], default=None)
        if best is not None:
            body = (f"{len(preds)} setup(s) graded, none GO>={args.min_conviction:.0f}.\n"
                    f"Best: {best['symbol']} {best['dir']} {best['call']} "
                    f"{best['conviction']:.0f} ({best['tier']}, tape {best['tape']:.0f}, "
                    f"news {best['news'] or 'none'})")
        elif preds:
            body = (f"{len(preds)} setup(s) found but UNGRADED — tape score unavailable "
                    f"(FRED/feed issue): " + ", ".join(p["symbol"] for p in preds[:5]))
        else:
            body = "no EW-grade daily setups at today's close"
        print("trade_alert digest:", body.replace("\n", " | "))
        if args.push:
            push(body, title=f"Daily digest {preds[0]['asof'] if preds else ''}: no trade card",
                 priority="default")
        return 0

    sent = set()
    if SENT.is_file():
        sent = {(r["symbol"], r["asof"]) for r in
                (json.loads(x) for x in SENT.read_text().splitlines() if x.strip())}

    from reporting.trade_plan import trade_plan
    n = 0
    for p in gos:
        if n >= MAX_ALERTS:
            print(f"trade_alert: {MAX_ALERTS}/day cap reached — remaining GOs not sent")
            break
        key = (p["symbol"], p["asof"])
        if key in sent:
            continue
        try:
            plan = trade_plan(p["symbol"], p["dir"], horizon_days=10,
                              conviction=float(p["conviction"] or 60))
        except Exception as ex:
            plan = {"recommended": {}, "alternatives": [], "error": str(ex)}
        card = _card(p, plan)
        title = (f"TRADE CARD: {p['symbol']} {'LONG' if p['dir'] == 'UP' else 'SHORT'} "
                 f"({p.get('tf', '1d')}) — GO {p['conviction']:.0f}")
        print(f"--- {title}\n{card}\n")
        if args.push:
            # record as sent ONLY on confirmed delivery — a failed push must retry
            # on the next run, not be silently marked done (the 07-09/10 bug)
            if push(card, title=title, priority="high"):
                with open(SENT, "a") as f:
                    f.write(json.dumps({"symbol": p["symbol"], "asof": p["asof"],
                                        "conviction": p["conviction"]}) + "\n")
                    f.flush()
            else:
                print(f"trade_alert: PUSH FAILED for {p['symbol']} — will retry next run")
        n += 1
    print(f"trade_alert: {n} card(s) {'sent' if args.push else 'previewed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
