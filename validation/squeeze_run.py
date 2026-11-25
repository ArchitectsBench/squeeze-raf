"""Squeeze-ONLY funnel — pure volatility-expansion breakout, no RAF.

    uv run python -m validation.squeeze_run --tf 2h
    uv run python -m validation.squeeze_run --tf 1d

Tests the hypothesis that the squeeze fire alone (decoupled from the mean-reversion
RAF) is a cleaner / larger-move signal. Runs the full gated funnel at the chosen
timeframe: Stage-1 edge stat -> anchored walk-forward -> graduation gauntlet with
the locked OOS unlocked. Small pre-registered grid (honest DSR/PBO).
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from backtest.engine import BARS_PER_YEAR, ExitParams, SizingParams, simulate_symbol
from data.cache import ParquetCache
from data.universe import load_universe
from edge_check.event_study import collect_entries
from optimize.common import oos_months, sharpe
from optimize.walkforward import run_wfa
from signals import ConfluenceParams, generate_squeeze

from .bootstrap import block_bootstrap_sharpe
from .dsr import deflated_sharpe_ratio
from .pbo_cscv import pbo_cscv
from .permutation import permutation_test

GATES = Path(__file__).resolve().parent.parent / "config" / "gates.yaml"
TIME_STOPS = {"2h": [6, 8, 12], "4h": [5, 6, 8], "1d": [5, 8, 10]}


def grid_for(tf):
    return {
        "confluence_window": [2, 4, 6],
        "atr_stop_mult": [2.5, 4.0],
        "time_stop_bars": TIME_STOPS[tf],
    }


class SqueezeSignals:
    def __init__(self, frames, length):
        self.frames, self.length = frames, length
        self._cache = {}

    def get(self, sym, cw):
        key = (sym, cw)
        if key not in self._cache:
            p = ConfluenceParams(length=self.length, confluence_window=cw)
            self._cache[key] = generate_squeeze(self.frames[sym], p)
        return self._cache[key]


def _exits(p):
    return ExitParams(time_stop_bars=int(p["time_stop_bars"]),
                      atr_stop_mult=float(p["atr_stop_mult"]), profit_target_R=2.0,
                      use_momentum_exit=True, use_profit_target=False)


def evaluate(sigs, cost_class, p, cut, window="research"):
    per, n = {}, 0
    for sym, df in sigs.frames.items():
        sig = sigs.get(sym, int(p["confluence_window"]))
        r, trades = simulate_symbol(df, sig, cost_class.get(sym, "equities"),
                                    _exits(p), SizingParams())
        s = pd.Series(r, index=df.index)
        mask = s.index < cut if window == "research" else s.index >= cut
        per[sym] = s[mask]
        n += sum(1 for t in trades
                 if (df.index[t["entry_bar"]] < cut) == (window == "research"))
    return pd.DataFrame(per).sort_index().mean(axis=1).fillna(0.0), n


def positions(sigs, cost_class, p, cut):
    pos_list, rets = [], []
    for sym, df in sigs.frames.items():
        sig = sigs.get(sym, int(p["confluence_window"]))
        _, trades = simulate_symbol(df, sig, cost_class.get(sym, "equities"),
                                    _exits(p), SizingParams())
        arr = np.zeros(len(df))
        for t in trades:
            arr[t["entry_bar"]:t["exit_bar"] + 1] = t["dir"] * t["exposure"]
        m = np.asarray(df.index < cut)
        pos_list.append(arr[m])
        rets.append(df["close"].pct_change().fillna(0.0).to_numpy()[m])
    return pos_list, rets


def main(argv=None) -> int:
    pd.set_option("display.width", 240)
    ap = argparse.ArgumentParser(description="Squeeze-only funnel")
    ap.add_argument("--tf", default="2h", choices=["2h", "4h", "1d"])
    args = ap.parse_args(argv)
    tf = args.tf
    ann = BARS_PER_YEAR[tf]
    length = 21 if tf == "1d" else 13
    GRID = grid_for(tf)
    n_free = sum(1 for v in GRID.values() if len(v) > 1)
    min_trades = 30 * n_free
    with open(GATES) as f:
        gates = yaml.safe_load(f)
    grad, mc = gates["graduation"], gates["monte_carlo"]

    u = load_universe("broad")
    cost_class = {s.ticker: s.cost_class for s in u.symbols}
    cache = ParquetCache()
    frames = {s: cache.read(s, tf) for s in u.tickers if cache.exists(s, tf)}
    frames = {s: d for s, d in frames.items() if d is not None and len(d) > 300}
    cut = max(d.index.max() for d in frames.values()) - pd.DateOffset(months=oos_months())
    sigs = SqueezeSignals(frames, length)
    combos = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]

    print(f"=== SQUEEZE-ONLY FUNNEL — {tf} (no RAF) ===")
    print(f"{len(frames)} symbols | {len(combos)} configs ({n_free} free) | OOS unlocked\n")

    parts = [collect_entries(df, sigs.get(s, 4), [4]) for s, df in frames.items()]
    pooled = pd.concat(parts, ignore_index=True)["ret_4"].dropna()
    t = pooled.mean() / (pooled.std(ddof=1) / np.sqrt(len(pooled))) if len(pooled) > 5 else float("nan")
    print(f"Stage-1 edge (squeeze entries, h=4): n={len(pooled)}, mean={pooled.mean():.1f}bps, t={t:.2f}")

    rows, ret_by, keys = [], {}, []
    for c in combos:
        r, n_tr = evaluate(sigs, cost_class, c, cut, "research")
        k = tuple(c[x] for x in GRID)
        ret_by[k] = r
        keys.append(k)
        rows.append({**c, "sharpe": sharpe(r, ann), "n_trades": n_tr})
    gdf = pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)
    best = gdf.iloc[0]
    bp = {k: best[k] for k in GRID}
    research_ret = ret_by[tuple(bp[k] for k in GRID)]
    print("\nTOP configs (research Sharpe):")
    print(gdf.head(6).to_string(index=False))

    ref = next(iter(frames.values()))
    wfa = run_wfa(ret_by, [dict(zip(GRID, k)) for k in keys], keys,
                  ref.index[ref.index < cut], ann, n_folds=4)
    print(f"\nWFA efficiency (OOS/IS) = {wfa.efficiency:.2f} | mean test Sharpe "
          f"{wfa.mean_test_sharpe:.2f} | folds={['%.2f'%s for s in wfa.test_sharpes]}")

    sr_var = float(np.nanvar((gdf["sharpe"] / np.sqrt(ann)).to_numpy()))
    dsr = deflated_sharpe_ratio(research_ret, sr_var, len(gdf))
    pbo = pbo_cscv(np.column_stack([ret_by[k].to_numpy() for k in keys]), s_blocks=10)
    boot = block_bootstrap_sharpe(research_ret, ann, n_iter=mc["bootstrap_iterations"], seed=mc["random_seed"])
    pos, rets = positions(sigs, cost_class, bp, cut)
    perm = permutation_test(pos, rets, ann, n_iter=mc["permutation_iterations"], seed=mc["random_seed"])
    by_year = {int(y): sharpe(r, ann) for y, r in research_ret.groupby(research_ret.index.year)}
    sub_ok = np.mean([v > 0 for v in by_year.values()]) >= 0.75 and min(by_year.values()) > -0.5
    neigh = [gdf.iloc[i]["sharpe"] for i in range(len(gdf))
             if sum(1 for k in GRID if len(GRID[k]) > 1 and gdf.iloc[i][k] != best[k]) == 1]
    neigh_ok = bool(neigh and min(neigh) > 0 and min(neigh) > 0.5 * best["sharpe"])
    oos_ret, _ = evaluate(sigs, cost_class, bp, cut, "holdout")
    oos_sharpe = sharpe(oos_ret, ann)

    crit = {
        "OOS Sharpe": (round(oos_sharpe, 2), f">= {grad['oos_sharpe_min']}", oos_sharpe >= grad["oos_sharpe_min"]),
        "PBO (CSCV)": (round(pbo["pbo"], 3), f"< {grad['pbo_max']}", pbo["pbo"] == pbo["pbo"] and pbo["pbo"] < grad["pbo_max"]),
        "Deflated Sharpe": (round(dsr["dsr"], 3), "> 0.95", dsr["significant"]),
        "Bootstrap p5": (round(boot["p5"], 2), f">= {grad['bootstrap_sharpe_5th_pct_min']}", boot["p5"] >= grad["bootstrap_sharpe_5th_pct_min"]),
        "Bootstrap median": (round(boot["median"], 2), f">= {grad['bootstrap_sharpe_median_min']}", boot["median"] >= grad["bootstrap_sharpe_median_min"]),
        "Permutation pctile": (round(perm["percentile"], 1), f">= {grad['permutation_percentile_min']}", perm["percentile"] >= grad["permutation_percentile_min"]),
        "Subperiod stability": ({y: round(v, 2) for y, v in by_year.items()}, "stable", bool(sub_ok)),
        "Neighborhood robust": (f"min {round(min(neigh), 2) if neigh else 'na'}", "robust", neigh_ok),
        "Trade budget": (int(best["n_trades"]), f">= {min_trades}", best["n_trades"] >= min_trades),
    }
    print(f"\nGraduated config: {bp}\nresearch Sharpe {sharpe(research_ret, ann):.2f}\n")
    print("--- SQUEEZE-ONLY GRADUATION SCORECARD ---")
    for name, (val, thr, ok) in crit.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {val}  (need {thr})")
    passed = all(ok for _, _, ok in crit.values())
    print("\n" + "=" * 60)
    print(f"  SQUEEZE-ONLY {tf} GRADUATION: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
