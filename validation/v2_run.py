"""Confluence v2 funnel — DAILY swing signal + WEEKLY alignment (higher-TF push).

    uv run python -m validation.v2_run

Hypothesis (from the P8 timeframe sweep): the options overlay only works at the 1d
horizon (big enough moves), but the 1d Mode A signal isn't WFA-robust. v2 applies
the v1 recipe one timeframe up — daily Mode A entries gated by the WEEKLY EMA-ribbon
trend, swing-tuned exits (wide catastrophe stop, ~1-2 week time stop) — and runs the
full gated funnel: Stage-1 edge stat -> anchored walk-forward -> graduation gauntlet
with the locked OOS unlocked. Small pre-registered grid (honest DSR/PBO).
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
from optimize.common import oos_months, sharpe
from optimize.walkforward import run_wfa
from signals import ConfluenceParams, generate_v1, weekly_regime

from .bootstrap import block_bootstrap_sharpe
from .dsr import deflated_sharpe_ratio
from .pbo_cscv import pbo_cscv
from .permutation import permutation_test

GATES = Path(__file__).resolve().parent.parent / "config" / "gates.yaml"
TF, ANN = "1d", 252
LENGTH = 21  # daily squeeze length

V2_GRID = {
    "confluence_window": [4, 6],
    "sc_ready": [1],
    "energy_lookback": [6],
    "atr_stop_mult": [2.5, 4.0],
    "profit_target_R": [2.0],
    "time_stop_bars": [5, 8, 10],     # ~1-2 week swing hold (daily bars)
    "use_momentum_exit": [True],
    "use_profit_target": [False],
}
N_FREE = sum(1 for v in V2_GRID.values() if len(v) > 1)
MIN_TRADES = 30 * N_FREE


def combos():
    keys = list(V2_GRID)
    return [dict(zip(keys, vals)) for vals in itertools.product(*V2_GRID.values())]


class V2Signals:
    """Daily Mode A + weekly-ribbon alignment, memoized per entry-param tuple."""
    def __init__(self, frames):
        self.frames = frames
        self._align = {s: weekly_regime(df, df.index) for s, df in frames.items()}
        self._cache = {}

    def get(self, sym, cw, sc, el):
        key = (sym, cw, sc, el)
        if key not in self._cache:
            p = ConfluenceParams(length=LENGTH, confluence_window=cw, sc_ready=sc,
                                 energy_lookback=el)
            al, ash = self._align[sym]
            self._cache[key] = generate_v1(self.frames[sym], p, 0.0, al, ash)
        return self._cache[key]


def _exits(params):
    return ExitParams(time_stop_bars=int(params["time_stop_bars"]),
                      atr_stop_mult=float(params["atr_stop_mult"]),
                      profit_target_R=float(params["profit_target_R"]),
                      use_momentum_exit=bool(params["use_momentum_exit"]),
                      use_profit_target=bool(params["use_profit_target"]))


def evaluate(sigs, cost_class, params, cut, window="research"):
    per, n = {}, 0
    for sym, df in sigs.frames.items():
        sig = sigs.get(sym, int(params["confluence_window"]), int(params["sc_ready"]),
                       int(params["energy_lookback"]))
        r, trades = simulate_symbol(df, sig, cost_class.get(sym, "equities"),
                                    _exits(params), SizingParams())
        s = pd.Series(r, index=df.index)
        mask = s.index < cut if window == "research" else (
            s.index >= cut if window == "holdout" else s.index == s.index)
        per[sym] = s[mask]
        n += sum(1 for t in trades
                 if (df.index[t["entry_bar"]] < cut) == (window == "research"))
    return pd.DataFrame(per).sort_index().mean(axis=1).fillna(0.0), n


def positions(sigs, cost_class, params, cut):
    pos_list, rets = [], []
    for sym, df in sigs.frames.items():
        sig = sigs.get(sym, int(params["confluence_window"]), int(params["sc_ready"]),
                       int(params["energy_lookback"]))
        _, trades = simulate_symbol(df, sig, cost_class.get(sym, "equities"),
                                    _exits(params), SizingParams())
        p = np.zeros(len(df))
        for t in trades:
            p[t["entry_bar"]:t["exit_bar"] + 1] = t["dir"] * t["exposure"]
        m = np.asarray(df.index < cut)
        pos_list.append(p[m])
        rets.append(df["close"].pct_change().fillna(0.0).to_numpy()[m])
    return pos_list, rets


def main(argv=None) -> int:
    pd.set_option("display.width", 240)
    with open(GATES) as f:
        gates = yaml.safe_load(f)
    grad, mc = gates["graduation"], gates["monte_carlo"]

    u = load_universe("broad")
    cost_class = {s.ticker: s.cost_class for s in u.symbols}
    cache = ParquetCache()
    frames = {s: cache.read(s, TF) for s in u.tickers if cache.exists(s, TF)}
    frames = {s: d for s, d in frames.items() if d is not None and len(d) > 300}
    cut = max(d.index.max() for d in frames.values()) - pd.DateOffset(months=oos_months())
    sigs = V2Signals(frames)

    print("=== CONFLUENCE v2 FUNNEL — daily swing + weekly alignment ===")
    print(f"{len(frames)} symbols | {len(combos())} configs ({N_FREE} free) | "
          f"OOS holdout UNLOCKED for the final gauntlet\n")

    # Stage-1 edge confirmation (forward-return t-stat of v2 entries, h=4 days)
    parts = [collect_entries(df, sigs.get(s, 4, 1, 6), [4]) for s, df in frames.items()]
    pooled = pd.concat(parts, ignore_index=True)["ret_4"].dropna()
    t = pooled.mean() / (pooled.std(ddof=1) / np.sqrt(len(pooled))) if len(pooled) > 5 else float("nan")
    print(f"Stage-1 edge (v2 entries, h=4d): n={len(pooled)}, mean={pooled.mean():.1f}bps, t={t:.2f}")

    rows, ret_by, keys = [], {}, []
    for c in combos():
        r, n_tr = evaluate(sigs, cost_class, c, cut, "research")
        k = tuple(c[x] for x in V2_GRID)
        ret_by[k] = r
        keys.append(k)
        rows.append({**c, "sharpe": sharpe(r, ANN), "n_trades": n_tr})
    grid_df = pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)
    best = grid_df.iloc[0]
    bp = {k: best[k] for k in V2_GRID}
    research_ret = ret_by[tuple(bp[k] for k in V2_GRID)]
    print("\nTOP configs (research Sharpe):")
    print(grid_df[["confluence_window", "atr_stop_mult", "time_stop_bars", "sharpe",
                   "n_trades"]].head(6).to_string(index=False))

    ref = next(iter(frames.values()))
    wfa = run_wfa(ret_by, [dict(zip(V2_GRID, k)) for k in keys], keys,
                  ref.index[ref.index < cut], ANN, n_folds=4)
    print(f"\nWFA efficiency (OOS/IS) = {wfa.efficiency:.2f} | mean test Sharpe "
          f"{wfa.mean_test_sharpe:.2f} | folds={['%.2f'%s for s in wfa.test_sharpes]}")

    # gauntlet
    sr_var = float(np.nanvar((grid_df["sharpe"] / np.sqrt(ANN)).to_numpy()))
    dsr = deflated_sharpe_ratio(research_ret, sr_var, len(grid_df))
    pbo = pbo_cscv(np.column_stack([ret_by[k].to_numpy() for k in keys]), s_blocks=10)
    boot = block_bootstrap_sharpe(research_ret, ANN, n_iter=mc["bootstrap_iterations"],
                                  seed=mc["random_seed"])
    pos, rets = positions(sigs, cost_class, bp, cut)
    perm = permutation_test(pos, rets, ANN, n_iter=mc["permutation_iterations"],
                            seed=mc["random_seed"])
    by_year = {int(y): sharpe(r, ANN) for y, r in research_ret.groupby(research_ret.index.year)}
    sub_ok = np.mean([v > 0 for v in by_year.values()]) >= 0.75 and min(by_year.values()) > -0.5
    neigh = [grid_df.iloc[i]["sharpe"] for i in range(len(grid_df))
             if sum(1 for k in V2_GRID if len(V2_GRID[k]) > 1 and grid_df.iloc[i][k] != best[k]) == 1]
    neigh_ok = bool(neigh and min(neigh) > 0 and min(neigh) > 0.5 * best["sharpe"])
    oos_ret, _ = evaluate(sigs, cost_class, bp, cut, "holdout")
    oos_sharpe = sharpe(oos_ret, ANN)

    crit = {
        "OOS Sharpe (holdout)": (round(oos_sharpe, 2), f">= {grad['oos_sharpe_min']}", oos_sharpe >= grad["oos_sharpe_min"]),
        "PBO (CSCV)": (round(pbo["pbo"], 3), f"< {grad['pbo_max']}", pbo["pbo"] == pbo["pbo"] and pbo["pbo"] < grad["pbo_max"]),
        "Deflated Sharpe": (round(dsr["dsr"], 3), "> 0.95", dsr["significant"]),
        "Bootstrap p5": (round(boot["p5"], 2), f">= {grad['bootstrap_sharpe_5th_pct_min']}", boot["p5"] >= grad["bootstrap_sharpe_5th_pct_min"]),
        "Bootstrap median": (round(boot["median"], 2), f">= {grad['bootstrap_sharpe_median_min']}", boot["median"] >= grad["bootstrap_sharpe_median_min"]),
        "Permutation pctile": (round(perm["percentile"], 1), f">= {grad['permutation_percentile_min']}", perm["percentile"] >= grad["permutation_percentile_min"]),
        "Subperiod stability": ({y: round(v, 2) for y, v in by_year.items()}, "stable", bool(sub_ok)),
        "Neighborhood robust": (f"min {round(min(neigh), 2) if neigh else 'na'}", "robust", neigh_ok),
        "Trade budget": (int(best["n_trades"]), f">= {MIN_TRADES}", best["n_trades"] >= MIN_TRADES),
    }
    print("\nGraduated config:", bp, f"\nresearch Sharpe {sharpe(research_ret, ANN):.2f}\n")
    print("--- v2 GRADUATION SCORECARD ---")
    for name, (val, thr, ok) in crit.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {val}  (need {thr})")
    passed = all(ok for _, _, ok in crit.values())
    print("\n" + "=" * 60)
    print(f"  v2 GRADUATION: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
