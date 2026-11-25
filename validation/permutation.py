"""Monte Carlo permutation test (BUILD_SPEC.md §5 Stage 4d).

Null hypothesis: the strategy's TIMING carries no information. We build the null by
randomly circular-shifting each symbol's position series relative to its returns
(preserving the autocorrelation of both, destroying their alignment) and recomputing
the portfolio Sharpe. Gate: the real Sharpe must beat the 95th percentile of the
null (gates.yaml permutation_percentile_min).

Sharpe here is order-invariant, so a plain return permutation would be useless —
shifting the POSITION vs returns is what actually tests timing skill.
"""

from __future__ import annotations

import numpy as np


def _portfolio_sharpe(positions: list[np.ndarray], rets: list[np.ndarray],
                      ann: int, shifts=None) -> float:
    """Mean-across-symbols per-bar return -> annualized Sharpe. `shifts` optionally
    circular-shifts each symbol's position array."""
    cols = []
    for i, (pos, ret) in enumerate(zip(positions, rets)):
        p = pos if shifts is None else np.roll(pos, shifts[i])
        cols.append(p * ret)
    # align by index position (all per-symbol arrays are full-length, NaN where flat=0)
    L = min(len(c) for c in cols)
    mat = np.vstack([c[:L] for c in cols])
    port = np.nanmean(mat, axis=0)
    sd = port.std(ddof=1)
    return float(port.mean() / sd * np.sqrt(ann)) if sd > 0 else 0.0


def permutation_test(
    positions: list[np.ndarray], rets: list[np.ndarray], ann: int,
    n_iter: int = 1000, seed: int = 42, min_shift: int = 5,
) -> dict:
    """positions/rets: per-symbol aligned arrays (position = signed exposure;
    ret = close-to-close). Returns {real, p95_null, percentile, beats}."""
    if not positions:
        return {"real": float("nan"), "p95_null": float("nan"),
                "percentile": float("nan"), "beats": False}
    real = _portfolio_sharpe(positions, rets, ann)
    rng = np.random.default_rng(seed)
    lengths = [len(p) for p in positions]
    null = np.empty(n_iter)
    for k in range(n_iter):
        shifts = [int(rng.integers(min_shift, max(min_shift + 1, L - min_shift)))
                  for L in lengths]
        null[k] = _portfolio_sharpe(positions, rets, ann, shifts=shifts)
    p95 = float(np.percentile(null, 95))
    pct = float((null < real).mean() * 100)
    return {"real": real, "p95_null": p95, "percentile": pct, "beats": bool(real > p95)}
