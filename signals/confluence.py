"""Confluence v0 — the rule to validate BEFORE any optimization (BUILD_SPEC.md §3).

Symmetric long/short (D3). Signal is computed on close[t]; execution is next-bar
open, enforced by the backtest engine (P4) — nothing here peeks at the same bar's
future (T5). Default params come from config/universe.yaml strategy_defaults; this
module does NOT optimize anything (P3 is edge-check only).

Three trigger modes tested head-to-head (D11):
- Mode A (AND):     squeeze fires (state->0) + histogram rising/falling + RAF SC staged.
- Mode B (RAF-led): RAF full Fire + squeeze histogram not opposing.
- Mode C (score):   directional conviction crosses a threshold.

Every mode is additionally gated by:
- Regime gate: ribbon bullish (bearish) AND savgstate >= 0 (<= 0).
- Energy gate: squeeze state >= 1 within the last `energy_lookback` bars.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from indicators import raf, squeeze
from indicators.squeeze import HIST_DOWN, HIST_DOWN_DEC, HIST_UP, HIST_UP_DEC

from .scoring import directional_conviction

MODES = ("A", "B", "C")


@dataclass
class ConfluenceParams:
    length: int = 13            # squeeze Length (13 for 2h/4h, 21 for 1d)
    energy_lookback: int = 6    # squeeze state>=1 within last N bars
    sc_ready: int = 1           # min |SC| for Mode A (Ready Buy/Sell)
    score_threshold: float = 0.5  # Mode C entry threshold
    pro_length: int = 28        # RAF displaced-TEMA length
    confluence_window: int = 6  # co-occurrence window for A/B (tied to energy_lookback;
    #                             a squeeze fire and a RAF stage rarely land on the SAME
    #                             bar — the two engines are opposite — so confluence is
    #                             read as "both within the last K bars", K=energy_lookback)

    @classmethod
    def for_timeframe(cls, tf: str, **overrides) -> "ConfluenceParams":
        length = 21 if tf == "1d" else 13
        return cls(length=length, **overrides)


@dataclass
class Signals:
    long_entry: pd.Series
    short_entry: pd.Series
    conviction: pd.Series   # conviction of the active entry's direction at each bar
    direction: pd.Series    # +1 / -1 / 0 (the entered direction, 0 if no entry)
    sq: squeeze.SqueezeFeatures
    rf: raf.RAFFeatures


def _energy(state: pd.Series, lookback: int) -> pd.Series:
    """squeeze was loaded (state>=1) at some point within the last `lookback` bars
    (inclusive, look-back only)."""
    return (state >= 1).rolling(lookback, min_periods=1).max().astype(bool)


def _within(cond: pd.Series, window: int) -> pd.Series:
    """True if `cond` was True at any of the last `window` bars (look-back only)."""
    return cond.fillna(False).rolling(window, min_periods=1).max().astype(bool)


def _rising_edge(cond: pd.Series) -> pd.Series:
    """Discrete event: True only on the bar `cond` turns True (no repeat fills
    while it stays True within the confluence window)."""
    return cond & ~cond.shift(1, fill_value=False)


def generate(df: pd.DataFrame, params: ConfluenceParams, mode: str = "A") -> Signals:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    sq = squeeze.compute(df, length=params.length)
    rf = raf.compute(df, pro_length=params.pro_length)

    regime_long = sq.ribbon.bullish & (rf.savgstate >= 0)
    regime_short = sq.ribbon.bearish & (rf.savgstate <= 0)
    energy = _energy(sq.state, params.energy_lookback)

    hist_rising = sq.hist > sq.hist.shift(1)
    conv_long = directional_conviction(sq, rf, +1)
    conv_short = directional_conviction(sq, rf, -1)
    K = params.confluence_window

    if mode == "A":
        # squeeze fired recently AND RAF staged recently (windowed confluence),
        # confirmed by current-bar histogram direction.
        fire_recent = _within(sq.fire, K)
        sc_long_recent = _within(rf.sc >= params.sc_ready, K)
        sc_short_recent = _within(rf.sc <= -params.sc_ready, K)
        trig_long = fire_recent & sc_long_recent & hist_rising
        trig_short = fire_recent & sc_short_recent & (~hist_rising)
    elif mode == "B":
        # RAF full Fire recently AND squeeze histogram not opposing now.
        non_bearish = sq.hist_state.isin([HIST_UP, HIST_UP_DEC])
        non_bullish = sq.hist_state.isin([HIST_DOWN, HIST_DOWN_DEC])
        trig_long = _within(rf.fire_buy, K) & non_bearish
        trig_short = _within(rf.fire_sell, K) & non_bullish
    else:  # mode C — conviction crosses up through the threshold (discrete events)
        thr = params.score_threshold
        trig_long = (conv_long >= thr) & (conv_long.shift(1) < thr)
        trig_short = (conv_short >= thr) & (conv_short.shift(1) < thr)

    # gate, then reduce to discrete entry events (rising edge of the gated trigger)
    long_entry = _rising_edge((regime_long & energy & trig_long).fillna(False))
    short_entry = _rising_edge((regime_short & energy & trig_short).fillna(False))
    # a bar cannot be both; if it somehow is, drop both (ambiguous — no trade)
    both = long_entry & short_entry
    long_entry &= ~both
    short_entry &= ~both

    direction = pd.Series(0, index=df.index, dtype="int64")
    direction = direction.mask(long_entry, 1).mask(short_entry, -1)
    conviction = pd.Series(0.0, index=df.index)
    conviction = conviction.mask(long_entry, conv_long).mask(short_entry, conv_short)

    return Signals(
        long_entry=long_entry, short_entry=short_entry,
        conviction=conviction, direction=direction, sq=sq, rf=rf,
    )


# --- Confluence v1: multi-TF alignment + conviction filter (P6-fix) ----------
ALIGN_TF_OFFSET = pd.Timedelta("4h")  # the alignment timeframe's bar length


def align_regime(df_align: pd.DataFrame, target_index: pd.DatetimeIndex,
                 pro_length: int = 28, length: int = 13):
    """Higher-timeframe (4h) directional regime, aligned causally onto a finer
    (2h) index. The 4h bar labelled L is only COMPLETE at L+4h, so we stamp each
    regime value at its completion time before forward-filling — no look-ahead.

    Returns (align_long, align_short) boolean Series on `target_index`.
    """
    sq4 = squeeze.compute(df_align, length=length)
    rf4 = raf.compute(df_align, pro_length=pro_length)
    reg_long = (sq4.ribbon.bullish & (rf4.savgstate >= 0))
    reg_short = (sq4.ribbon.bearish & (rf4.savgstate <= 0))

    def lift(s: pd.Series) -> pd.Series:
        completed = pd.Series(s.to_numpy(), index=s.index + ALIGN_TF_OFFSET)
        return completed.reindex(target_index, method="ffill").fillna(False).astype(bool)

    return lift(reg_long), lift(reg_short)


def generate_squeeze(df: pd.DataFrame, params: ConfluenceParams) -> Signals:
    """SQUEEZE-ONLY signal (no RAF) — a pure volatility-expansion breakout.

    Long: squeeze fired (state->0) within the last `confluence_window` bars AND the
    EMA ribbon is bullish AND momentum histogram is positive & rising. Short mirrors.
    The fire's prior compression IS the energy gate. Larger/faster moves than the
    RAF-combined rule (expansion, not exhaustion). `rf` is None (RAF unused)."""
    sq = squeeze.compute(df, length=params.length)
    K = params.confluence_window
    fire_recent = _within(sq.fire, K)
    hist_rising = sq.hist > sq.hist.shift(1)

    trig_long = fire_recent & sq.ribbon.bullish & (sq.hist > 0) & hist_rising
    trig_short = fire_recent & sq.ribbon.bearish & (sq.hist < 0) & (~hist_rising)
    long_entry = _rising_edge(trig_long.fillna(False))
    short_entry = _rising_edge(trig_short.fillna(False))
    both = long_entry & short_entry
    long_entry &= ~both
    short_entry &= ~both

    def conv(direction):
        comp = sq.state / 3.0
        rib = (sq.ribbon.bullish if direction > 0 else sq.ribbon.bearish).astype(float)
        slope = (sq.ribbon.sloping_up if direction > 0
                 else sq.ribbon.sloping_down).astype(float)
        mom = ((sq.hist > 0) if direction > 0 else (sq.hist < 0)).astype(float)
        return (0.3 * comp + 0.3 * rib + 0.2 * slope + 0.2 * mom).clip(0.0, 1.0)

    conv_long, conv_short = conv(1), conv(-1)
    direction = pd.Series(0, index=df.index, dtype="int64").mask(long_entry, 1).mask(short_entry, -1)
    conviction = pd.Series(0.0, index=df.index).mask(long_entry, conv_long).mask(short_entry, conv_short)
    return Signals(long_entry=long_entry, short_entry=short_entry, conviction=conviction,
                   direction=direction, sq=sq, rf=None)


_WEEK_AGG = {"open": "first", "high": "max", "low": "min", "close": "last",
             "volume": "sum"}


def weekly_frame(daily_df: pd.DataFrame) -> pd.DataFrame:
    """Resample a daily OHLCV frame to weekly bars (week-ending Friday)."""
    return daily_df.resample("W-FRI").agg(_WEEK_AGG).dropna()


def weekly_regime(daily_df: pd.DataFrame, target_index: pd.DatetimeIndex):
    """Weekly EMA-ribbon trend, aligned causally onto the daily index. A weekly bar
    (Fri label) is complete at its Friday close; daily bars forward-fill the most
    recent COMPLETED week, so Mon-Thu use last week and Friday uses the week that
    just closed — no look-ahead. Returns (align_long, align_short)."""
    w = weekly_frame(daily_df)
    if len(w) < 25:
        f = pd.Series(False, index=target_index)
        return f, f
    rib = squeeze.ema_ribbon(w)
    long = rib.bullish.reindex(target_index, method="ffill").fillna(False).astype(bool)
    short = rib.bearish.reindex(target_index, method="ffill").fillna(False).astype(bool)
    return long, short


def generate_v1(df: pd.DataFrame, params: ConfluenceParams, conviction_min: float,
                align_long: pd.Series, align_short: pd.Series,
                mode: str = "A") -> Signals:
    """Confluence v1 = base Mode A entries AND (higher-TF regime agrees) AND
    (conviction >= conviction_min). Fewer, higher-quality entries; lower turnover."""
    base = generate(df, params, mode=mode)
    conv_long = directional_conviction(base.sq, base.rf, +1)
    conv_short = directional_conviction(base.sq, base.rf, -1)

    le = (base.long_entry & (conv_long >= conviction_min) & align_long).fillna(False)
    se = (base.short_entry & (conv_short >= conviction_min) & align_short).fillna(False)
    both = le & se
    le &= ~both
    se &= ~both

    direction = pd.Series(0, index=df.index, dtype="int64").mask(le, 1).mask(se, -1)
    conviction = pd.Series(0.0, index=df.index).mask(le, conv_long).mask(se, conv_short)
    return Signals(long_entry=le, short_entry=se, conviction=conviction,
                   direction=direction, sq=base.sq, rf=base.rf)
