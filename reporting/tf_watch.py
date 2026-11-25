"""Multi-timeframe high-conviction watcher — trade cards from ANY timeframe.

    uv run python -m reporting.tf_watch                          # preview intraday TFs
    uv run python -m reporting.tf_watch --push --rth-only        # launchd (every 15m, RTH)
    uv run python -m reporting.tf_watch --tfs 2d,3d,4d,1w --push # nightly multiday pass

Scans the cached matrix timeframes for tier-grade setups, verdicts each
(tape + news, same decision.verdict as everywhere), and pushes a trade card for
GO >= MIN_CONVICTION. The 1d class stays with reporting.trade_alert (it feeds the
pre-registered forward test); this watcher covers everything else.

HONESTY: only the 1d setup class has validated alert evidence — cards from other
timeframes carry an explicit "TF class unvalidated" tag. 5min/15min are excluded
by default (they go stale faster than a human can mirror them; pass --tfs to add).

Dedupe: max one card per (symbol, tf, day) via data/tf_alerts_sent.jsonl; global
MAX_ALERTS per invocation. Data freshness comes from the refresher daemon
(intraday caches update every ~5 min during RTH; quotes are 15-min delayed).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from data.cache import ParquetCache
from reporting.notify import push
from reporting.screener_core import classify
from reporting.squeeze_scan import _news_sentiment
from reporting.trade_alert import _card

ROOT = Path(__file__).resolve().parent.parent
SENT = ROOT / "data" / "tf_alerts_sent.jsonl"
UNIV = ROOT / "config" / "common_stocks.json"
DEFAULT_TFS = ["5min", "15min", "30min", "1h", "2h", "3h", "4h"]
FAST_TFS = {"5min", "15min", "30min"}   # day-trade class: extra latency warning
# 70, NOT 65: the 65 GO bucket was validated on DAILY setups only (trade_alert's
# domain). At 65, a risk-on tape alone qualifies every trend-aligned coil (107
# would-be cards on 2026-07-10) — unvalidated TF classes keep the stricter bar.
MIN_CONVICTION = 70.0
MAX_PER_DAY = 10   # global daily card cap across all passes
MAX_ALERTS = 5
ET = ZoneInfo("America/New_York")
HOLD_HINT = {"5min": "scalp (<1h)", "15min": "scalp-hours", "30min": "intraday",
             "1h": "intraday-1d", "2h": "~1-3d", "3h": "~1-3d",
             "4h": "~2-5d", "2d": "~2-4wk", "3d": "~3-6wk", "4d": "~1-2mo", "1w": "~1-3mo"}


_data_client: list = []   # lazy singleton


def _client():
    if not _data_client:
        from reporting.tradier import TradierData
        _data_client.append(TradierData())
    return _data_client[0]


def _live_quote(symbol: str) -> float | None:
    """REAL-TIME last price — only when a funded prod token is configured. Returns
    None on sandbox/delayed creds so cards keep the stale-data warning instead of a
    delayed quote masquerading as live."""
    try:
        cl = _client()
        return cl.last(symbol) if cl.realtime else None
    except Exception:
        return None


def _rt_confirm(symbol: str, tf: str) -> bool | None:
    """Re-run the setup check on REAL-TIME Tradier 5min bars (resampled to tf).
    True = still tier-grade live; False = gone (skip the alert); None = can't verify
    (no prod token / endpoint hiccup) -> caller falls back to the delayed warning."""
    try:
        cl = _client()
        if not cl.realtime:
            return None
        raw = cl.timesales(symbol, interval="5min", days=10)
        if raw is None or len(raw) < 60:
            return None
        if tf == "5min":
            df = raw
        else:
            from data.resample import resample_intraday
            df = resample_intraday(raw, tf)
        c = classify(df, length=20)
        return bool(c and c["tier"])
    except Exception:
        return None


def _rth_now() -> bool:
    now = datetime.now(timezone.utc).astimezone(ET)
    return now.weekday() < 5 and dtime(9, 45) <= now.timetz().replace(tzinfo=None) <= dtime(16, 5)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="multi-timeframe high-conviction watcher")
    ap.add_argument("--tfs", default=",".join(DEFAULT_TFS))
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--min-conviction", type=float, default=MIN_CONVICTION)
    ap.add_argument("--rth-only", action="store_true",
                    help="exit quietly outside market hours (for the launchd interval job)")
    args = ap.parse_args(argv)
    if args.rth_only and not _rth_now():
        return 0
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]

    from reporting.decision import verdict as _verdict
    from reporting.tape_score import tape_score
    try:
        tape = float(tape_score()["score"])
    except Exception:
        print("tf_watch: tape unavailable — cannot verdict, skipping pass (fail-closed: "
              "unvalidated TF class does not alert ungated)")
        return 0

    cache = ParquetCache()
    syms = json.loads(UNIV.read_text())
    today = datetime.now(timezone.utc).astimezone(ET).strftime("%Y-%m-%d")
    sent = set()
    sent_today = 0
    if SENT.is_file():
        rows = [json.loads(x) for x in SENT.read_text().splitlines() if x.strip()]
        sent = {(r["symbol"], r["tf"], r["day"]) for r in rows}
        sent_today = sum(1 for r in rows if r.get("day") == today)
    if sent_today >= MAX_PER_DAY:
        print(f"tf_watch: daily cap {MAX_PER_DAY} reached ({sent_today} sent) — quiet until tomorrow")
        return 0

    candidates = []
    for s in syms:
        for tf in tfs:
            if (s, tf, today) in sent:
                continue
            c = classify(cache.read(s, tf), length=20)
            if c is None or not c["tier"] or c["dir"] == "—":
                continue
            candidates.append({"symbol": s, "tf": tf, **c})

    n = 0
    for c in candidates:
        if n >= MAX_ALERTS:
            print(f"tf_watch: {MAX_ALERTS}/pass cap reached")
            break
        news = _news_sentiment(c["symbol"])
        v = _verdict(c["dir"], c["tier"], 1, 1.0, tape, news, None)
        if v["call"] != "GO" or v["conviction"] < args.min_conviction:
            continue
        p = {"symbol": c["symbol"], "dir": c["dir"], "conviction": v["conviction"],
             "tier": c["tier"], "tape": tape, "news": news, "tf": c["tf"], "asof": today}
        try:
            from reporting.trade_plan import trade_plan
            plan = trade_plan(c["symbol"], c["dir"], horizon_days=10,
                              conviction=float(v["conviction"]))
        except Exception as ex:
            plan = {"recommended": {}, "alternatives": [], "error": str(ex)}
        card = _card(p, plan).replace(
            "hold ~10d", f"hold {HOLD_HINT.get(c['tf'], '?')}")
        card += "\n⚠ non-1d timeframe: signal class UNVALIDATED (no backtest evidence yet)"
        if c["tf"] in FAST_TFS:
            rt = _rt_confirm(c["symbol"], c["tf"])
            if rt is False:
                print(f"    -> {c['symbol']} {c['tf']}: setup GONE on real-time bars — skipped")
                continue
            live = _live_quote(c["symbol"])
            if live is not None:
                drift = (live / c["price"] - 1) * 100 if c["price"] else 0
                card += (f"\n⏱ REAL-TIME: {'setup CONFIRMED on live bars · ' if rt else ''}"
                         f"last {live:.2f} ({drift:+.1f}% vs setup bar {c['price']})")
            else:
                card += ("\n⏱ DATA IS ~15min DELAYED (Polygon Starter) — verify the setup "
                         "is still live on your broker's real-time chart before entering")
        title = (f"TRADE CARD: {c['symbol']} {'LONG' if c['dir'] == 'UP' else 'SHORT'} "
                 f"({c['tf']}) — GO {v['conviction']:.0f}")
        print(f"--- {title}\n{card}\n")
        if args.push:
            if push(card, title=title, priority="high"):
                with open(SENT, "a") as f:
                    f.write(json.dumps({"symbol": c["symbol"], "tf": c["tf"], "day": today,
                                        "conviction": v["conviction"]}) + "\n")
                    f.flush()
            else:
                print(f"tf_watch: PUSH FAILED for {c['symbol']} {c['tf']} — will retry next pass")
        n += 1
    print(f"tf_watch: {len(candidates)} tier-grade setups scanned across {tfs}, "
          f"{n} card(s) {'sent' if args.push else 'previewed'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
