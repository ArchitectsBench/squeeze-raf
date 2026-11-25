"""MA-pullback entry — buy a pullback to the rising 20 SMA / 21 EMA in an uptrend.

A location-based entry (not an oscillator): in an established trend, enter when price
RETRACES to its 20 SMA (or 21 EMA), which acts as dynamic support, and HOLDS there.

Long:  trend up (MA rising AND close > trend filter MA) AND the bar's LOW touches the
       MA zone (within `atr_touch`*ATR) AND close holds at/above the MA.
Short: mirror (MA falling, close < trend MA, HIGH touches the MA zone, close <= MA).
Entries are touch-and-hold EVENTS (rising edge). Look-back only (T5).
"""

from __future__ import annotations

import pandas as pd

from indicators import ta

from .confluence import Signals


def _rising_edge(cond: pd.Series) -> pd.Series:
    return (cond & ~cond.shift(1, fill_value=False)).fillna(False)


def generate_ma_pullback(df: pd.DataFrame, ma_len: int = 20, ma_type: str = "sma",
                         trend_len: int = 50, atr_touch: float = 0.25,
                         strong_trend: bool = False, long_len: int = 200,
                         adx_min: float = 20.0) -> Signals:
    close, high, low = df["close"], df["high"], df["low"]
    ma = ta.sma(close, ma_len) if ma_type == "sma" else ta.ema(close, ma_len)
    trend = ta.sma(close, trend_len)
    atr = ta.atr(df, 14)
    band = atr_touch * atr

    up = (close > trend) & (ma > ma.shift(1))            # uptrend, MA rising
    dn = (close < trend) & (ma < ma.shift(1))            # downtrend, MA falling
    if strong_trend:
        # only CLEAN, durable trends: price vs 200MA, golden/death cross, ADX trending
        long_ma = ta.sma(close, long_len)
        adx = ta.adx(df, 14)
        adx_ok = (adx >= adx_min) & (adx > adx.shift(1))
        up &= (close > long_ma) & (trend > long_ma) & adx_ok
        dn &= (close < long_ma) & (trend < long_ma) & adx_ok
    long_touch = up & ((low - ma).abs() <= band) & (close >= ma)
    short_touch = dn & ((high - ma).abs() <= band) & (close <= ma)
    long_entry = _rising_edge(long_touch)
    short_entry = _rising_edge(short_touch)
    both = long_entry & short_entry
    long_entry &= ~both
    short_entry &= ~both

    # conviction = trend strength (how far price sits beyond the trend filter), 0..1
    conv_long = ((close / trend - 1.0) / 0.10).clip(0.0, 1.0)
    conv_short = ((trend / close - 1.0) / 0.10).clip(0.0, 1.0)
    direction = pd.Series(0, index=df.index, dtype="int64").mask(long_entry, 1).mask(short_entry, -1)
    conviction = pd.Series(0.0, index=df.index).mask(long_entry, conv_long).mask(short_entry, conv_short)
    return Signals(long_entry=long_entry, short_entry=short_entry,
                   conviction=conviction.fillna(0.0), direction=direction, sq=None, rf=None)
