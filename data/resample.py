"""Resample native vendor bars into the canonical 2h / 4h signal bars.

Neither Polygon nor Alpha Vantage serves 2h/4h directly, so we fetch 1-hour
RTH bars and aggregate. The aggregation is **session-anchored**: bars are
bucketed from the 09:30 America/New_York open, regular-trading-hours only, so a
2h bar is 09:30-11:30 / 11:30-13:30 / 13:30-15:30 (last bucket is the 30-min
13:30-16:00 stub for 4h's second half) — consistent every day, which is what the
indicator-fidelity work in P2 needs. Extended hours are dropped upstream.

Look-ahead safety: each aggregated bar is timestamped at its **open** (label
left), and contains only data from that bucket — no future bars leak in
(CLAUDE.md rule 1 / T5). OHLCV aggregation: open=first, high=max, low=min,
close=last, volume=sum.

This module never invents bars: a bucket with no underlying 1h bars produces no
output row, so genuine holes stay visible to the integrity checker.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import OHLCV_COLUMNS, normalize_ohlcv

MARKET_TZ = "America/New_York"
SESSION_OPEN = "09:30"
SESSION_CLOSE = "16:00"

_AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}

# pandas resample rule per intraday target timeframe (09:30-anchored). 15m/30m can be
# derived from a finer 5min base so a single 5min pull feeds every intraday timeframe.
_RULE = {"15min": "15min", "30min": "30min", "1h": "1h", "2h": "2h", "3h": "3h", "4h": "4h"}

# multi-day timeframes built from daily bars
_MULTIDAY = {"2d", "3d", "4d", "1w"}


def filter_rth(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only regular-trading-hours bars (09:30-16:00 ET), drop weekends.

    Works on a UTC-indexed frame; comparison is done in market-local time so it
    is DST-correct.
    """
    et = df.index.tz_convert(MARKET_TZ)
    in_hours = (et.time >= pd.Timestamp(SESSION_OPEN).time()) & (
        et.time < pd.Timestamp(SESSION_CLOSE).time()
    )
    weekday = et.dayofweek < 5
    return df[in_hours & weekday]


def resample_intraday(hourly: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate 30min RTH bars to 1h/2h/3h/4h, anchored to the session open.

    `hourly` must be the canonical schema (UTC index, OHLCV). Returns the same
    schema at the coarser timeframe. The last bucket of the day may be a stub
    (e.g. 3h -> 09:30-12:30, 12:30-15:30, 15:30-16:00).
    """
    if timeframe not in _RULE:
        raise ValueError(f"resample_intraday handles {set(_RULE)}, got {timeframe!r}")

    hourly = normalize_ohlcv(hourly)
    rth = filter_rth(hourly)
    if rth.empty:
        return rth

    # Resample per ET session day so buckets always start at 09:30 local and
    # never straddle the overnight gap. `origin="start_day"` + the 09:30 offset
    # anchors the first bucket to the open.
    et = rth.tz_convert(MARKET_TZ)
    open_offset = pd.Timedelta(SESSION_OPEN + ":00")
    parts = []
    for _, day_df in et.groupby(et.index.date):
        agg = day_df.resample(
            _RULE[timeframe],
            origin="start_day",
            offset=open_offset,
            label="left",
            closed="left",
        ).agg(_AGG)
        agg = agg.dropna(subset=["open"])  # drop empty leading/trailing buckets
        parts.append(agg)

    out = pd.concat(parts) if parts else et.iloc[0:0]
    out = out.tz_convert("UTC")
    return normalize_ohlcv(out[OHLCV_COLUMNS])


def resample_multiday(daily: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Aggregate DAILY bars to 2d/3d/4d (N consecutive TRADING days) or 1w (week
    ending Friday). N-day buckets are anchored from the start of history so each
    completed bucket is stable; each bar is labelled by its LAST trading day (the
    bar is complete as of that date -> no look-ahead)."""
    daily = normalize_daily_index(daily).sort_index()
    if daily.empty:
        return daily
    if timeframe == "1w":
        out = daily.resample("W-FRI", label="right", closed="right").agg(_AGG)
    else:
        n = int(timeframe[:-1])
        grp = np.arange(len(daily)) // n
        out = daily.groupby(grp).agg(_AGG)
        out.index = pd.DatetimeIndex(
            [daily.index[np.where(grp == b)[0][-1]] for b in range(int(grp.max()) + 1)]
        )
        out.index.name = "timestamp"
    out = out.dropna(subset=["open"])
    return normalize_ohlcv(out[OHLCV_COLUMNS])


def normalize_daily_index(df: pd.DataFrame) -> pd.DataFrame:
    """Canonicalize a daily frame's index to UTC-midnight of the ET trading
    date, so daily bars from different vendors line up exactly on the date."""
    df = normalize_ohlcv(df)
    et_dates = df.index.tz_convert(MARKET_TZ).normalize()
    df.index = pd.DatetimeIndex(et_dates.date).tz_localize("UTC")
    df.index.name = "timestamp"
    return normalize_ohlcv(df)


# Intraday bases we accept. 30min is preferred for US equities because the
# bar grid includes a clean 09:30 open bar; vendor 1h bars are anchored to the
# top of the hour (09:00) and fold the open into a pre-09:30 bucket, so they
# would silently drop the opening half-hour after RTH filtering.
_INTRADAY_BASES = {"30min", "1h"}


# Native intraday resolutions returned as-is (RTH-filtered, no resampling).
_NATIVE_INTRADAY = {"5min", "15min", "30min"}


def to_timeframe(base: pd.DataFrame, timeframe: str, *, base_freq: str) -> pd.DataFrame:
    """Dispatch: daily passes through (already native), native intraday resolutions
    (15min/30min) are RTH-filtered as-is, coarser intraday (2h/4h) is resampled from a
    finer base. `base_freq` documents what `base` actually is."""
    if timeframe == "1d":
        return normalize_daily_index(base)
    if timeframe in _MULTIDAY:                 # 2d/3d/4d/1w built from daily bars
        return resample_multiday(base, timeframe)
    if timeframe in _NATIVE_INTRADAY:
        return filter_rth(base)
    if base_freq not in _INTRADAY_BASES:
        raise ValueError(
            f"intraday {timeframe} must be built from {_INTRADAY_BASES} bars, "
            f"got base_freq={base_freq!r}"
        )
    return resample_intraday(base, timeframe)
