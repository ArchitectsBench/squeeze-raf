"""Black-Scholes helpers for the structuring layer (P7).

Used to turn a target delta into a strike, price the spread legs, and compute
probabilities — all from an IV input. In P7 that IV is a REALIZED-VOLATILITY
PROXY (see iv_regime.py); P8 replaces it with real options IV/greeks and validates
actual P&L. European BS is an approximation for American equity options, fine for
the rules layer. Rates default to 0 (short DTE).
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def _d1_d2(S, K, sigma, T, r=0.0):
    sqrtT = np.sqrt(max(T, 1e-9))
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrtT)
    return d1, d1 - sigma * sqrtT


def price(S, K, sigma, T, r=0.0, kind="call") -> float:
    d1, d2 = _d1_d2(S, K, sigma, T, r)
    disc = np.exp(-r * T)
    if kind == "call":
        return float(S * norm.cdf(d1) - K * disc * norm.cdf(d2))
    return float(K * disc * norm.cdf(-d2) - S * norm.cdf(-d1))


def delta(S, K, sigma, T, r=0.0, kind="call") -> float:
    d1, _ = _d1_d2(S, K, sigma, T, r)
    return float(norm.cdf(d1)) if kind == "call" else float(norm.cdf(d1) - 1.0)


def strike_from_delta(S, target_delta, sigma, T, r=0.0, kind="call") -> float:
    """Strike whose option has |delta| = target_delta (0<target_delta<1).

    call: delta=N(d1)=target -> d1=N^{-1}(target).
    put:  delta=N(d1)-1=-target -> N(d1)=1-target -> d1=N^{-1}(1-target).
    Invert d1's definition for K.
    """
    sqrtT = np.sqrt(max(T, 1e-9))
    d1 = norm.ppf(target_delta) if kind == "call" else norm.ppf(1.0 - target_delta)
    # d1 = (ln(S/K) + (r+0.5 sig^2)T)/(sig sqrtT)  ->  K = S*exp((r+0.5 sig^2)T - d1*sig*sqrtT)
    return float(S * np.exp((r + 0.5 * sigma**2) * T - d1 * sigma * sqrtT))


def prob_above(S, level, sigma, T, r=0.0) -> float:
    """Risk-neutral P(S_T > level) under lognormal BS = N(d2) with K=level."""
    _, d2 = _d1_d2(S, level, sigma, T, r)
    return float(norm.cdf(d2))


def prob_below(S, level, sigma, T, r=0.0) -> float:
    return 1.0 - prob_above(S, level, sigma, T, r)
