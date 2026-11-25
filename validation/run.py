"""P6 graduation gauntlet runner (BUILD_SPEC.md §5 Stage 4; gates.yaml graduation).

    uv run python -m validation.run --universe broad --timeframe 2h

Runs the full overfitting battery on the P5-graduated config and — for the FIRST
time — evaluates the LOCKED OOS holdout. Produces one table with every metric and a
PASS/FAIL against the pre-registered graduation thresholds. Deterministic seeds.

This is the only stage permitted to read the holdout window.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from backtest.engine import ExitParams, SizingParams, simulate_symbol
from data.cache import ParquetCache
from data.universe import load_universe
from optimize.common import SignalCache, ann_factor, evaluate, oos_months, sharpe
from optimize.grid import GRID, run_grid

from .bootstrap import block_bootstrap_sharpe
from .dsr import deflated_sharpe_ratio
from .pbo_cscv import pbo_cscv
from .permutation import permutation_test

GATES = Path(__file__).resolve().parent.parent / "config" / "gates.yaml"


def graduation_gate() -> dict:
    with open(GATES) as f:
        g = yaml.safe_load(f)
    return g["graduation"], g["monte_carlo"]


def reconstruct_positions(frames, cache, cost_class, params, cut):
    """Per-symbol (signed-exposure position array, close-to-close return array) over
    the research range, for the permutation test."""
    length = 21 if cache.timeframe == "1d" else 13
    exits = ExitParams(time_stop_bars=int(params["time_stop_bars"]),
                       atr_stop_mult=float(params["atr_stop_mult"]),
                       profit_target_R=float(params["profit_target_R"]),
                       use_momentum_exit=bool(params["use_momentum_exit"]),
                       use_profit_target=bool(params["use_profit_target"]))
    positions, rets = [], []
    for sym, df in frames.items():
        sig = cache.get(sym, length, int(params["confluence_window"]),
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


def subperiod_stability(returns: pd.Series, ann: int) -> dict:
    by_year = {int(y): sharpe(r, ann) for y, r in returns.groupby(returns.index.year)}
    vals = list(by_year.values())
    pos_frac = np.mean([v > 0 for v in vals]) if vals else 0.0
    return {"by_year": {y: round(v, 2) for y, v in by_year.items()},
            "stable": bool(pos_frac >= 0.75 and min(vals) > -0.5)}


def neighborhood(grid_df: pd.DataFrame, best: pd.Series) -> dict:
    """One-step grid neighbors of the best config; robust if they stay positive and
    near the best (broad plateau, not a spike)."""
    steps = {k: sorted(v) for k, v in GRID.items() if len(v) > 1}
    neigh = []
    for _, row in grid_df.iterrows():
        diffs = sum(1 for k in steps if row[k] != best[k])
        if diffs == 1:
            neigh.append(row["sharpe"])
    if not neigh:
        return {"n": 0, "min": float("nan"), "mean": float("nan"), "robust": False}
    return {"n": len(neigh), "min": float(min(neigh)), "mean": float(np.mean(neigh)),
            "robust": bool(min(neigh) > 0 and min(neigh) > 0.5 * best["sharpe"])}


def main(argv: list[str] | None = None) -> int:
    pd.set_option("display.width", 240)
    ap = argparse.ArgumentParser(description="P6 graduation gauntlet")
    ap.add_argument("--universe", default="broad")
    ap.add_argument("--timeframe", default="2h")
    args = ap.parse_args(argv)
    grad, mc = graduation_gate()
    tf = args.timeframe

    universe = load_universe(args.universe)
    cost_class = {s.ticker: s.cost_class for s in universe.symbols}
    store = ParquetCache()
    frames = {s: store.read(s, tf) for s in universe.tickers if store.exists(s, tf)}
    frames = {s: df for s, df in frames.items() if df is not None and len(df) > 200}
    max_ts = max(df.index.max() for df in frames.values())
    cut = max_ts - pd.DateOffset(months=oos_months())
    ann = ann_factor(tf)

    print(f"=== P6 GRADUATION GAUNTLET — Mode A, {tf}, '{args.universe}' ===")
    print("** OOS holdout is now UNLOCKED for evaluation (first and only time) **\n")

    cache = SignalCache(frames=frames, timeframe=tf)
    grid_df, ret_by_combo, keys = run_grid(cache, cost_class, cut, ann)
    best = grid_df.iloc[0]
    best_params = {k: best[k] for k in GRID}
    best_key = tuple(best_params[k] for k in GRID)
    research_ret = ret_by_combo[best_key]

    # --- battery ---
    n_trials = len(grid_df) + 40  # grid + the Optuna budget from P5
    # DSR works in PER-BAR Sharpe units; grid_df["sharpe"] is annualized -> convert.
    sr_var = float(np.nanvar((grid_df["sharpe"] / np.sqrt(ann)).to_numpy()))
    dsr = deflated_sharpe_ratio(research_ret, sr_var, n_trials)

    matrix = np.column_stack([ret_by_combo[k].to_numpy() for k in keys])
    pbo = pbo_cscv(matrix, s_blocks=10)

    boot = block_bootstrap_sharpe(research_ret, ann,
                                  n_iter=mc["bootstrap_iterations"], seed=mc["random_seed"])
    pos, rets = reconstruct_positions(frames, cache, cost_class, best_params, cut)
    perm = permutation_test(pos, rets, ann, n_iter=mc["permutation_iterations"],
                            seed=mc["random_seed"])
    sub = subperiod_stability(research_ret, ann)
    neigh = neighborhood(grid_df, best)
    oos_ret, oos_trades = evaluate(cache, cost_class, best_params, cut, window="holdout")
    oos_sharpe = sharpe(oos_ret, ann)

    # --- report ---
    print("Graduated config (best by research Sharpe):")
    for k in GRID:
        print(f"    {k} = {best_params[k]}")
    print(f"\n  research Sharpe (IS) = {sharpe(research_ret, ann):.2f} | trades = {int(best['n_trades'])}")

    crit = {
        "OOS Sharpe (holdout)": (round(oos_sharpe, 2), f">= {grad['oos_sharpe_min']}",
                                 oos_sharpe >= grad["oos_sharpe_min"]),
        "PBO (CSCV)": (round(pbo["pbo"], 3) if pbo["pbo"] == pbo["pbo"] else "nan",
                       f"< {grad['pbo_max']}",
                       pbo["pbo"] == pbo["pbo"] and pbo["pbo"] < grad["pbo_max"]),
        "Deflated Sharpe": (round(dsr["dsr"], 3) if dsr["dsr"] == dsr["dsr"] else "nan",
                            "> 0.95 (sig)", dsr["significant"]),
        "Bootstrap Sharpe p5": (round(boot["p5"], 2), f">= {grad['bootstrap_sharpe_5th_pct_min']}",
                                boot["p5"] >= grad["bootstrap_sharpe_5th_pct_min"]),
        "Bootstrap Sharpe median": (round(boot["median"], 2), f">= {grad['bootstrap_sharpe_median_min']}",
                                    boot["median"] >= grad["bootstrap_sharpe_median_min"]),
        "Permutation pctile": (round(perm["percentile"], 1), f">= {grad['permutation_percentile_min']}",
                               perm["percentile"] >= grad["permutation_percentile_min"]),
        "Subperiod stability": (sub["by_year"], "stable", sub["stable"]),
        "Neighborhood robust": (f"min {round(neigh['min'],2)} of {neigh['n']}", "robust",
                                neigh["robust"]),
    }
    print("\n--- GRADUATION SCORECARD ---")
    for name, (val, thr, ok) in crit.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {val}  (need {thr})")

    passed = all(ok for _, _, ok in crit.values())
    print("\n" + "=" * 60)
    print(f"  GRADUATION: {'PASS — strategy graduates' if passed else 'FAIL — does not graduate'}")
    print("=" * 60)
    print(f"\n  (OOS holdout: {oos_trades} trades; bootstrap {boot['n_iter']} iters; "
          f"PBO {pbo['n_combos']} CSCV combos; perm {mc['permutation_iterations']} iters; "
          f"n_trials for DSR={n_trials})")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
