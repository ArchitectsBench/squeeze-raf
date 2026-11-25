"""Stage-1 edge-check runner (P3).

    uv run python -m edge_check.run

Loads the cached pilot universe, runs the Confluence v0 event study with DEFAULT
params across modes A/B/C and timeframes 2h/4h/1d, and prints:
- the forward-return table (mean/median bps, hit rate, t-stat) vs a matched
  random-entry baseline, per mode/timeframe/horizon;
- conviction-monotonicity buckets at the primary horizon;
- a PASS/FAIL verdict per (mode, timeframe) against the locked edge gate.

No optimization. The OOS window is NOT touched (this is descriptive Stage-1).
"""

from __future__ import annotations

import sys

import pandas as pd

from data.cache import ParquetCache
from data.universe import load_universe

from .event_study import PRIMARY_HORIZON_BARS, run_study
from .gates import evaluate, load_edge_gate


def main(argv: list[str] | None = None) -> int:
    import argparse
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)

    ap = argparse.ArgumentParser(description="Stage-1 edge check")
    ap.add_argument("--universe", default="pilot", help="named universe (pilot/broad/...)")
    args = ap.parse_args(argv)

    universe = load_universe(args.universe)
    cache = ParquetCache()
    gate = load_edge_gate()
    horizons = gate["forward_horizons_bars"]

    print("=== STAGE-1 EDGE CHECK (Confluence v0, default params) ===")
    print(f"universe: {len(universe.tickers)} symbols | horizons(bars): {horizons} "
          f"| primary={PRIMARY_HORIZON_BARS}")
    print(f"gate: hit>={gate['min_hit_rate']}, t>={gate['min_fwd_return_t_stat']}, "
          f"net>=+{gate['fwd_return_must_exceed_cost_by_bps']}bps, beats-random, "
          f"monotonic={gate['require_monotonic_conviction']}\n")

    all_verdicts = []
    for tf in universe.timeframes:
        frames = {s: cache.read(s, tf) for s in universe.tickers if cache.exists(s, tf)}
        frames = {s: df for s, df in frames.items() if df is not None and len(df) > 100}
        if not frames:
            print(f"[{tf}] no cached data — skipping")
            continue
        summary, mono = run_study(frames, tf, horizons)

        print(f"\n----- timeframe {tf} ({len(frames)} symbols) -----")
        show = summary.copy()
        for c in ("mean_bps", "median_bps", "t_stat", "rand_mean_bps", "rand_t_stat"):
            show[c] = show[c].round(2)
        for c in ("hit_rate", "rand_hit_rate"):
            show[c] = show[c].round(3)
        print(show.to_string(index=False))

        print(f"  conviction monotonicity @h={PRIMARY_HORIZON_BARS} (mean bps per tercile):")
        for mode, m in mono.items():
            print(f"    mode {mode}: {m['buckets']} monotonic={m['monotonic']} {m['note']}")

        for mode in ("A", "B", "C"):
            v = evaluate(summary, mono, mode, tf, gate)
            all_verdicts.append(v)
            print(v)

    passes = [v for v in all_verdicts if v.passed]
    print("\n=== EDGE-CHECK SUMMARY ===")
    if passes:
        print(f"{len(passes)} (mode,timeframe) combo(s) PASS the locked gate:")
        for v in passes:
            print(f"    PASS  mode {v.mode} {v.timeframe}")
        print("\n-> Proceed to P4 (backtest) with the passing mode(s).")
        return 0
    print("No (mode,timeframe) combo passes the locked edge gate.")
    print("Per CLAUDE.md rule 3: do NOT optimize an unvalidated rule. Mutate the")
    print("rule or revisit indicator fidelity before continuing.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
