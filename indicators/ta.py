"""Technical-analysis primitives shared by the squeeze + RAF ports.

These follow ThinkOrSwim conventions as closely as can be pinned without the
export fixtures (Stage 0). Where ToS leaves a convention ambiguous, the choice is
documented inline and exposed as a parameter so the fidelity harness can lock it:

- EMA  -> recursive, seeded at the first bar (ToS `ExpAverage` / `compoundValue`),
  i.e. pandas ewm(adjust=False). Matches ToS within tolerance after ~`length` bars.
- SMA  -> simple rolling mean.
- StDev-> POPULATION (ddof=0), matching ToS `StDev` used by BollingerBands.
- ATR  -> Wilder's RMA smoothing by default (ToS `AvgTrueRange` averageType=WILDERS);
  the squeeze KC width uses EMA-of-TrueRange instead (T2), passed explicitly.
- TEMA -> 3*e1 - 3*e2 + e3 (triple EMA), EMA seeding as above.
- linreg_endpoint -> value of the least-squares line at the last point of each
  rolling window (ToS `Inertia` / LinearRegressionCurve), for the TTM histogram (T3).

No look-ahead anywhere: every function uses only current-and-past bars (rolling /
recursive). Displacement (the RAF backward lag) lives in raf.py, not here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, length: int) -> pd.Series:
    """ToS ExpAverage: recursive EMA seeded at the first value (adjust=False)."""
    return s.ewm(span=length, adjust=False).mean()


def sma(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length).mean()


def stdev(s: pd.Series, length: int, ddof: int = 0) -> pd.Series:
    """Population stdev by default (ddof=0) to match ToS BollingerBands."""
    return s.rolling(length).std(ddof=ddof)


def true_range(df: pd.DataFrame) -> pd.Series:
    """max(H-L, |H-prevC|, |L-prevC|). First bar = H-L (no prior close)."""
    h, lo, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat(
        [(h - lo), (h - prev_c).abs(), (lo - prev_c).abs()], axis=1
    ).max(axis=1)
    tr.iloc[0] = (h.iloc[0] - lo.iloc[0])
    return tr


def rma(s: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing (RMA): alpha = 1/length, seeded at the first value.
    This is ToS WildersAverage, used by AvgTrueRange/ADX by default."""
    return s.ewm(alpha=1.0 / length, adjust=False).mean()


def atr(df: pd.DataFrame, length: int, method: str = "wilders") -> pd.Series:
    tr = true_range(df)
    if method == "wilders":
        return rma(tr, length)
    if method == "ema":
        return ema(tr, length)
    if method == "sma":
        return sma(tr, length)
    raise ValueError(f"unknown atr method {method!r}")


def tema(s: pd.Series, length: int) -> pd.Series:
    """Triple EMA: 3*e1 - 3*e2 + e3."""
    e1 = ema(s, length)
    e2 = ema(e1, length)
    e3 = ema(e2, length)
    return 3 * e1 - 3 * e2 + e3


def highest(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length).max()


def lowest(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length).min()


def linreg_endpoint(s: pd.Series, length: int) -> pd.Series:
    """Least-squares line value at the END of each rolling window (ToS Inertia /
    LinearRegressionCurve last point). Vectorized via rolling sums.

    For x = 0..L-1, slope = cov(x,y)/var(x), endpoint = mean(y) + slope*(L-1-mean(x)).
    mean(x) = (L-1)/2, var(x) = (L^2-1)/12 (population).
    """
    L = length
    x_mean = (L - 1) / 2.0
    x_var = (L * L - 1) / 12.0
    idx = np.arange(L, dtype=float)

    y_mean = s.rolling(L).mean()
    # cov(x,y) = mean(x*y) - mean(x)*mean(y); compute mean(x*y) via weighted rolling sum
    xy_mean = s.rolling(L).apply(lambda w: float(np.dot(idx, w)) / L, raw=True)
    slope = (xy_mean - x_mean * y_mean) / x_var
    return y_mean + slope * (L - 1 - x_mean)


def stochastic(
    df: pd.DataFrame, k_period: int, smooth: int, average: str = "exponential"
) -> pd.Series:
    """%K stochastic over `k_period`, smoothed by `smooth` (EXPONENTIAL per RAF /
    ToS StochasticFull averageType=EXPONENTIAL). Returns a 0..100 series.

    raw %K = 100 * (close - lowest(low,k)) / (highest(high,k) - lowest(low,k)).
    """
    hh = highest(df["high"], k_period)
    ll = lowest(df["low"], k_period)
    rng = (hh - ll).replace(0.0, np.nan)
    raw_k = 100.0 * (df["close"] - ll) / rng
    raw_k = raw_k.fillna(50.0)  # flat range -> neutral
    if average == "exponential":
        return ema(raw_k, smooth)
    if average == "simple":
        return sma(raw_k, smooth)
    raise ValueError(f"unknown stochastic average {average!r}")


def stoch_normalized(
    df: pd.DataFrame, k_period: int, smooth: int, average: str = "exponential"
) -> pd.Series:
    """Stochastic mapped to [-1, 1]: (s - 50) / 50, where s is the smoothed %K."""
    return (stochastic(df, k_period, smooth, average) - 50.0) / 50.0


def rsi(close: pd.Series, length: int = 2) -> pd.Series:
    """Wilder's RSI. Short lengths (e.g. 2) drive the pullback/mean-reversion signal."""
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1.0 / length, adjust=False).mean()
    roll_down = down.ewm(alpha=1.0 / length, adjust=False).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Wilder's ADX (ToS DMI/ADX convention: WILDERS smoothing)."""
    h, lo = df["high"], df["low"]
    up = h.diff()
    down = -lo.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    tr = true_range(df)
    atr_w = rma(tr, length)
    plus_di = 100 * rma(plus_dm, length) / atr_w
    minus_di = 100 * rma(minus_dm, length) / atr_w
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return rma(dx.fillna(0.0), length)
