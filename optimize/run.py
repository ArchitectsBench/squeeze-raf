"""P5 optimization runner (coarse grid -> Optuna, anchored walk-forward).

    uv run python -m optimize.run --universe broad

Per timeframe: builds the research range (final OOS window LOCKED out), runs the
coarse grid + Optuna refinement, then anchored walk-forward. Prints top-20 configs,
WFA efficiency (OOS/IS), the ≥30-trades/free-param budget check, a parameter
plateau heatmap (PNG), and a knife-edge flag. Persists the best config per
timeframe to optimize/best_configs.json for the P6 gauntlet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from data.cache import ParquetCache  # noqa: E402
from data.universe import load_universe  # noqa: E402

from .common import SignalCache, ann_factor, oos_months  # noqa: E402
from .grid import GRID, N_FREE_PARAMS, run_grid  # noqa: E402
from .optuna_search import refine  # noqa: E402
from .walkforward import run_wfa  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "tearsheets"
BEST_PATH = Path(__file__).resolve().parent / "best_configs.json"
MIN_TRADES = 30 * N_FREE_PARAMS


def plateau_heatmap(grid_df: pd.DataFrame, tf: str, path: Path):
    piv = grid_df.pivot_table(index="atr_stop_mult", columns="profit_target_R",
                              values="sharpe", aggfunc="mean")
    fig, ax = plt.subplots(figsize=(6, 4.5))
    im = ax.imshow(piv.values, cmap="viridis", aspect="auto", origin="lower")
    ax.set_xticks(range(len(piv.columns)), piv.columns)
    ax.set_yticks(range(len(piv.index)), piv.index)
    ax.set_xlabel("profit_target_R")
    ax.set_ylabel("atr_stop_mult")
    ax.set_title(f"Mode A {tf}: mean research Sharpe (plateau check)")
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            ax.text(j, i, f"{piv.values[i, j]:.2f}", ha="center", va="center",
                    color="w", fontsize=8)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def knife_edge(grid_df: pd.DataFrame) -> tuple[bool, str]:
    """Flag if the top config is an isolated spike vs the rest of the top-10."""
    top = grid_df.iloc[0]["sharpe"]
    rest = grid_df.iloc[1:10]["sharpe"].mean()
    gap = top - rest
    suspect = gap > 0.5 and rest < top * 0.6
    return suspect, f"top={top:.2f} vs mean(next 9)={rest:.2f} (gap {gap:.2f})"


def main(argv: list[str] | None = None) -> int:
    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 30)
    ap = argparse.ArgumentParser(description="P5 optimize + walk-forward")
    ap.add_argument("--universe", default="broad")
    ap.add_argument("--optuna-trials", type=int, default=40)
    ap.add_argument("--wf-folds", type=int, default=4)
    args = ap.parse_args(argv)

    universe = load_universe(args.universe)
    cost_class = {s.ticker: s.cost_class for s in universe.symbols}
    cache_store = ParquetCache()
    months = oos_months()

    print(f"=== P5 OPTIMIZE — Mode A, universe '{args.universe}' ===")
    print(f"OOS LOCKED: final {months} months excluded from all P5 work. "
          f"grid={len(GRID)} dims, {N_FREE_PARAMS} free; budget >= {MIN_TRADES} trades\n")

    best_configs = {}
    for tf in universe.timeframes:
        frames = {s: cache_store.read(s, tf) for s in universe.tickers
                  if cache_store.exists(s, tf)}
        frames = {s: df for s, df in frames.items() if df is not None and len(df) > 200}
        if not frames:
            continue
        max_ts = max(df.index.max() for df in frames.values())
        cut = max_ts - pd.DateOffset(months=months)  # research/holdout boundary
        ann = ann_factor(tf)

        cache = SignalCache(frames=frames, timeframe=tf)
        grid_df, ret_grid, keys_grid = run_grid(cache, cost_class, cut, ann)
        opt_df, ret_opt, keys_opt = refine(cache, cost_class, cut, ann,
                                           n_trials=args.optuna_trials)

        combined = pd.concat([grid_df.assign(src="grid"), opt_df.assign(src="optuna")],
                             ignore_index=True).sort_values("sharpe", ascending=False)
        combined = combined.reset_index(drop=True)

        # research index for WFA (any symbol's research-range bars)
        ref = next(iter(frames.values()))
        research_idx = ref.index[ref.index < cut]
        wfa = run_wfa(ret_grid, [dict(zip(GRID, k)) for k in keys_grid], keys_grid,
                      research_idx, ann, n_folds=args.wf_folds)
        ke_flag, ke_msg = knife_edge(grid_df)

        print(f"\n----- timeframe {tf} ({len(frames)} symbols) -----")
        cols = list(GRID) + ["sharpe", "n_trades", "src"]
        print("TOP 20 (by research Sharpe):")
        print(combined[cols].head(20).to_string(index=False))
        print(f"\n  WFA folds={wfa.n_folds} | train Sharpe={['%.2f'%s for s in wfa.train_sharpes]}"
              f" | test Sharpe={['%.2f'%s for s in wfa.test_sharpes]}")
        print(f"  WFA efficiency (OOS/IS) = {wfa.efficiency:.2f} | mean test Sharpe = {wfa.mean_test_sharpe:.2f}")
        budget_ok = combined.iloc[0]["n_trades"] >= MIN_TRADES
        print(f"  trade budget: top config has {int(combined.iloc[0]['n_trades'])} trades "
              f"(need >= {MIN_TRADES}) -> {'OK' if budget_ok else 'INSUFFICIENT'}")
        print(f"  knife-edge: {'SUSPECT' if ke_flag else 'ok (broad plateau)'} [{ke_msg}]")
        plateau_heatmap(grid_df, tf, OUT_DIR / f"plateau_A_{tf}_{args.universe}.png")
        print(f"  plateau heatmap -> {OUT_DIR}/plateau_A_{tf}_{args.universe}.png")

        best = combined.iloc[0]
        best_configs[tf] = {k: (int(best[k]) if k != "atr_stop_mult" and k != "profit_target_R"
                                else float(best[k])) for k in GRID}
        best_configs[tf].update(research_sharpe=float(best["sharpe"]),
                                wfa_efficiency=float(wfa.efficiency),
                                mean_test_sharpe=float(wfa.mean_test_sharpe),
                                n_trades=int(best["n_trades"]),
                                knife_edge_suspect=bool(ke_flag))

    BEST_PATH.write_text(json.dumps(best_configs, indent=2))
    print(f"\nbest configs -> {BEST_PATH}")
    print("\nNote: all Sharpes here are RESEARCH-range (OOS window still locked for P6).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
