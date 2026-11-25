"""Deflated Sharpe Ratio (Bailey & López de Prado; BUILD_SPEC.md §5 Stage 4a).

The DSR corrects an observed Sharpe for (a) the number of trials that were run to
find it, (b) non-normal returns (skew/kurtosis), and (c) sample length. It is the
probability that the TRUE Sharpe exceeds a benchmark set by the expected maximum
Sharpe under N independent trials. DSR > 0.95 => significant after deflation.

We implement directly (pypbo is deferred — see pyproject). All Sharpes here are
per-period (NOT annualized); deflation is scale-free in the period unit.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm

EULER_MASCHERONI = 0.5772156649015329


def probabilistic_sharpe_ratio(sr: float, n: int, skew: float, kurt: float,
                               sr_benchmark: float = 0.0) -> float:
    """PSR: P(true SR > sr_benchmark) given the estimate `sr` over n obs.
    kurt is the (non-excess) kurtosis (normal = 3)."""
    if n < 3:
        return float("nan")
    denom = np.sqrt(1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2)
    if denom <= 0 or not np.isfinite(denom):
        return float("nan")
    z = (sr - sr_benchmark) * np.sqrt(n - 1) / denom
    return float(norm.cdf(z))


def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """Expected maximum of N i.i.d. Sharpe estimates ~ the deflation benchmark."""
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    sigma = np.sqrt(sr_variance)
    g = EULER_MASCHERONI
    a = norm.ppf(1.0 - 1.0 / n_trials)
    b = norm.ppf(1.0 - 1.0 / (n_trials * np.e))
    return float(sigma * ((1.0 - g) * a + g * b))


def deflated_sharpe_ratio(returns, sr_variance_across_trials: float, n_trials: int) -> dict:
    """Returns {dsr, sr, benchmark, significant} for a per-period return series."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < 10 or r.std(ddof=1) == 0:
        return {"dsr": float("nan"), "sr": 0.0, "benchmark": 0.0, "significant": False}
    sr = r.mean() / r.std(ddof=1)
    from scipy.stats import kurtosis, skew
    sk = float(skew(r))
    ku = float(kurtosis(r, fisher=False))  # non-excess
    bench = expected_max_sharpe(sr_variance_across_trials, n_trials)
    dsr = probabilistic_sharpe_ratio(sr, n, sk, ku, sr_benchmark=bench)
    return {"dsr": dsr, "sr": float(sr), "benchmark": float(bench),
            "significant": bool(dsr is not None and dsr == dsr and dsr > 0.95)}
