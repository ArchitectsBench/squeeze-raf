"""Stage-1 edge-check for PEAD (post-earnings drift), raw + market-neutral.

    uv run python -m validation.pead_edge

For each detected earnings reaction (volume-spike day near a filing date), does price
keep DRIFTING in the reaction direction over the next weeks? Forward returns signed by
the reaction direction, measured on RAW and RESIDUAL (beta-stripped) prices, vs a
random baseline. A strong-reaction subset is also shown (big surprises drift more).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from edge_check.event_study import _stats
from signals.market import residual_close
from signals.pead import detect_events

HORIZONS = [10, 20, 40]
PRIMARY = 20
EARN = Path(__file__).resolve().parent.parent / "data" / "earnings_dates.json"


def _fwd_for(price: pd.Series, t, sign, horizons):
    i = price.index.get_loc(t)
    out = {}
    for h in horizons:
        j = i + h
        out[h] = sign * (price.iloc[j] / price.iloc[i] - 1.0) * 1e4 if j < len(price) else np.nan
    return out


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    cache = ParquetCache()
    spy = cache.read("SPY", "1d")["close"]
    earn = json.loads(EARN.read_text())
    rng = np.random.default_rng(42)

    raw = {h: [] for h in HORIZONS}
    res = {h: [] for h in HORIZONS}
    strong = {h: [] for h in HORIZONS}     # |reaction| > 3%
    rand = {h: [] for h in HORIZONS}
    n_ev = 0
    for sym, dates in earn.items():
        df = cache.read(sym, "1d")
        if df is None or len(df) < 300:
            continue
        rc = residual_close(df["close"], spy.reindex(df.index).ffill())
        evs = detect_events(df, dates)
        for t, reaction in evs:
            sgn = 1 if reaction > 0 else -1
            n_ev += 1
            fr = _fwd_for(df["close"], t, sgn, HORIZONS)
            fres = _fwd_for(rc, t, sgn, HORIZONS)
            for h in HORIZONS:
                raw[h].append(fr[h])
                res[h].append(fres[h])
                if abs(reaction) > 0.03:
                    strong[h].append(fr[h])
            # random baseline: random day in this name, random direction
            i = rng.integers(20, len(df) - max(HORIZONS) - 1)
            rd = 1 if rng.random() < 0.5 else -1
            for h in HORIZONS:
                rand[h].append(rd * (df["close"].iloc[i + h] / df["close"].iloc[i] - 1.0) * 1e4)

    print("=== STAGE-1 EDGE — PEAD (post-earnings drift) ===")
    print(f"{len(earn)} names | earnings events detected: {n_ev} | horizons(days) {HORIZONS}\n")
    for label, d in [("RAW drift", raw), ("RESIDUAL drift", res), ("STRONG (|react|>3%) raw", strong)]:
        print(f"--- {label} ---")
        rows = []
        for h in HORIZONS:
            rs = _stats(pd.Series(d[h], dtype=float))
            zs = _stats(pd.Series(rand[h], dtype=float))
            rows.append({"h": h, "n": rs["n"], "mean_bps": round(rs["mean_bps"], 1),
                         "hit": round(rs["hit_rate"], 3), "t": round(rs["t_stat"], 2),
                         "rand_mean": round(zs["mean_bps"], 1),
                         "beats_rand": rs["mean_bps"] > zs["mean_bps"]})
        print(pd.DataFrame(rows).to_string(index=False), "\n")

    r = _stats(pd.Series(res[PRIMARY], dtype=float))
    ok = r["hit_rate"] >= 0.53 and r["t_stat"] >= 2.0
    print("VERDICT (residual @h=%d): hit %.3f t %.2f -> %s" %
          (PRIMARY, r["hit_rate"], r["t_stat"], "market-neutral PEAD edge" if ok else "not significant"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
