"""Cross-sectional residual-momentum scoring (beta-neutral, the upgraded book).

The score is each name's 12-1 momentum with the MARKET-driven part removed:
    score_i = (raw 12-1 return)_i  -  beta_i * (market's 12-1 return)
where beta_i is the OLS slope of the name's daily returns on the market's over the
formation window. This strips the market component so the rank reflects *idiosyncratic*
momentum -> a genuinely market-agnostic signal. Sector-neutralization (demean within
SIC group) and inverse-vol leg weighting are applied in the backtest.
"""

from __future__ import annotations

import pandas as pd


def betas(RET: pd.DataFrame, mret: pd.Series, lo: int, hi: int) -> pd.Series:
    """OLS beta of each column's returns on the market over rows [lo:hi]."""
    sub = RET.iloc[lo:hi]
    m = mret.iloc[lo:hi]
    mcen = m - m.mean()
    var = float((mcen**2).mean())
    if var <= 0:
        return pd.Series(0.0, index=RET.columns)
    cov = sub.sub(sub.mean()).mul(mcen, axis=0).mean()
    return cov / var


def resid_scores(C: pd.DataFrame, RET: pd.DataFrame, mret: pd.Series,
                 mclose: pd.Series, i: int, lookback: int, skip: int) -> pd.Series:
    """Residual (beta-neutral) momentum score per name at row i."""
    b = betas(RET, mret, i - lookback, i - skip)
    stock_ret = C.iloc[i - skip] / C.iloc[i - lookback] - 1.0
    mkt_ret = float(mclose.iloc[i - skip] / mclose.iloc[i - lookback] - 1.0)
    return stock_ret - b * mkt_ret
