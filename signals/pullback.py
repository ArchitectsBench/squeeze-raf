"""Pullback-in-trend signal — per-name directional 'buy the dip' (NEW class).

Motivation: breakout/trend entries buy HIGHS (worse than random entry -> lose to the
bull-market beta baseline). A pullback enters at a BETTER-than-random price (the dip),
so it has a genuine shot at timing alpha. Combined with the trend filter it buys
dips in uptrends / shorts rallies in downtrends, then rides the resumption — a move
big enough to express as a naked long call / put (defined risk = premium, uncapped).

Long:  close > SMA(trend_ma) AND RSI(rsi_len) < oversold      (buy the dip in an uptrend)
Short: close < SMA(trend_ma) AND RSI(rsi_len) > 100-oversold  (fade the pop in a downtrend)
Entries are events (RSI crossing into the zone). Look-back only; no peeking (T5).
"""

from __future__ import annotations

import pandas as pd

from indicators import ta

from .confluence import Signals


def _rising_edge(cond: pd.Series) -> pd.Series:
    return (cond & ~cond.shift(1, fill_value=False)).fillna(False)


def generate_pullback(df: pd.DataFrame, trend_ma: int = 100, rsi_len: int = 2,
                      oversold: float = 10.0, trend_mom: int = 0) -> Signals:
    """`trend_mom` (bars, 0=off): trend-strength filter requiring MOMENTUM to agree
    with the MA filter — long needs ret(trend_mom) > 0, short needs < 0. Screens out
    'below the SMA but actually flat/up' marginal setups (e.g. the weak MA short)."""
    close = df["close"]
    sma_t = ta.sma(close, trend_ma)
    r = ta.rsi(close, rsi_len)

    long_cond = (close > sma_t) & (r < oversold)
    short_cond = (close < sma_t) & (r > 100.0 - oversold)
    if trend_mom > 0:
        mom = close.pct_change(trend_mom)
        long_cond &= mom > 0
        short_cond &= mom < 0
    long_entry = _rising_edge(long_cond)
    short_entry = _rising_edge(short_cond)
    both = long_entry & short_entry
    long_entry &= ~both
    short_entry &= ~both

    # conviction: depth of the dip (how oversold) scaled to 0..1
    conv_long = ((oversold - r) / oversold).clip(0.0, 1.0)
    conv_short = ((r - (100.0 - oversold)) / oversold).clip(0.0, 1.0)
    direction = pd.Series(0, index=df.index, dtype="int64").mask(long_entry, 1).mask(short_entry, -1)
    conviction = pd.Series(0.0, index=df.index).mask(long_entry, conv_long).mask(short_entry, conv_short)
    return Signals(long_entry=long_entry, short_entry=short_entry,
                   conviction=conviction.fillna(0.0), direction=direction, sq=None, rf=None)
