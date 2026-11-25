"""Probability of Backtest Overfitting via CSCV (López de Prado; BUILD_SPEC.md §5 Stage 4b).

Combinatorially-Symmetric Cross-Validation: given a matrix of per-bar returns for N
candidate configs over T bars, split T into S equal blocks and, for every way of
choosing S/2 blocks as in-sample (the rest out-of-sample), pick the config that is
best IS and measure its OOS rank. PBO = P(the IS-best config lands below the OOS
median) — i.e. how often "best in backtest" fails to generalize. PBO < 0.20 passes.

Implemented directly (pypbo deferred). Sharpe used as the performance measure.
"""

from __future__ import annotations

import itertools

import numpy as np


def _sharpe_cols(mat: np.ndarray) -> np.ndarray:
    """Per-column (config) Sharpe on the given rows. mat: (T, N)."""
    mean = mat.mean(axis=0)
    sd = mat.std(axis=0, ddof=1)
    sd[sd == 0] = np.nan
    return mean / sd


def pbo_cscv(returns_matrix: np.ndarray, s_blocks: int = 10) -> dict:
    """returns_matrix: (T, N) per-bar returns for N configs. Returns
    {pbo, n_combos, median_logit}."""
    T, N = returns_matrix.shape
    if N < 2 or T < s_blocks * 4:
        return {"pbo": float("nan"), "n_combos": 0, "median_logit": float("nan")}
    if s_blocks % 2 == 1:
        s_blocks += 1
    # split rows into S contiguous blocks
    edges = np.linspace(0, T, s_blocks + 1).astype(int)
    blocks = [np.arange(edges[i], edges[i + 1]) for i in range(s_blocks)]
    all_idx = set(range(s_blocks))

    logits = []
    below_median = 0
    for is_combo in itertools.combinations(range(s_blocks), s_blocks // 2):
        oos_combo = sorted(all_idx - set(is_combo))
        is_rows = np.concatenate([blocks[b] for b in is_combo])
        oos_rows = np.concatenate([blocks[b] for b in oos_combo])

        is_sr = _sharpe_cols(returns_matrix[is_rows])
        if np.all(np.isnan(is_sr)):
            continue
        n_star = int(np.nanargmax(is_sr))                 # best IS config
        oos_sr = _sharpe_cols(returns_matrix[oos_rows])
        # rank of n_star among OOS (1=worst .. N=best); relative rank omega
        order = np.argsort(np.where(np.isnan(oos_sr), -np.inf, oos_sr))
        rank = int(np.where(order == n_star)[0][0]) + 1
        omega = rank / (N + 1)
        omega = min(max(omega, 1e-6), 1 - 1e-6)
        logits.append(np.log(omega / (1 - omega)))
        if omega <= 0.5:
            below_median += 1

    if not logits:
        return {"pbo": float("nan"), "n_combos": 0, "median_logit": float("nan")}
    return {"pbo": float(below_median / len(logits)),
            "n_combos": len(logits),
            "median_logit": float(np.median(logits))}
