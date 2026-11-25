"""squeezeultra port — TTM Squeeze Pro variant (compression + trend + momentum).

Math: BUILD_SPEC.md §2.1. Traps preserved exactly (indicators/CLAUDE.md):
- T2: Bollinger basis is an **EMA**; Keltner basis is an **SMA** with **EMA-of-
  TrueRange** width. The basis asymmetry is intentional — not tidied.
- T3: momentum histogram is the linear-regression (Inertia) value of
  `close - 0.5*(donchian_mid(L) + SMA(close,L))` over L.

State encoding is fixed (do not flip): 3 = tightest compression (BB inside the
1.0-ATR Keltner), 2 = mid, 1 = loose, 0 = no squeeze. A "fire" = state goes from
>=1 to 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .ta import ema, linreg_endpoint, sma, stdev, true_range

# ToS constants (config/universe.yaml strategy_defaults.squeeze)
BB_MULT = 2.0
KC_HIGH, KC_MID, KC_LOW = 1.0, 1.5, 2.0  # Keltner ATR multipliers (tight->loose)


def squeeze_state(
    df: pd.DataFrame,
    length: int = 13,
    bb_mult: float = BB_MULT,
    kc_mults: tuple[float, float, float] = (KC_HIGH, KC_MID, KC_LOW),
) -> pd.Series:
    """Compression state in {3,2,1,0}.

    BB_upper = EMA(close,L) + bb_mult*stdev(close,L)        # EMA basis (T2)
    shift    = EMA(TrueRange,L)                             # EMA-of-TR width
    KC_upper(m) = SMA(close,L) + shift*m                    # SMA basis (T2)
    3 if BB_upper<=KC(1.0); 2 if <=KC(1.5); 1 if <=KC(2.0); else 0
    """
    close = df["close"]
    bb_upper = ema(close, length) + bb_mult * stdev(close, length)
    shift = ema(true_range(df), length)
    kc_basis = sma(close, length)
    m_high, m_mid, m_low = kc_mults

    kc_high = kc_basis + shift * m_high
    kc_mid = kc_basis + shift * m_mid
    kc_low = kc_basis + shift * m_low

    state = pd.Series(0, index=df.index, dtype="int64")
    state = state.mask(bb_upper <= kc_low, 1)
    state = state.mask(bb_upper <= kc_mid, 2)
    state = state.mask(bb_upper <= kc_high, 3)
    # bars without enough history (NaN bands) -> 0/no-squeeze
    invalid = bb_upper.isna() | kc_low.isna()
    state = state.mask(invalid, 0)
    return state


def fire(state: pd.Series) -> pd.Series:
    """Boolean: squeeze fires when state transitions from >=1 to 0."""
    prev = state.shift(1)
    return (prev >= 1) & (state == 0)


def donchian_mid(df: pd.DataFrame, length: int) -> pd.Series:
    return 0.5 * (df["high"].rolling(length).max() + df["low"].rolling(length).min())


def momentum_hist(df: pd.DataFrame, length: int = 13) -> pd.Series:
    """TTM momentum histogram (T3): linreg endpoint of
    close - 0.5*(donchian_mid(L) + SMA(close,L)) over L."""
    val = df["close"] - 0.5 * (donchian_mid(df, length) + sma(df["close"], length))
    return linreg_endpoint(val, length)


# histogram state codes
HIST_UP = "Up"                      # rising & >=0  (strongest long)
HIST_DOWN_DEC = "DownDecreasing"    # rising & <0   (early reversal up)
HIST_UP_DEC = "UpDecreasing"        # falling & >=0 (bullish fading)
HIST_DOWN = "Down"                  # falling & <0  (strongest short)


def momentum_state(hist: pd.Series) -> pd.Series:
    """Map the histogram to its 4 ToS color states."""
    rising = hist > hist.shift(1)
    nonneg = hist >= 0
    out = pd.Series(index=hist.index, dtype="object")
    out[rising & nonneg] = HIST_UP
    out[rising & ~nonneg] = HIST_DOWN_DEC
    out[~rising & nonneg] = HIST_UP_DEC
    out[~rising & ~nonneg] = HIST_DOWN
    return out


@dataclass
class Ribbon:
    ema8: pd.Series
    ema13: pd.Series
    ema21: pd.Series
    bullish: pd.Series       # EMA8 > EMA13 > EMA21
    bearish: pd.Series       # EMA8 < EMA13 < EMA21
    sloping_up: pd.Series    # EMA13 and EMA21 both rising
    sloping_down: pd.Series  # EMA13 and EMA21 both falling


def ema_ribbon(df: pd.DataFrame, lengths: tuple[int, int, int] = (8, 13, 21)) -> Ribbon:
    a, b, c = lengths
    e8, e13, e21 = ema(df["close"], a), ema(df["close"], b), ema(df["close"], c)
    return Ribbon(
        ema8=e8, ema13=e13, ema21=e21,
        bullish=(e8 > e13) & (e13 > e21),
        bearish=(e8 < e13) & (e13 < e21),
        sloping_up=(e13 > e13.shift(1)) & (e21 > e21.shift(1)),
        sloping_down=(e13 < e13.shift(1)) & (e21 < e21.shift(1)),
    )


@dataclass
class SqueezeFeatures:
    state: pd.Series
    fire: pd.Series
    hist: pd.Series
    hist_state: pd.Series
    ribbon: Ribbon


def compute(df: pd.DataFrame, length: int = 13) -> SqueezeFeatures:
    """All squeeze features in one pass (used by the signal layer)."""
    state = squeeze_state(df, length)
    hist = momentum_hist(df, length)
    return SqueezeFeatures(
        state=state,
        fire=fire(state),
        hist=hist,
        hist_state=momentum_state(hist),
        ribbon=ema_ribbon(df),
    )
