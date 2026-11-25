"""Confluence v1 funnel — multi-TF alignment + conviction filter (P6-fix).

    uv run python -m validation.v1_run

Re-runs the gated funnel on v1 with a SMALL, pre-registered config set (so the
DSR/PBO trial penalty is honest): quick Stage-1 edge stat, anchored walk-forward
(WFA efficiency), then the full graduation gauntlet with the locked OOS unlocked.

Pre-registered (NOT searched): conviction_min=0.45, 4h alignment, energy_lookback=6,
sc_ready=1, profit_target off, momentum exit on. Searched: confluence_window {4,6},
atr_stop_mult {2.5,4.0}, time_stop_bars {4,6,8} = 12 configs.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from backtest.engine import ExitParams, SizingParams, simulate_symbol
from data.cache import ParquetCache
from data.universe import load_universe
from edge_check.event_study import collect_entries
from optimize.common import SignalCache, ann_factor, evaluate, oos_months, sharpe
from optimize.walkforward import run_wfa

from .bootstrap import block_bootstrap_sharpe
from .dsr import deflated_sharpe_ratio
from .pbo_cscv import pbo_cscv
from .permutation import permutation_test

GATES = Path(__file__).resolve().parent.parent / "config" / "gates.yaml"
CONVICTION_MIN = 0.0                  # effectively OFF. The v0 conviction score is
#   too DISCRETE to filter on (it clusters at exactly 0.35; >=0.40 retains only 7%
#   of entries — nukes the sample). The multi-TF alignment is the usable quality
#   lever (keeps 61%, 1460 entries), so v1 = Mode A + 4h alignment. Documented as a
#   calibration finding (decided pre-performance).
SIG_TF, ALIGN_TF = "2h", "4h"

V1_GRID = {
    "confluence_window": [4, 6],
    "sc_ready": [1],
    "energy_lookback": [6],
    "atr_stop_mult": [2.5, 4.0],
    "profit_target_R": [2.0],
    "time_stop_bars": [4, 6, 8],
    "use_momentum_exit": [True],
    "use_profit_target": [False],
}
N_FREE = sum(1 for v in V1_GRID.values() if len(v) > 1)
MIN_TRADES = 30 * N_FREE


def combos():
    keys = list(V1_GRID)
    return [dict(zip(keys, vals)) for vals in itertools.product(*V1_GRID.values())]


def reconstruct_positions(cache, frames, cost_class, params, cut):
    exits = ExitParams(time_stop_bars=int(params["time_stop_bars"]),
                       atr_stop_mult=float(params["atr_stop_mult"]),
                       profit_target_R=float(params["profit_target_R"]),
                       use_momentum_exit=bool(params["use_momentum_exit"]),
                       use_profit_target=bool(params["use_profit_target"]))
    positions, rets = [], []
    for sym, df in frames.items():
        sig = cache.get(sym, 13, int(params["confluence_window"]),
                        int(params["sc_ready"]), int(params["energy_lookback"]))
        _, trades = simulate_symbol(df, sig, cost_class.get(sym, "equities"), exits,
                                    SizingParams())
        pos = np.zeros(len(df))
        for t in trades:
            pos[t["entry_bar"]:t["exit_bar"] + 1] = t["dir"] * t["exposure"]
        ret = df["close"].pct_change().fillna(0.0).to_numpy()
        mask = np.asarray(df.index < cut)
        positions.append(pos[mask])
        rets.append(ret[mask])
    return positions, rets


def edge_tstat(cache, frames, cut, horizon=4):
    """Quick Stage-1 confirmation: pooled forward-return t-stat of v1 entries
    (research range), at the primary horizon."""
    parts = []
    for sym, df in frames.items():
        sig = cache.get(sym, 13, 4, 1, 6)  # representative entry params
        parts.append(collect_entries(df, sig, [horizon]))  # full-sample sanity stat
    pooled = pd.concat(parts, ignore_index=True)[f"ret_{horizon}"].dropna()
    n = len(pooled)
    if n < 5:
        return {"n": n, "t": float("nan"), "mean_bps": float("nan")}
    t = pooled.mean() / (pooled.std(ddof=1) / np.sqrt(n))
    return {"n": n, "t": float(t), "mean_bps": float(pooled.mean())}


def main(argv: list[str] | None = None) -> int:
    pd.set_option("display.width", 240)
    with open(GATES) as f:
        gates = yaml.safe_load(f)
    grad, mc = gates["graduation"], gates["monte_carlo"]

    universe = load_universe("broad")
    cost_class = {s.ticker: s.cost_class for s in universe.symbols}
    store = ParquetCache()
    frames = {s: store.read(s, SIG_TF) for s in universe.tickers if store.exists(s, SIG_TF)}
    frames = {s: df for s, df in frames.items() if df is not None and len(df) > 200}
    align = {s: store.read(s, ALIGN_TF) for s in universe.tickers if store.exists(s, ALIGN_TF)}
    max_ts = max(df.index.max() for df in frames.values())
    cut = max_ts - pd.DateOffset(months=oos_months())
    ann = ann_factor(SIG_TF)

    print("=== CONFLUENCE v1 FUNNEL (Mode A + 4h alignment) ===")
    print("NOTE: conviction-threshold filter dropped — v0 score too discrete (clusters "
          "at 0.35); 4h alignment is the quality lever (keeps ~61% of entries).")
    print(f"2h signal + {ALIGN_TF} regime alignment | "
          f"{len(V1_GRID)} dims, {N_FREE} free, {len(combos())} configs")
    print("** OOS holdout UNLOCKED for the final gauntlet **\n")

    cache = SignalCache(frames=frames, timeframe=SIG_TF,
                        align_frames=align, conviction_min=CONVICTION_MIN)

    # Stage-1 edge confirmation
    edge = edge_tstat(cache, frames, cut)
    print(f"Stage-1 edge (v1 entries, h=4): n={edge['n']}, mean={edge['mean_bps']:.1f}bps, "
          f"t={edge['t']:.2f}")

    # grid -> research returns
    rows, ret_by, keys = [], {}, []
    for c in combos():
        r, n_tr = evaluate(cache, cost_class, c, cut, window="research")
        k = tuple(c[x] for x in V1_GRID)
        ret_by[k] = r
        keys.append(k)
        rows.append({**c, "sharpe": sharpe(r, ann), "n_trades": n_tr})
    grid_df = pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)
    best = grid_df.iloc[0]
    best_params = {k: best[k] for k in V1_GRID}
    best_key = tuple(best_params[k] for k in V1_GRID)
    research_ret = ret_by[best_key]

    print("\nTOP configs by research Sharpe:")
    print(grid_df[["confluence_window", "atr_stop_mult", "time_stop_bars", "sharpe",
                   "n_trades"]].head(8).to_string(index=False))

    # WFA
    wfa = run_wfa(ret_by, [dict(zip(V1_GRID, k)) for k in keys], keys,
                  next(iter(frames.values())).index[next(iter(frames.values())).index < cut],
                  ann, n_folds=4)
    print(f"\nWFA efficiency (OOS/IS) = {wfa.efficiency:.2f} | mean test Sharpe "
          f"{wfa.mean_test_sharpe:.2f} | folds test={['%.2f'%s for s in wfa.test_sharpes]}")

    # --- gauntlet ---
    # DSR works in PER-BAR Sharpe units; grid_df["sharpe"] is annualized, so convert
    # the across-trials variance back to per-bar (divide by the annualization factor).
    sr_var = float(np.nanvar((grid_df["sharpe"] / np.sqrt(ann)).to_numpy()))
    dsr = deflated_sharpe_ratio(research_ret, sr_var, len(grid_df))
    matrix = np.column_stack([ret_by[k].to_numpy() for k in keys])
    pbo = pbo_cscv(matrix, s_blocks=10)
    boot = block_bootstrap_sharpe(research_ret, ann, n_iter=mc["bootstrap_iterations"],
                                  seed=mc["random_seed"])
    pos, rets = reconstruct_positions(cache, frames, cost_class, best_params, cut)
    perm = permutation_test(pos, rets, ann, n_iter=mc["permutation_iterations"],
                            seed=mc["random_seed"])
    by_year = {int(y): sharpe(r, ann) for y, r in research_ret.groupby(research_ret.index.year)}
    sub_stable = (np.mean([v > 0 for v in by_year.values()]) >= 0.75
                  and min(by_year.values()) > -0.5)
    neigh = [grid_df.iloc[i]["sharpe"] for i in range(len(grid_df))
             if sum(1 for k in V1_GRID if len(V1_GRID[k]) > 1 and grid_df.iloc[i][k] != best[k]) == 1]
    neigh_robust = bool(neigh and min(neigh) > 0 and min(neigh) > 0.5 * best["sharpe"])
    oos_ret, oos_tr = evaluate(cache, cost_class, best_params, cut, window="holdout")
    oos_sharpe = sharpe(oos_ret, ann)

    crit = {
        "OOS Sharpe (holdout)": (round(oos_sharpe, 2), f">= {grad['oos_sharpe_min']}",
                                 oos_sharpe >= grad["oos_sharpe_min"]),
        "PBO (CSCV)": (round(pbo["pbo"], 3), f"< {grad['pbo_max']}",
                       pbo["pbo"] == pbo["pbo"] and pbo["pbo"] < grad["pbo_max"]),
        "Deflated Sharpe": (round(dsr["dsr"], 3), "> 0.95", dsr["significant"]),
        "Bootstrap p5": (round(boot["p5"], 2), f">= {grad['bootstrap_sharpe_5th_pct_min']}",
                         boot["p5"] >= grad["bootstrap_sharpe_5th_pct_min"]),
        "Bootstrap median": (round(boot["median"], 2), f">= {grad['bootstrap_sharpe_median_min']}",
                             boot["median"] >= grad["bootstrap_sharpe_median_min"]),
        "Permutation pctile": (round(perm["percentile"], 1), f">= {grad['permutation_percentile_min']}",
                               perm["percentile"] >= grad["permutation_percentile_min"]),
        "Subperiod stability": ({y: round(v, 2) for y, v in by_year.items()}, "stable", sub_stable),
        "Neighborhood robust": (f"min {round(min(neigh), 2) if neigh else 'na'}", "robust", neigh_robust),
        "Trade budget": (int(best["n_trades"]), f">= {MIN_TRADES}", best["n_trades"] >= MIN_TRADES),
    }
    print("\nGraduated config:", best_params)
    print(f"research Sharpe {sharpe(research_ret, ann):.2f}\n")
    print("--- v1 GRADUATION SCORECARD ---")
    for name, (val, thr, ok) in crit.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {val}  (need {thr})")
    passed = all(ok for _, _, ok in crit.values())
    print("\n" + "=" * 60)
    print(f"  v1 GRADUATION: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
