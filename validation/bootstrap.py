"""Block bootstrap Monte Carlo (BUILD_SPEC.md §5 Stage 4c).

Resamples the strategy's per-bar returns in BLOCKS (preserving autocorrelation,
per gates.yaml monte_carlo.block_bootstrap) to build a distribution of the Sharpe
ratio. Gate: 5th percentile of annualized Sharpe >= 0.5 and median >= 1.2.
Deterministic via seed.
"""

from __future__ import annotations

import numpy as np


def block_bootstrap_sharpe(
    returns, ann: int, n_iter: int = 5000, block: int | None = None, seed: int = 42
) -> dict:
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < 30 or r.std(ddof=1) == 0:
        return {"median": float("nan"), "p5": float("nan"), "p95": float("nan"),
                "n_iter": 0}
    if block is None:
        block = max(5, int(round(n ** (1 / 3))))   # ~ rule-of-thumb block length
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    sqrt_ann = np.sqrt(ann)
    sharpes = np.empty(n_iter)
    for i in range(n_iter):
        starts = rng.integers(0, n, size=n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % n
        sample = r[idx[:n]]
        sd = sample.std(ddof=1)
        sharpes[i] = (sample.mean() / sd * sqrt_ann) if sd > 0 else 0.0
    return {"median": float(np.median(sharpes)),
            "p5": float(np.percentile(sharpes, 5)),
            "p95": float(np.percentile(sharpes, 95)),
            "n_iter": n_iter}
