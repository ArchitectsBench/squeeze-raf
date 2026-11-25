"""Nightly PREDICTION LEDGER — the live forward test's primary evidence stream.

    uv run python -m reporting.prediction_ledger          # snapshot today's verdicts

Records EVERY daily EW-grade setup's verdict (symbol, dir, tier, tape, news,
conviction, GO/CAUTION/AVOID) to data/predictions.jsonl — predictions are graded
later by reporting.prediction_scorecard against forward returns, so the system is
scored on ~dozens of calls/day instead of a handful of trades. Pre-registered
criteria: config/gates.yaml (live_forward_test).

Idempotent per as-of date (re-runs skip). Fail-open like the alert path: if tape is
unavailable the snapshot still writes with verdict '?' rows excluded from grading.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from data.cache import ParquetCache
from reporting.paper_ledger import record as _record_raw  # fsync'd append helper
from reporting.screener_core import classify

LEDGER = Path(__file__).resolve().parent.parent / "data" / "predictions.jsonl"
UNIV = Path(__file__).resolve().parent.parent / "config" / "common_stocks.json"


def _append(entry: dict) -> None:
    # reuse paper_ledger's fsync discipline but on our own file
    import reporting.paper_ledger as pl
    orig = pl.LEDGER
    try:
        pl.LEDGER = LEDGER
        _record_raw(entry)
    finally:
        pl.LEDGER = orig


def snapshot() -> int:
    cache = ParquetCache()
    syms = json.loads(UNIV.read_text())

    setups = []
    asof = None
    for s in syms:
        df = cache.read(s, "1d")
        c = classify(df, length=20)
        if c is None or not c["tier"]:
            continue
        last = str(df.index[-1].date())
        asof = max(asof or last, last)
        setups.append({"symbol": s, "tf": "1d", **{k: c[k] for k in
                       ("price", "state", "coil", "dir", "mom", "tier")}})
    if asof is None:
        print("no setups — nothing to snapshot")
        return 0

    existing = set()
    if LEDGER.is_file():
        for line in LEDGER.read_text().splitlines():
            if line.strip():
                existing.add(json.loads(line)["asof"])
    if asof in existing:
        print(f"snapshot for {asof} already recorded — skip (idempotent)")
        return 0

    # tape + per-symbol news via the same code path as alerts (fail-open)
    from reporting.squeeze_scan import _news_sentiment
    from reporting.tape_score import tape_score
    try:
        tape = float(tape_score()["score"])
    except Exception:
        tape = None
    from reporting.decision import verdict as _verdict
    n = 0
    for st in setups:
        if tape is None:
            row = {"asof": asof, **st, "tape": None, "news": None,
                   "conviction": None, "call": "?"}
        else:
            news = _news_sentiment(st["symbol"])
            v = _verdict(st["dir"], st["tier"], 1, 1.0, tape, news, None)
            row = {"asof": asof, **st, "tape": tape, "news": news,
                   "conviction": v["conviction"], "call": v["call"]}
        _append(row)
        n += 1
    print(f"recorded {n} predictions for {asof} (tape={tape})")
    return 0


if __name__ == "__main__":
    sys.exit(snapshot())
