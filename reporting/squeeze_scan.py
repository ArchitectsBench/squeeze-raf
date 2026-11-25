"""Scan the universe for SQUEEZE setups — coiled (ready to explode) + just-fired.

    uv run python -m reporting.squeeze_scan            # daily
    uv run python -m reporting.squeeze_scan --tf 2h --alerts

A "squeeze" = Bollinger Bands compressed INSIDE the Keltner Channel (low volatility,
energy building). State 3=tightest, 2=mid, 1=loose, 0=fired/none.

Direction is TREND-based (price vs a rising/falling 20-MA), NOT the lagging momentum
histogram — the histogram only times the fire. --alerts emits EW-grade coils in two tiers:
  ALERT = trend + momentum CONFIRM (same direction, building) -> pushed to ntfy
  WATCH = trend-aligned tight coil, momentum not yet confirmed (shown in-scan only)

All scan/tier/direction logic lives in reporting.screener_core (shared with the web API).
NOTE: the squeeze fire showed a real Stage-1 edge but did NOT graduate as a timed
strategy — treat these as discretionary watch candidates, not auto-trades.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import pandas as pd

from data.cache import ParquetCache
from reporting.screener_core import classify

UNIV = Path(__file__).resolve().parent.parent / "config" / "liquid_universe.json"
CS = Path(__file__).resolve().parent.parent / "config" / "common_stocks.json"


def _news_sentiment(symbol: str) -> str | None:
    """Per-ticker net sentiment from Polygon news insights, counting ONLY articles
    within the validated ~7-day window (stale headlines must not set the badge).
    Best-effort (None on failure)."""
    key = os.environ.get("POLYGON_API_KEY", "")
    if not key:
        return None
    floor = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)).isoformat()
    try:
        r = httpx.get("https://api.polygon.io/v2/reference/news",
                      params={"ticker": symbol, "limit": 10, "apiKey": key}, timeout=15)
        counts = {"positive": 0, "negative": 0}
        for a in r.json().get("results", []):
            if (a.get("published_utc") or "") < floor:
                continue
            ins = next((i for i in (a.get("insights") or []) if i.get("ticker") == symbol), None)
            s = (ins or {}).get("sentiment")
            if s in counts:
                counts[s] += 1
        if counts["positive"] > counts["negative"]:
            return "positive"
        if counts["negative"] > counts["positive"]:
            return "negative"
        return "neutral"
    except Exception:
        return None


def _apply_verdicts(alerts: list[dict]) -> None:
    """Annotate each ALERT with its GO/CAUTION/AVOID verdict (tape + news; the validated
    gate — GO 55.7% win vs AVOID 45.1%). FAIL-OPEN: if the tape score is unavailable the
    alert stays ungated (verdict '?') — a dead FRED feed must not silence real setups."""
    from reporting.decision import verdict
    from reporting.tape_score import tape_score
    try:
        tape = float(tape_score()["score"])
    except Exception:
        for a in alerts:
            a["verdict"], a["conviction"] = "?", None
        return
    for a in alerts:
        v = verdict(a["dir"], a["tier"], 1, 1.0, tape, _news_sentiment(a["symbol"]), None)
        a["verdict"], a["conviction"] = v["call"], v["conviction"]


def _push_ntfy(topic: str, alerts: list[dict], tf: str, day) -> None:
    """Push the ALERT-tier setups to an ntfy topic (phone notification). Best-effort."""
    ups = [a for a in alerts if a["dir"] == "UP"]
    downs = [a for a in alerts if a["dir"] == "DOWN"]

    def fmt(a):
        v = f" {a['verdict']}{a['conviction']:.0f}" if a.get("conviction") is not None else ""
        return f"{a['symbol']} ${a['price']} st{a['state']} coil{a['bars_in_sqz']} mom{a['mom']:+.2f}{v}"
    lines = []
    if ups:
        lines.append("▲ UP: " + ", ".join(fmt(a) for a in ups))
    if downs:
        lines.append("▼ DOWN: " + ", ".join(fmt(a) for a in downs))
    body = "\n".join(lines)
    try:
        httpx.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"),
                   headers={"Title": f"{len(alerts)} squeeze setups ({tf}, {day})",
                            "Tags": "chart_with_upwards_trend", "Priority": "default"},
                   timeout=15)
        print(f"-> pushed {len(alerts)} alerts to ntfy.sh/{topic}")
    except Exception as ex:
        print(f"-> ntfy push failed: {ex}")


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    try:
        from data.base import load_dotenv
        load_dotenv()
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="squeeze scanner")
    ap.add_argument("--tf", default="1d", help="timeframe (5min..1w)")
    ap.add_argument("--length", type=int, default=20, help="squeeze length")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--alerts", action="store_true", help="emit only EW-grade ALERT/WATCH setups")
    ap.add_argument("--ntfy", default=None,
                    help="ntfy topic to push ALERTs to (default: $NTFY_TOPIC). Implies --alerts.")
    args = ap.parse_args(argv)
    if args.ntfy:
        args.alerts = True

    cache = ParquetCache()
    syms = json.loads(UNIV.read_text())
    cs = set(json.loads(CS.read_text())) if CS.exists() else None
    coiled, fired, alerts, asof = [], [], [], None
    for s in syms:
        if cs is not None and s not in cs:
            continue
        df = cache.read(s, args.tf)
        c = classify(df, length=args.length)
        if c is None:
            continue
        asof = df.index[-1] if asof is None else max(asof, df.index[-1])
        row = {"symbol": s, "price": c["price"], "state": c["state"],
               "bars_in_sqz": c["coil"], "dir": c["dir"], "mom": c["mom"],
               "mom_building": c["mom_building"]}
        if c["tier"]:
            alerts.append({**row, "tier": c["tier"]})
        if c["state"] >= 1:
            coiled.append(row)
        elif c["fired_recent"]:
            fired.append(row)

    if args.alerts:
        alerts.sort(key=lambda r: (-r["state"], -r["bars_in_sqz"], -abs(r["mom"])))
        day = asof.date() if asof is not None else "?"
        alert_t = [a for a in alerts if a["tier"] == "ALERT"]
        watch_t = [a for a in alerts if a["tier"] == "WATCH"]
        _apply_verdicts(alert_t)   # GO/CAUTION pushed; AVOID printed but NOT pushed
        cols = ["symbol", "price", "dir", "state", "bars_in_sqz", "mom", "mom_building"]
        acols = cols + ["verdict", "conviction"]
        print(f"=== SQUEEZE ALERTS ({args.tf}, length {args.length}) — as of {day} — "
              f"{len(alert_t)} ALERT / {len(watch_t)} WATCH ===")
        print("--- ALERT (trend + momentum confirm) ---")
        print(pd.DataFrame(alert_t)[acols].to_string(index=False) if alert_t else "(none)")
        print("\n--- WATCH (trend-aligned coil; momentum not yet confirmed) ---")
        print(pd.DataFrame(watch_t)[cols].to_string(index=False) if watch_t else "(none)")
        topic = args.ntfy or os.environ.get("NTFY_TOPIC")
        pushable = [a for a in alert_t if a["verdict"] != "AVOID"]
        skipped = len(alert_t) - len(pushable)
        if skipped:
            print(f"-> {skipped} AVOID-verdict alert(s) NOT pushed (backtest: loser bucket)")
        if topic and pushable:
            _push_ntfy(topic, pushable, args.tf, day)
        return 0

    print(f"=== SQUEEZE SCAN ({args.tf}, length {args.length}) — as of "
          f"{asof.date() if asof is not None else '?'} ===")
    print(f"universe {len(cs) if cs else len(syms)} common stocks | "
          f"{len(coiled)} coiled, {len(fired)} just-fired\n")
    coiled.sort(key=lambda r: (-r["state"], -r["bars_in_sqz"], -abs(r["mom"])))
    print("--- READY TO EXPLODE (in squeeze now; state 3=tightest) ---")
    print(pd.DataFrame(coiled[: args.top]).to_string(index=False) if coiled else "(none)")
    fired.sort(key=lambda r: -abs(r["mom"]))
    print("\n--- JUST FIRED (expansion started) ---")
    print(pd.DataFrame(fired[: args.top]).to_string(index=False) if fired else "(none)")
    print("\nDiscretionary watch candidates — squeeze fire is a real but NON-graduated signal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
