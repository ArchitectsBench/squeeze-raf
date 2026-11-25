"""Optuna (Bayesian) refinement over the coarse-grid neighborhood (BUILD_SPEC.md §5 Stage 3).

Refines Mode A's parameters with a TPE sampler around the grid ranges. Objective =
research-range Sharpe. Deterministic seed (gates.yaml monte_carlo.random_seed) so
runs reproduce. Optimizes on the research range only; the OOS window stays locked.
"""

from __future__ import annotations

import pandas as pd

from .common import SignalCache, evaluate, sharpe


def refine(
    cache: SignalCache,
    cost_class: dict,
    research_cut: pd.Timestamp,
    ann: int,
    n_trials: int = 40,
    seed: int = 42,
) -> tuple[pd.DataFrame, dict, list]:
    """Run Optuna; return (results_df, returns_by_combo, keys) in the same shape as
    the grid so they can be concatenated."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    returns_by_combo, keys, rows = {}, [], []

    def objective(trial):
        params = {
            "confluence_window": trial.suggest_int("confluence_window", 3, 8),
            "sc_ready": trial.suggest_int("sc_ready", 1, 2),
            "energy_lookback": trial.suggest_int("energy_lookback", 4, 10),
            "atr_stop_mult": trial.suggest_float("atr_stop_mult", 2.0, 5.0, step=0.5),
            "profit_target_R": trial.suggest_float("profit_target_R", 1.5, 3.0, step=0.5),
            "time_stop_bars": trial.suggest_int("time_stop_bars", 3, 10),
            "use_momentum_exit": trial.suggest_categorical("use_momentum_exit", [False, True]),
            "use_profit_target": trial.suggest_categorical("use_profit_target", [False, True]),
        }
        r, n_trades = evaluate(cache, cost_class, params, research_cut)
        s = sharpe(r, ann)
        k = tuple(params.values())
        returns_by_combo[k] = r
        keys.append(k)
        rows.append({**params, "sharpe": s, "n_trades": n_trades})
        return s

    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    df = pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)
    return df, returns_by_combo, keys
