"""Market-beta utilities for market-agnostic (beta-neutral) selection & hedging.

The day-to-day P&L noise we want to remove is market BETA. These helpers let us:
- estimate each name's beta to the market (SPY),
- build a beta-neutral hedge (offset net beta with the index),
- and trade the RESIDUAL (idiosyncratic) return — strip beta*market out of the
  stock's return so signals fire on stock-specific moves, not market moves.
"""

from __future__ import annotations

import pandas as pd

MARKET = "SPY"


def beta_latest(stock_close: pd.Series, mkt_close: pd.Series, lookback: int = 120) -> float:
    """OLS beta of daily returns over the last `lookback` aligned bars (cov/var)."""
    rs = stock_close.pct_change()
    rm = mkt_close.pct_change()
    d = pd.concat([rs.rename("s"), rm.rename("m")], axis=1).dropna().iloc[-lookback:]
    if len(d) < 30 or d["m"].var() == 0:
        return 1.0
    return float(d["s"].cov(d["m"]) / d["m"].var())


def residual_close(stock_close: pd.Series, mkt_close: pd.Series, lookback: int = 120,
                   base: float = 100.0) -> pd.Series:
    """A synthetic 'beta-neutral price': compound the idiosyncratic daily return
    (stock_ret - beta*market_ret). Signals run on this fire on stock-specific moves."""
    b = beta_latest(stock_close, mkt_close, lookback)
    rs = stock_close.pct_change()
    rm = mkt_close.pct_change()
    resid = (rs - b * rm).fillna(0.0)
    return base * (1.0 + resid).cumprod()


def residual_frame(df: pd.DataFrame, mkt_close: pd.Series, lookback: int = 120) -> pd.DataFrame:
    """OHLCV-shaped frame whose close is the residual (beta-neutral) price, for
    running close-based signals (pullback) on the idiosyncratic series."""
    rc = residual_close(df["close"], mkt_close.reindex(df.index).ffill(), lookback)
    out = pd.DataFrame({"open": rc, "high": rc, "low": rc, "close": rc,
                        "volume": df["volume"]}, index=df.index)
    out.index.name = df.index.name
    return out
