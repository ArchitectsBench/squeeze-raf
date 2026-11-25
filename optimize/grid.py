"""Coarse parameter grid (BUILD_SPEC.md §5 Stage 3).

A deliberately COARSE grid over Mode A's free parameters — broad coverage to find
plateaus, not a fine search (that invites overfitting). Optuna refines afterward.
Each combo is scored on the research range (Sharpe) with the trade-count recorded
for the ≥30-trades-per-free-parameter budget (gates.yaml).
"""

from __future__ import annotations

import itertools

import pandas as pd

from .common import SignalCache, evaluate, sharpe

# Exit-redesign grid (P5 round 2): the Stage-1 entry edge is real but the original
# exit set (momentum-flip + R-target + tight ATR stop, ~969 trades/yr) gave it back
# under walk-forward. Here we compare exit STYLES — pure time-stop at the edge
# horizon vs. target/momentum variants — with a WIDE (catastrophe-only) ATR stop so
# the stop stops cutting normal noise. Entry params kept minimal (edge is in entries).
GRID = {
    "confluence_window": [4, 6],
    "sc_ready": [1],
    "energy_lookback": [6],
    "atr_stop_mult": [2.5, 4.0],          # wide: tail protection, not noise-cutting
    "profit_target_R": [2.0],             # only used when use_profit_target=True
    "time_stop_bars": [3, 4, 5, 6, 8],    # the key lever now (edge horizon ~4 bars)
    "use_momentum_exit": [False, True],
    "use_profit_target": [False, True],
}
# count parameters genuinely being searched (>1 level) for the trade budget
N_FREE_PARAMS = sum(1 for v in GRID.values() if len(v) > 1)


def combos() -> list[dict]:
    keys = list(GRID)
    return [dict(zip(keys, vals)) for vals in itertools.product(*GRID.values())]


def combo_key(c: dict) -> tuple:
    return tuple(c[k] for k in GRID)


def run_grid(cache: SignalCache, cost_class: dict, research_cut: pd.Timestamp,
             ann: int) -> tuple[pd.DataFrame, dict, list]:
    """Evaluate every grid combo. Returns (results_df, returns_by_combo, keys)."""
    rows, returns_by_combo, keys = [], {}, []
    for c in combos():
        r, n_trades = evaluate(cache, cost_class, c, research_cut)
        k = combo_key(c)
        returns_by_combo[k] = r
        keys.append(k)
        rows.append({**c, "sharpe": sharpe(r, ann), "n_trades": n_trades})
    df = pd.DataFrame(rows).sort_values("sharpe", ascending=False).reset_index(drop=True)
    return df, returns_by_combo, keys
