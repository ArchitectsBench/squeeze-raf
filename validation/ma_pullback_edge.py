"""Stage-1 edge-check for the MA-pullback entry (raw + market-neutral).

    uv run python -m validation.ma_pullback_edge

Tests whether 'pullback to the rising 20 SMA / 21 EMA in an uptrend' predicts a
forward bounce — for both the 20 SMA and 21 EMA, measured on RAW returns and on
RESIDUAL (beta-stripped) returns vs a random baseline. Beating random in RAW =
edge (maybe beta); beating it in RESIDUAL = genuine market-agnostic entry edge.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from data.universe import load_universe
from edge_check.event_study import _stats, collect_entries, monotonicity, random_entries
from signals.ma_pullback import generate_ma_pullback
from signals.market import residual_frame

HORIZONS = [3, 5, 10, 20]
PRIMARY = 10
# (ma_type, ma_len, strong_trend)
CONFIGS = [("sma", 20, True), ("ema", 21, True), ("sma", 20, False)]
ROUND_TRIP_BPS = 4.0


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    u = load_universe("broad")
    cache = ParquetCache()
    spy = cache.read("SPY", "1d")["close"]
    frames = {s: cache.read(s, "1d") for s in u.tickers
              if cache.exists(s, "1d") and s != "SPY"}
    frames = {s: d for s, d in frames.items() if d is not None and len(d) > 250}
    rng = np.random.default_rng(42)
    print("=== STAGE-1 EDGE — MA-pullback entry (daily) ===")
    print(f"{len(frames)} symbols | horizons {HORIZONS} | primary h={PRIMARY}\n")

    any_pass = False
    for ma_type, ma_len, strong in CONFIGS:
        for space in ("raw", "residual"):
            real_parts, rand_parts = [], []
            for sym, df in frames.items():
                src = df if space == "raw" else residual_frame(df, spy)
                sig = generate_ma_pullback(src, ma_len=ma_len, ma_type=ma_type,
                                           strong_trend=strong)
                e = collect_entries(src, sig, HORIZONS)
                real_parts.append(e)
                bal = float((e["direction"] > 0).mean()) if len(e) else 0.5
                rand_parts.append(random_entries(src, len(e), bal, HORIZONS, rng))
            real = pd.concat(real_parts, ignore_index=True)
            rand = pd.concat(rand_parts, ignore_index=True)

            tag = "strong-trend" if strong else "base"
            print(f"--- {ma_type.upper()}{ma_len} [{tag}] [{space}] (entries: {len(real)}) ---")
            rows = []
            for h in HORIZONS:
                rs, zs = _stats(real[f"ret_{h}"]), _stats(rand[f"ret_{h}"])
                rows.append({"h": h, "n": rs["n"], "mean_bps": round(rs["mean_bps"], 1),
                             "hit": round(rs["hit_rate"], 3), "t": round(rs["t_stat"], 2),
                             "rand_mean": round(zs["mean_bps"], 1),
                             "beats_rand": rs["mean_bps"] > zs["mean_bps"]})
            print(pd.DataFrame(rows).to_string(index=False))
            m = monotonicity(real, PRIMARY)
            print(f"  conviction monotonicity @h={PRIMARY}: {m['buckets']} mono={m['monotonic']}")
            r, z = _stats(real[f"ret_{PRIMARY}"]), _stats(rand[f"ret_{PRIMARY}"])
            net = r["mean_bps"] - ROUND_TRIP_BPS
            ok = (r["hit_rate"] >= 0.53 and r["t_stat"] >= 2.0 and net >= 5
                  and r["mean_bps"] > z["mean_bps"] and r["hit_rate"] > z["hit_rate"])
            any_pass = any_pass or (ok and space == "residual")
            print(f"  @h={PRIMARY}: hit {r['hit_rate']:.3f}, t {r['t_stat']:.2f}, net {net:.1f}bps, "
                  f"beats-random {r['mean_bps']>z['mean_bps'] and r['hit_rate']>z['hit_rate']} "
                  f"-> {'PASS' if ok else 'fail'}\n")

    print("=== VERDICT ===")
    print("MA-pullback shows market-neutral entry edge." if any_pass
          else "No market-agnostic edge at the primary horizon (raw may still beat random = beta).")
    return 0 if any_pass else 1


if __name__ == "__main__":
    sys.exit(main())
