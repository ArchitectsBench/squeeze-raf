"""Stage-1 edge-check for the pairs / sector-relative signal (market-neutral).

    uv run python -m validation.pairs_edge

For each pair, when the spread is stretched (|z|>entry) we bet on reversion; the
forward return is the DOLLAR-NEUTRAL held return d*[Δ(logA) - beta*Δ(logB)] (beta
fixed at entry) — no market beta. Pooled vs a random baseline (random time +
direction), whose mean is ~0 because the spread has no drift. Beating it = genuine
market-agnostic mean-reversion alpha.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from edge_check.event_study import _stats
from signals.pairs import PAIRS, entries, pair_table

import argparse

HORIZONS = [5, 10, 20]
PRIMARY = 10
ROUND_TRIP_BPS = 8.0   # two legs in + two out (~2bps/side x4)


def _fwd_returns(table, ent, horizons, rng=None, randomize=False):
    """Per-entry dollar-neutral forward returns (bps) at each horizon."""
    la = table["la"].to_numpy()
    lb = table["lb"].to_numpy()
    beta = table["beta"].to_numpy()
    n = len(table)
    if randomize:
        valid = np.arange(n - max(horizons) - 1)
        k = int((ent != 0).sum())
        if k == 0 or len(valid) == 0:
            idx = np.array([], dtype=int)
            dirs = np.array([])
        else:
            idx = rng.choice(valid, size=min(k, len(valid)), replace=False)
            dirs = rng.choice([-1, 1], size=len(idx))
    else:
        idx = np.where(ent.to_numpy() != 0)[0]
        idx = idx[idx < n - max(horizons) - 1]
        dirs = ent.to_numpy()[idx]
    out = {h: [] for h in horizons}
    for t, d in zip(idx, dirs):
        for h in horizons:
            r = d * ((la[t + h] - la[t]) - beta[t] * (lb[t + h] - lb[t]))
            out[h].append(r * 1e4)
    return {h: pd.Series(v, dtype=float) for h, v in out.items()}


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    ap = argparse.ArgumentParser(description="pairs edge-check")
    ap.add_argument("--z", type=float, default=2.0, help="entry |z| threshold")
    ap.add_argument("--lookback", type=int, default=60)
    args = ap.parse_args(argv)
    entry_z, lookback = args.z, args.lookback
    cache = ParquetCache()
    rng = np.random.default_rng(42)
    print("=== STAGE-1 EDGE — pairs / sector-relative (market-neutral) ===")
    print(f"{len(PAIRS)} pairs | entry |z|>{entry_z} | lookback {lookback} | "
          f"horizons(days) {HORIZONS} | primary h={PRIMARY}\n")

    real = {h: [] for h in HORIZONS}
    rand = {h: [] for h in HORIZONS}
    n_entries = 0
    for a, b in PAIRS:
        da, db = cache.read(a, "1d"), cache.read(b, "1d")
        if da is None or db is None:
            continue
        idx = da.index.intersection(db.index)
        tbl = pair_table(da["close"].reindex(idx), db["close"].reindex(idx), lookback=lookback).dropna()
        if len(tbl) < 120:
            continue
        ent = entries(tbl, entry_z)
        n_entries += int((ent != 0).sum())
        rr = _fwd_returns(tbl, ent, HORIZONS)
        zz = _fwd_returns(tbl, ent, HORIZONS, rng=rng, randomize=True)
        for h in HORIZONS:
            real[h].append(rr[h])
            rand[h].append(zz[h])

    print(f"total entries across pairs: {n_entries}\n")
    rows = []
    for h in HORIZONS:
        rs = _stats(pd.concat(real[h], ignore_index=True))
        zs = _stats(pd.concat(rand[h], ignore_index=True))
        rows.append({"h": h, "n": rs["n"], "mean_bps": round(rs["mean_bps"], 1),
                     "hit": round(rs["hit_rate"], 3), "t": round(rs["t_stat"], 2),
                     "rand_mean": round(zs["mean_bps"], 1),
                     "beats_rand": rs["mean_bps"] > zs["mean_bps"]})
    tbl = pd.DataFrame(rows)
    print(tbl.to_string(index=False))

    r = _stats(pd.concat(real[PRIMARY], ignore_index=True))
    net = r["mean_bps"] - ROUND_TRIP_BPS
    ok = r["hit_rate"] >= 0.53 and r["t_stat"] >= 2.0 and net >= 5 and bool(tbl[tbl.h == PRIMARY]["beats_rand"].iloc[0])
    print(f"\n@h={PRIMARY}: hit {r['hit_rate']:.3f}, t {r['t_stat']:.2f}, net {net:.1f}bps "
          f"-> {'PASS' if ok else 'fail'}")
    print("VERDICT:", "pairs show market-neutral reversion edge -> proceed." if ok
          else "no market-neutral edge at the primary horizon.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
