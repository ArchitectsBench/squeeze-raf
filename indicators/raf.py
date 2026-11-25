"""RAF port — Ready-Aim-Fire / Relative Adaptive Filter (inflection + regime).

Math: BUILD_SPEC.md §2.2. Traps preserved exactly (indicators/CLAUDE.md):
- T1: the displaced TEMA is a **BACKWARD** lag — `savg = tavg.shift(14)` (value
  14 bars ago). NEVER `shift(-14)`; that injects look-ahead. `DISPLACEMENT_SIGN`
  is +1 (backward) and a unit test fails if it is ever made negative.
- T4: stochastics use **EXPONENTIAL** averaging; TEMA and ADX follow ToS
  (Wilder) conventions (see indicators/ta.py).

Conventions that the ToS fixtures (Stage 0) will lock — documented as parameters:
- stochastic normalization to [-1,1] = (smoothed %K - 50)/50.
- SC (setup count) staging on the sign of k2v with k1/k2 crossovers.
- Fisher "Major" line = recursive Fisher transform of k3v, OB/OS threshold 1.2.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .ta import adx as adx_wilder
from .ta import atr, stoch_normalized, tema

# Displacement is BACKWARD (T1). This constant exists so the look-ahead test can
# assert it is positive; flipping it negative is the canonical look-ahead bug.
DISPLACEMENT_SIGN = +1
PRO_LENGTH = 28
DISPLACEMENT = 14
OBOS = 1.2  # overbought/oversold threshold on the Fisher "Major" line

# (k_period, smoothing) for the three EMA stochastics
K1 = (5, 3)   # fast
K2 = (8, 5)   # medium
K3 = (17, 5)  # slow


def stochastics(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """k1v (fast), k2v (medium), k3v (slow), each normalized to [-1, 1]."""
    k1v = stoch_normalized(df, *K1)
    k2v = stoch_normalized(df, *K2)
    k3v = stoch_normalized(df, *K3)
    return k1v, k2v, k3v


def savgstate(df: pd.DataFrame, pro_length: int = PRO_LENGTH,
              displacement: int = DISPLACEMENT) -> pd.Series:
    """Displaced-TEMA trend regime in {+1, 0, -1}.

    tavg = TEMA(close, pro_length)
    savg = tavg.shift(+displacement)      # BACKWARD lag (T1) — value `disp` bars ago
    matr = 0.5*ATR(10)
    +1 if low > savg+matr ; -1 if high < savg-matr ; else 0
    """
    tavg = tema(df["close"], pro_length)
    savg = tavg.shift(DISPLACEMENT_SIGN * displacement)  # +disp = backward
    matr = 0.5 * atr(df, 10)
    state = pd.Series(0, index=df.index, dtype="int64")
    state = state.mask(df["low"] > savg + matr, 1)
    state = state.mask(df["high"] < savg - matr, -1)
    state = state.mask(savg.isna() | matr.isna(), 0)
    return state


def setup_count(k1v: pd.Series, k2v: pd.Series) -> pd.Series:
    """Ready/Aim staging (SC). Stateful per §2.2:

    cross_above = k1 crosses above k2 ; cross_below = k1 crosses below k2
    if k2v>0:  SC = min(0, SC[-1]) + (-1 if cross_below and k2v[-1]>0 else 0)   # SELL
    else:      SC = max(0, SC[-1]) + (+1 if cross_above and k2v[-1]<=0 else 0)  # BUY

    SC=+1 Ready Buy, >+1 Aim Buy; SC=-1 Ready Sell, <-1 Aim Sell.
    """
    k1 = k1v.to_numpy()
    k2 = k2v.to_numpy()
    n = len(k1)
    sc = np.zeros(n, dtype="int64")
    for t in range(1, n):
        cross_above = (k1[t - 1] <= k2[t - 1]) and (k1[t] > k2[t])
        cross_below = (k1[t - 1] >= k2[t - 1]) and (k1[t] < k2[t])
        if k2[t] > 0:
            inc = -1 if (cross_below and k2[t - 1] > 0) else 0
            sc[t] = min(0, sc[t - 1]) + inc
        else:
            inc = +1 if (cross_above and k2[t - 1] <= 0) else 0
            sc[t] = max(0, sc[t - 1]) + inc
    return pd.Series(sc, index=k1v.index)


def fisher_major(k3v: pd.Series) -> pd.Series:
    """Recursive Fisher transform of the slow stochastic ("Major"/Fire line):
    f3 = 0.5*( ln((1+k3v)/(1-k3v)) + f3[-1] ). k3v clipped to avoid infinities."""
    x = k3v.clip(-0.999, 0.999).to_numpy()
    n = len(x)
    f = np.zeros(n, dtype="float64")
    prev = 0.0
    for t in range(n):
        if np.isnan(x[t]):
            f[t] = prev
            continue
        prev = 0.5 * (np.log((1 + x[t]) / (1 - x[t])) + prev)
        f[t] = prev
    return pd.Series(f, index=k3v.index)


def major_signals(major: pd.Series, obos: float = OBOS) -> tuple[pd.Series, pd.Series]:
    """Curvature inflections of the Fisher line, gated by the OB/OS band.

    MajorBuy  = inflection up (delta turns from <=0 to >0) while major <= -obos.
    MajorSell = inflection down (delta turns from >=0 to <0) while major >= +obos.
    """
    d = major.diff()
    turn_up = (d > 0) & (d.shift(1) <= 0)
    turn_down = (d < 0) & (d.shift(1) >= 0)
    major_buy = turn_up & (major <= -obos)
    major_sell = turn_down & (major >= obos)
    return major_buy.fillna(False), major_sell.fillna(False)


@dataclass
class RAFFeatures:
    k1v: pd.Series
    k2v: pd.Series
    k3v: pd.Series
    sc: pd.Series
    major: pd.Series
    major_buy: pd.Series
    major_sell: pd.Series
    savgstate: pd.Series
    adx: pd.Series
    adx_rising: pd.Series
    fire_buy: pd.Series   # full FIRE per §2.2
    fire_sell: pd.Series


def compute(df: pd.DataFrame, pro_length: int = PRO_LENGTH) -> RAFFeatures:
    """All RAF features in one pass."""
    k1v, k2v, k3v = stochastics(df)
    sc = setup_count(k1v, k2v)
    major = fisher_major(k3v)
    major_buy, major_sell = major_signals(major)
    adx = adx_wilder(df, 14)
    # Full FIRE (§2.2): Aim stage AND Fisher inflecting from the OB/OS extreme.
    fire_buy = (sc > 1) & major_buy
    fire_sell = (sc < -1) & major_sell
    return RAFFeatures(
        k1v=k1v, k2v=k2v, k3v=k3v, sc=sc, major=major,
        major_buy=major_buy, major_sell=major_sell,
        savgstate=savgstate(df, pro_length),
        adx=adx, adx_rising=(adx > adx.shift(1)).fillna(False),
        fire_buy=fire_buy, fire_sell=fire_sell,
    )
