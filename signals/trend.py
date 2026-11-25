"""Trend-following signal — a NEW signal class for the daily/options goal.

The squeeze/RAF family was shown to have edge only at 2h (too small for options).
This is a different hypothesis entirely: time-series trend-following (the most
robust documented equity anomaly), which captures sustained multi-week moves —
exactly what a directional debit spread with weeks of DTE needs.

Trend v0 (daily): an N-day Donchian breakout in the direction of the long-term
trend filter (price vs SMA(trend_ma)).
- Long:  new N-day high AND close > SMA(trend_ma)
- Short: new N-day low  AND close < SMA(trend_ma)
Entries are the breakout EVENT (rising edge). No look-ahead: highs/lows are
shifted one bar; the trend filter uses only past closes.
"""

from __future__ import annotations

import pandas as pd

from indicators import ta

from .confluence import Signals


def _rising_edge(cond: pd.Series) -> pd.Series:
    return (cond & ~cond.shift(1, fill_value=False)).fillna(False)


def generate_trend(df: pd.DataFrame, breakout: int = 20, trend_ma: int = 200,
                   conv_scale: float = 0.15) -> Signals:
    """Donchian-breakout trend follower. `breakout` = channel lookback (bars),
    `trend_ma` = long-term trend filter length. sq/rf are None (no squeeze/RAF)."""
    close = df["close"]
    sma_t = ta.sma(close, trend_ma)
    hh = ta.highest(df["high"], breakout).shift(1)   # prior N-bar high (no look-ahead)
    ll = ta.lowest(df["low"], breakout).shift(1)

    long_cond = (close > sma_t) & (close > hh)
    short_cond = (close < sma_t) & (close < ll)
    long_entry = _rising_edge(long_cond)
    short_entry = _rising_edge(short_cond)
    both = long_entry & short_entry
    long_entry &= ~both
    short_entry &= ~both

    # conviction = how far price sits beyond the trend MA (trend strength), 0..1
    conv_long = ((close / sma_t - 1.0) / conv_scale).clip(0.0, 1.0)
    conv_short = ((sma_t / close - 1.0) / conv_scale).clip(0.0, 1.0)
    direction = pd.Series(0, index=df.index, dtype="int64").mask(long_entry, 1).mask(short_entry, -1)
    conviction = pd.Series(0.0, index=df.index).mask(long_entry, conv_long).mask(short_entry, conv_short)
    conviction = conviction.fillna(0.0)
    return Signals(long_entry=long_entry, short_entry=short_entry, conviction=conviction,
                   direction=direction, sq=None, rf=None)
