"""Stage-1 edge-check for the pullback-in-trend signal (daily), BETA-AWARE.

    uv run python -m validation.pullback_edge

The decisive test: do dip entries beat a RANDOM same-direction baseline (which
captures the bull-market drift)? Breakouts failed this (they buy highs); pullbacks
buy dips, so a win here is genuine timing alpha, not beta. Forward-return event
study over multi-day horizons, pooled over the universe. No optimization.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from data.universe import load_universe
from edge_check.event_study import _stats, collect_entries, monotonicity, random_entries
from signals.pullback import generate_pullback

HORIZONS = [3, 5, 10, 20]
PRIMARY = 5  # the pullback edge lives at the short horizon (3-5d reversion)
# (trend_ma, oversold, trend_mom): trend_mom>0 turns on the trend-strength filter
SETTINGS = [(100, 15.0, 0), (100, 15.0, 63), (100, 10.0, 63), (50, 15.0, 63)]
ROUND_TRIP_BPS = 4.0


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    u = load_universe("broad")
    cache = ParquetCache()
    frames = {s: cache.read(s, "1d") for s in u.tickers if cache.exists(s, "1d")}
    frames = {s: d for s, d in frames.items() if d is not None and len(d) > 250}
    rng = np.random.default_rng(42)

    print("=== STAGE-1 EDGE CHECK — pullback-in-trend (daily), beta-aware ===")
    print(f"{len(frames)} symbols | horizons(days) {HORIZONS} | primary h={PRIMARY}\n")

    any_pass = False
    for trend_ma, oversold, trend_mom in SETTINGS:
        real_parts, rand_parts = [], []
        for sym, df in frames.items():
            sig = generate_pullback(df, trend_ma=trend_ma, oversold=oversold,
                                    trend_mom=trend_mom)
            e = collect_entries(df, sig, HORIZONS)
            real_parts.append(e)
            bal = float((e["direction"] > 0).mean()) if len(e) else 0.5
            rand_parts.append(random_entries(df, len(e), bal, HORIZONS, rng))
        real = pd.concat(real_parts, ignore_index=True)
        rand = pd.concat(rand_parts, ignore_index=True)

        long_frac = float((real["direction"] > 0).mean()) if len(real) else float("nan")
        filt = f"+trendMom({trend_mom})" if trend_mom else "(no trend filter)"
        print(f"--- trend_ma={trend_ma}, RSI2<{oversold} {filt} (entries: {len(real)}, "
              f"{long_frac:.0%} long) ---")
        rows = []
        for h in HORIZONS:
            rs, zs = _stats(real[f"ret_{h}"]), _stats(rand[f"ret_{h}"])
            rows.append({"h": h, "n": rs["n"], "mean_bps": round(rs["mean_bps"], 1),
                         "hit": round(rs["hit_rate"], 3), "t": round(rs["t_stat"], 2),
                         "rand_mean": round(zs["mean_bps"], 1), "rand_hit": round(zs["hit_rate"], 3),
                         "beats_rand": rs["mean_bps"] > zs["mean_bps"]})
        print(pd.DataFrame(rows).to_string(index=False))
        mono = monotonicity(real, PRIMARY)
        print(f"  conviction monotonicity @h={PRIMARY}: {mono['buckets']} mono={mono['monotonic']}")
        r, z = _stats(real[f"ret_{PRIMARY}"]), _stats(rand[f"ret_{PRIMARY}"])
        net = r["mean_bps"] - ROUND_TRIP_BPS
        ok = (r["hit_rate"] >= 0.53 and r["t_stat"] >= 2.0 and net >= 5
              and r["mean_bps"] > z["mean_bps"] and r["hit_rate"] > z["hit_rate"])
        any_pass = any_pass or ok
        print(f"  @h={PRIMARY}: hit {r['hit_rate']:.3f}, t {r['t_stat']:.2f}, net {net:.1f}bps, "
              f"beats-random {r['mean_bps']>z['mean_bps'] and r['hit_rate']>z['hit_rate']} "
              f"-> {'PASS' if ok else 'fail'}\n")

    print("=== VERDICT ===")
    print("Pullback shows genuine (beta-beating) edge -> proceed to WFA/gauntlet + long-option test."
          if any_pass else "No setting beats the beta baseline at the primary horizon.")
    return 0 if any_pass else 1


if __name__ == "__main__":
    sys.exit(main())
