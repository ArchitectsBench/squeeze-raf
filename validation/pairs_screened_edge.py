"""Out-of-sample edge-check for cointegration-SCREENED pairs (market-neutral).

    uv run python -m validation.pairs_screened_edge

Screen pairs on a TRAIN window (cointegration), then test the mean-reversion edge
ONLY on the later (OOS) window — measuring forward DOLLAR-NEUTRAL spread returns vs
a random baseline. This is the powered, look-ahead-safe test of the pairs lead.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from edge_check.event_study import _stats
from signals.pairs import entries, pair_table
from signals.pairs_screen import screen

HORIZONS = [5, 10, 20]
PRIMARY = 10
ENTRY_Z = 2.0
ROUND_TRIP_BPS = 8.0
TRAIN_END = pd.Timestamp("2024-06-18", tz="UTC")   # screen on <train_end, trade after


def _fwd(tbl, ent, test_mask, horizons, rng=None, randomize=False):
    la, lb, beta = tbl["la"].to_numpy(), tbl["lb"].to_numpy(), tbl["beta"].to_numpy()
    n = len(tbl)
    if randomize:
        valid = np.where(test_mask)[0]
        valid = valid[valid < n - max(horizons) - 1]
        k = int(((ent != 0) & test_mask).sum())
        idx = rng.choice(valid, size=min(k, len(valid)), replace=False) if (k and len(valid)) else np.array([], int)
        dirs = rng.choice([-1, 1], size=len(idx))
    else:
        idx = np.where((ent.to_numpy() != 0) & test_mask)[0]
        idx = idx[idx < n - max(horizons) - 1]
        dirs = ent.to_numpy()[idx]
    out = {h: [] for h in horizons}
    for t, d in zip(idx, dirs):
        for h in horizons:
            out[h].append(d * ((la[t + h] - la[t]) - beta[t] * (lb[t + h] - lb[t])) * 1e4)
    return {h: pd.Series(v, dtype=float) for h, v in out.items()}


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    cache = ParquetCache()
    rng = np.random.default_rng(42)
    frames = {}
    from data.universe import pairs_pool_tickers
    for t in pairs_pool_tickers():
        d = cache.read(t, "1d")
        if d is not None and len(d) > 300:
            frames[t] = d

    kept = screen(frames, TRAIN_END)
    print("=== PAIRS (cointegration-screened) — OOS edge-check ===")
    print(f"pool {len(frames)} names | screened pairs: {len(kept)} (coint p<0.05, HL 2-40d, on train)")
    print(f"train < {TRAIN_END.date()}, test after | entry |z|>{ENTRY_Z} | horizons {HORIZONS}\n")
    if not kept:
        print("no pairs passed the cointegration screen.")
        return 1
    print("sample screened pairs:", [(k["a"], k["b"], k["pvalue"], k["half_life"]) for k in kept[:8]])

    real = {h: [] for h in HORIZONS}
    rand = {h: [] for h in HORIZONS}
    n_entries = 0
    for k in kept:
        da, db = frames[k["a"]], frames[k["b"]]
        idx = da.index.intersection(db.index)
        tbl = pair_table(da["close"].reindex(idx), db["close"].reindex(idx), lookback=60).dropna()
        if len(tbl) < 200:
            continue
        ent = entries(tbl, ENTRY_Z)
        test_mask = np.asarray(tbl.index >= TRAIN_END)
        n_entries += int(((ent != 0) & test_mask).sum())
        rr = _fwd(tbl, ent, test_mask, HORIZONS)
        zz = _fwd(tbl, ent, test_mask, HORIZONS, rng=rng, randomize=True)
        for h in HORIZONS:
            real[h].append(rr[h])
            rand[h].append(zz[h])

    print(f"\nOOS entries (test window): {n_entries}\n")
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
    print(f"\n@h={PRIMARY}: hit {r['hit_rate']:.3f}, t {r['t_stat']:.2f}, net {net:.1f}bps -> {'PASS' if ok else 'fail'}")
    print("VERDICT:", "screened pairs show OOS market-neutral edge -> proceed to WFA/gauntlet." if ok
          else "no significant OOS market-neutral edge even after cointegration screening.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
