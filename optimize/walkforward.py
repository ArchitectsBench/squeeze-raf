"""Walk-forward analysis harness (anchored; BUILD_SPEC.md §5 Stage 3; gates.yaml).

Anchored = the training window always starts at the beginning of the research
range and expands; each test fold is the next contiguous slice. We re-pick the
best config on each fold's TRAIN slice and score it on the (unseen) TEST slice.
WFA efficiency = mean(test Sharpe) / mean(train Sharpe): ~1.0 means the edge
generalizes; << 1 means it was overfit to the in-sample window.

Operates on the research range only (the final OOS window is locked out upstream).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .common import sharpe


def anchored_folds(index: pd.DatetimeIndex, n_folds: int = 4) -> list[tuple]:
    """Return [(train_start, train_end, test_end), ...]. Train is anchored at the
    research start and expands; test is the following segment."""
    idx = index[index.notnull()].sort_values()
    if len(idx) < (n_folds + 1) * 20:
        n_folds = max(2, len(idx) // 40)
    bounds = [idx[min(int(round(i * len(idx) / (n_folds + 1))), len(idx) - 1)]
              for i in range(n_folds + 2)]
    bounds[-1] = idx[-1]
    start = idx[0]
    folds = []
    for i in range(1, n_folds + 1):
        folds.append((start, bounds[i], bounds[i + 1]))
    return folds


@dataclass
class WFAResult:
    n_folds: int
    train_sharpes: list[float]
    test_sharpes: list[float]
    chosen: list[dict]

    @property
    def efficiency(self) -> float:
        tr = np.mean([s for s in self.train_sharpes if np.isfinite(s)]) if self.train_sharpes else np.nan
        te = np.mean([s for s in self.test_sharpes if np.isfinite(s)]) if self.test_sharpes else np.nan
        if not tr or not np.isfinite(tr) or tr == 0:
            return float("nan")
        return float(te / tr)

    @property
    def mean_test_sharpe(self) -> float:
        vals = [s for s in self.test_sharpes if np.isfinite(s)]
        return float(np.mean(vals)) if vals else float("nan")


def run_wfa(
    returns_by_combo: dict,     # combo_key -> research-range return Series
    combos: list[dict],         # param dicts aligned to keys
    keys: list,                 # combo_key per combo
    research_index: pd.DatetimeIndex,
    ann: int,
    n_folds: int = 4,
) -> WFAResult:
    """Anchored WFA reusing precomputed full-research return series per combo
    (slicing only — no re-backtest). For each fold: pick best combo by TRAIN
    Sharpe, score on TEST."""
    folds = anchored_folds(research_index, n_folds)
    train_sh, test_sh, chosen = [], [], []
    for (t0, t1, t2) in folds:
        best_key, best_train = None, -np.inf
        for k in keys:
            r = returns_by_combo[k]
            tr = sharpe(r[(r.index >= t0) & (r.index < t1)], ann)
            if tr > best_train:
                best_train, best_key = tr, k
        r = returns_by_combo[best_key]
        te = sharpe(r[(r.index >= t1) & (r.index < t2)], ann)
        train_sh.append(best_train)
        test_sh.append(te)
        chosen.append(combos[keys.index(best_key)])
    return WFAResult(len(folds), train_sh, test_sh, chosen)
