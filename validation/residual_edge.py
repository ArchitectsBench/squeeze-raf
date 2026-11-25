"""Stage-1 edge-check on RESIDUAL (market-neutral) returns.

    uv run python -m validation.residual_edge

Runs the pullback signal on each name's beta-stripped 'residual price' and measures
RESIDUAL forward returns (stock minus beta*SPY — what a beta-neutral position
captures), vs a random baseline. This is the truly market-agnostic test: the market
drift is removed from BOTH the signal and the return, so any edge is idiosyncratic
stock-selection alpha, not beta. Compares against the raw (with-beta) version.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from data.universe import load_universe
from edge_check.event_study import _stats, collect_entries, monotonicity, random_entries
from signals.market import residual_frame
from signals.pullback import generate_pullback

HORIZONS = [3, 5, 10]
PRIMARY = 5


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    u = load_universe("broad")
    cache = ParquetCache()
    spy = cache.read("SPY", "1d")["close"]
    frames = {s: cache.read(s, "1d") for s in u.tickers if cache.exists(s, "1d")}
    frames = {s: d for s, d in frames.items() if d is not None and len(d) > 250 and s != "SPY"}
    rng = np.random.default_rng(42)

    print("=== STAGE-1 EDGE — pullback on RESIDUAL (market-neutral) returns ===")
    print(f"{len(frames)} symbols vs SPY | horizons {HORIZONS} | primary h={PRIMARY}\n")

    for mode in ("raw", "residual"):
        real_parts, rand_parts = [], []
        for sym, df in frames.items():
            src = df if mode == "raw" else residual_frame(df, spy)
            sig = generate_pullback(src, trend_ma=100, oversold=15.0, trend_mom=63)
            e = collect_entries(src, sig, HORIZONS)   # fwd returns measured on `src`
            real_parts.append(e)
            bal = float((e["direction"] > 0).mean()) if len(e) else 0.5
            rand_parts.append(random_entries(src, len(e), bal, HORIZONS, rng))
        real = pd.concat(real_parts, ignore_index=True)
        rand = pd.concat(rand_parts, ignore_index=True)

        print(f"--- {mode.upper()} returns (entries: {len(real)}) ---")
        rows = []
        for h in HORIZONS:
            rs, zs = _stats(real[f"ret_{h}"]), _stats(rand[f"ret_{h}"])
            rows.append({"h": h, "n": rs["n"], "mean_bps": round(rs["mean_bps"], 1),
                         "hit": round(rs["hit_rate"], 3), "t": round(rs["t_stat"], 2),
                         "rand_mean": round(zs["mean_bps"], 1),
                         "beats_rand": rs["mean_bps"] > zs["mean_bps"]})
        print(pd.DataFrame(rows).to_string(index=False))
        m = monotonicity(real, PRIMARY)
        print(f"  conviction monotonicity @h={PRIMARY}: {m['buckets']} mono={m['monotonic']}\n")
    print("Read: in RESIDUAL space the random baseline ~0 (market drift removed); a "
          "signal that still beats it has genuine market-agnostic stock-selection alpha.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
