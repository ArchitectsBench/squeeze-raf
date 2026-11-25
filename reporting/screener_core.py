"""Shared squeeze-screener core — single source of truth for the CLI, the ntfy
alerts, and the web API. Direction is TREND-based (1a); setups are tiered ALERT vs
WATCH (2c); the "early" filter keeps only just-starting coils.
"""

from __future__ import annotations

import pandas as pd

from indicators.squeeze import fire, momentum_hist, squeeze_state
from indicators.ta import sma

# EW-grade thresholds (tunable)
MIN_COIL = 8          # >= bars compressed
MOM_PCT_MAX = 2.0     # |momentum| as % of price => move just STARTING (early)
TREND_LEN = 20        # MA for trend-direction
TREND_SLOPE = 4       # bars over which the MA slope is measured
DIR_LABEL = {1: "UP", -1: "DOWN", 0: "—"}


def trailing_squeeze_len(state: pd.Series) -> int:
    n = 0
    for v in reversed(state.to_numpy()):
        if v >= 1:
            n += 1
        else:
            break
    return n


def trend_dir(df: pd.DataFrame) -> int:
    """1a: +1 if price above a RISING TREND_LEN-MA, -1 if below a FALLING one, else 0."""
    ma = sma(df["close"], TREND_LEN)
    if ma.isna().iloc[-1] or ma.isna().iloc[-1 - TREND_SLOPE]:
        return 0
    px = float(df["close"].iloc[-1])
    ma_now, ma_then = float(ma.iloc[-1]), float(ma.iloc[-1 - TREND_SLOPE])
    if px > ma_now and ma_now > ma_then:
        return 1
    if px < ma_now and ma_now < ma_then:
        return -1
    return 0


def tier(st: pd.Series, mom: pd.Series, price: float, trend: int) -> str | None:
    """2c: ALERT (trend+momentum confirm), WATCH (trend-aligned coil, momentum pending),
    or None (not an EW-grade coil)."""
    if trend == 0:
        return None
    if 3 not in st.iloc[-2:].to_numpy():
        return None
    if int(st.iloc[-1]) == 0:
        return None
    if trailing_squeeze_len(st) < MIN_COIL:
        return None
    m = mom.iloc[-3:].to_numpy()
    if len(m) < 3:
        return None
    if not (price > 0 and abs(m[-1]) / price * 100.0 <= MOM_PCT_MAX):
        return None
    mom_dir = 1 if m[-1] > 0 else -1
    building = abs(m[-1]) > abs(m[-2]) > abs(m[-3])
    return "ALERT" if (mom_dir == trend and building) else "WATCH"


def classify(df: pd.DataFrame, length: int = 20) -> dict | None:
    """Per-symbol/timeframe snapshot. Returns the cell dict (tier may be None) or None
    if there isn't enough history."""
    if df is None or len(df) < length + 30:
        return None
    st = squeeze_state(df, length=length)
    mom = momentum_hist(df, length=length)
    px = float(df["close"].iloc[-1])
    m, mprev = float(mom.iloc[-1]), float(mom.iloc[-2])
    trend = trend_dir(df)
    return {
        "price": round(px, 2),
        "state": int(st.iloc[-1]),
        "coil": trailing_squeeze_len(st),
        "dir": DIR_LABEL[trend],
        "mom": round(m, 3),
        "mom_building": bool(abs(m) > abs(mprev)),
        "tier": tier(st, mom, px, trend),
        "fired_recent": bool(fire(st).iloc[-3:].any()),
    }
