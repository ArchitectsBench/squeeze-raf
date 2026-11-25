"""Stage-1 edge-check for the trend-following signal (daily).

    uv run python -m validation.trend_edge

Pure forward-return event study (no exits, no optimization) — the rule-3 gate that
decides whether trend-following has raw edge BEFORE we build/optimize a strategy.
Tests a few breakout lookbacks across multi-day horizons vs a matched random
baseline, pooled over the universe.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from data.universe import load_universe
from edge_check.event_study import _stats, collect_entries, monotonicity, random_entries
from signals.trend import generate_trend

HORIZONS = [5, 10, 20, 40]       # trading days (~1wk .. 2mo)
PRIMARY = 20
BREAKOUTS = [20, 40, 55]
ROUND_TRIP_BPS = 4.0             # equities round-trip (2bps slippage x2)


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    u = load_universe("broad")
    cache = ParquetCache()
    frames = {s: cache.read(s, "1d") for s in u.tickers if cache.exists(s, "1d")}
    frames = {s: d for s, d in frames.items() if d is not None and len(d) > 250}
    rng = np.random.default_rng(42)

    print("=== STAGE-1 EDGE CHECK — trend-following (daily) ===")
    print(f"{len(frames)} symbols | horizons(days) {HORIZONS} | primary h={PRIMARY}\n")

    any_pass = False
    for bk in BREAKOUTS:
        real_parts, rand_parts = [], []
        for sym, df in frames.items():
            sig = generate_trend(df, breakout=bk)
            e = collect_entries(df, sig, HORIZONS)
            real_parts.append(e)
            n = len(e)
            bal = float((e["direction"] > 0).mean()) if n else 0.5
            rand_parts.append(random_entries(df, n, bal, HORIZONS, rng))
        real = pd.concat(real_parts, ignore_index=True)
        rand = pd.concat(rand_parts, ignore_index=True)

        print(f"--- breakout = {bk} bars  (entries: {len(real)}) ---")
        rows = []
        for h in HORIZONS:
            rs, zs = _stats(real[f"ret_{h}"]), _stats(rand[f"ret_{h}"])
            rows.append({"h": h, "n": rs["n"], "mean_bps": round(rs["mean_bps"], 1),
                         "hit": round(rs["hit_rate"], 3), "t": round(rs["t_stat"], 2),
                         "rand_mean": round(zs["mean_bps"], 1), "rand_hit": round(zs["hit_rate"], 3)})
        print(pd.DataFrame(rows).to_string(index=False))
        mono = monotonicity(real, PRIMARY)
        print(f"  conviction monotonicity @h={PRIMARY}: {mono['buckets']} mono={mono['monotonic']}")

        r = _stats(real[f"ret_{PRIMARY}"])
        z = _stats(rand[f"ret_{PRIMARY}"])
        net = r["mean_bps"] - ROUND_TRIP_BPS
        ok = (r["hit_rate"] >= 0.53 and r["t_stat"] >= 2.0 and net >= 5
              and r["mean_bps"] > z["mean_bps"] and r["hit_rate"] > z["hit_rate"])
        any_pass = any_pass or ok
        print(f"  @h={PRIMARY}: hit {r['hit_rate']:.3f} (>=0.53), t {r['t_stat']:.2f} (>=2), "
              f"net {net:.1f}bps (>=5), beats-random {r['mean_bps']>z['mean_bps'] and r['hit_rate']>z['hit_rate']}"
              f"  -> {'PASS' if ok else 'fail'}\n")

    print("=== VERDICT ===")
    print("Trend signal shows raw edge -> proceed to WFA/gauntlet." if any_pass
          else "No setting clears the edge gate at the primary horizon (per rule 3, "
               "do not optimize). Inspect horizons/conviction above.")
    return 0 if any_pass else 1


if __name__ == "__main__":
    sys.exit(main())
