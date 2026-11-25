"""Data-integrity gate for Phase P1 (CLAUDE.md: TEST-FIRST for data hygiene).

Proves two directions:
  1. A clean, well-formed frame PASSES `check_integrity` for every timeframe.
  2. Each defect we care about (duplicate timestamp, non-monotonic index, NaN
     bar, intraday/daily gap, broken OHLC) is CAUGHT.

Plus round-trips through the parquet cache and checks the session-anchored
resampler. These are synthetic frames — no network, no API keys — so the gate is
deterministic and runs in CI.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.cache import ParquetCache
from data.resample import resample_intraday
from data.schema import OHLCV_COLUMNS, check_integrity, normalize_ohlcv

ET = "America/New_York"

# Per-day ET bar times that satisfy the integrity grid for each timeframe.
SESSION_TIMES = {
    "2h": ["09:30", "11:30", "13:30", "15:30"],
    "4h": ["09:30", "13:30"],
}


def _ohlcv(n: int, seed: int = 0) -> dict:
    """Internally consistent OHLCV columns of length n (high>=O,C,L; low<=...)."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    open_ = close + rng.normal(0, 0.2, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.3, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.3, n))
    vol = rng.integers(1_000, 10_000, n).astype(float)
    return {"open": open_, "high": high, "low": low, "close": close, "volume": vol}


def make_clean(timeframe: str, n_days: int = 20, seed: int = 1) -> pd.DataFrame:
    """A clean frame on the canonical schema for the given timeframe."""
    days = pd.bdate_range("2024-01-02", periods=n_days)
    if timeframe == "1d":
        idx = pd.DatetimeIndex(days.date).tz_localize("UTC")
    else:
        stamps = []
        for d in days:
            for t in SESSION_TIMES[timeframe]:
                stamps.append(pd.Timestamp(f"{d.date()} {t}", tz=ET))
        idx = pd.DatetimeIndex(stamps).tz_convert("UTC")
    df = pd.DataFrame(_ohlcv(len(idx), seed), index=idx)
    df.index.name = "timestamp"
    return normalize_ohlcv(df)


# --- direction 1: clean frames pass --------------------------------------
@pytest.mark.parametrize("tf", ["2h", "4h", "1d"])
def test_clean_frame_passes(tf):
    rep = check_integrity(make_clean(tf), "TEST", tf)
    assert rep.ok, f"clean {tf} frame should pass, got: {rep.violations}"


@pytest.mark.parametrize("tf", ["2h", "4h", "1d"])
def test_clean_frame_schema(tf):
    df = make_clean(tf)
    assert list(df.columns) == OHLCV_COLUMNS
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing and df.index.is_unique
    assert df.index.name == "timestamp"


# --- direction 2: defects are caught -------------------------------------
def test_duplicate_timestamp_caught():
    df = make_clean("1d")
    dup = pd.concat([df, df.iloc[[5]]]).sort_index()  # bypass normalize de-dup
    rep = check_integrity(dup, "TEST", "1d")
    assert not rep.ok
    assert any("duplicate" in v for v in rep.violations)


def test_non_monotonic_caught():
    df = make_clean("1d")
    shuffled = df.iloc[[0, 2, 1, 3, 4]]  # swap two rows
    rep = check_integrity(shuffled, "TEST", "1d")
    assert not rep.ok
    assert any("monoton" in v for v in rep.violations)


def test_nan_bar_caught():
    df = make_clean("2h")
    df.iloc[10, df.columns.get_loc("close")] = np.nan
    rep = check_integrity(df, "TEST", "2h")
    assert not rep.ok
    assert any("NaN" in v for v in rep.violations)


def test_intraday_gap_caught():
    df = make_clean("2h", n_days=5)
    # drop an interior bar inside one session -> a hole on the 2h grid
    df = df.drop(df.index[2])
    rep = check_integrity(df, "TEST", "2h")
    assert not rep.ok
    assert any("intraday gap" in v for v in rep.violations)


def test_daily_gap_caught():
    """A RECENT >5-day hole must BLOCK (it gates trading on fresh data)."""
    days = pd.bdate_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=30)
    idx = pd.DatetimeIndex(days.date).tz_localize("UTC")
    df = make_clean("1d", n_days=30)
    df.index = idx
    df.index.name = "timestamp"
    cut_lo, cut_hi = idx[10], idx[20]   # excise ~2 weeks in the middle
    rep = check_integrity(df.loc[(df.index < cut_lo) | (df.index > cut_hi)], "TEST", "1d")
    assert not rep.ok
    assert any("daily gap" in v for v in rep.violations)


def test_historic_daily_gap_is_warning_not_blocking():
    """An OLD gap (ticker change / late IPO, e.g. FB->META 2022) must be reported
    as a warning but NOT block — recent-data integrity is what gates trading."""
    df = make_clean("1d", n_days=30)   # fixed 2024 dates -> historic by construction
    keep = df.index[(df.index < "2024-01-15") | (df.index > "2024-01-29")]
    rep = check_integrity(df.loc[keep], "TEST", "1d")
    assert rep.ok
    assert any("historic" in w and "daily gap" in w for w in rep.warnings)


def test_intraday_block_gap_caught():
    """A missing multi-session BLOCK (e.g. a ticker-change hole) must be caught,
    even though each surviving session is internally contiguous (regression for
    the META FB->META gap that slipped past the per-session check)."""
    df = make_clean("2h", n_days=20)
    # excise ~2 weeks in the middle -> a multi-session hole on the intraday grid
    keep = df.index[(df.index < "2024-01-15") | (df.index > "2024-01-29")]
    rep = check_integrity(df.loc[keep], "TEST", "2h")
    assert not rep.ok
    assert any("intraday block gap" in v for v in rep.violations)


def test_broken_ohlc_caught():
    df = make_clean("1d")
    # make high lower than close -> OHLC inconsistency
    df.iloc[7, df.columns.get_loc("high")] = df.iloc[7, df.columns.get_loc("low")] - 1
    rep = check_integrity(df, "TEST", "1d")
    assert not rep.ok
    assert any("high>=" in v for v in rep.violations)


def test_negative_price_caught():
    df = make_clean("1d")
    df.iloc[3, df.columns.get_loc("low")] = -5.0
    rep = check_integrity(df, "TEST", "1d")
    assert not rep.ok
    assert any("non-positive" in v for v in rep.violations)


def test_empty_frame_caught():
    rep = check_integrity(make_clean("1d").iloc[0:0], "TEST", "1d")
    assert not rep.ok
    assert any("empty" in v for v in rep.violations)


# --- cache round-trip -----------------------------------------------------
def test_cache_roundtrip_preserves_clean_frame(tmp_path):
    cache = ParquetCache(root=tmp_path / "cache")
    df = make_clean("4h")
    path = cache.write("TEST", "4h", df, vendor="synthetic",
                       cfg={"vendor": "synthetic", "start": "x", "end": "y"})
    assert path.is_file()
    back = cache.read("TEST", "4h")
    pd.testing.assert_frame_equal(df, back)
    rep = check_integrity(back, "TEST", "4h")
    assert rep.ok
    meta = cache.read_meta("TEST", "4h")
    assert meta["vendor"] == "synthetic" and meta["rows"] == len(df)
    assert meta["cfg_hash"]  # config hash logged (rule 7)


def test_cache_miss_returns_none(tmp_path):
    cache = ParquetCache(root=tmp_path / "cache")
    assert cache.read("NOPE", "1d") is None


# --- resampler ------------------------------------------------------------
def _hourly_day(date: str) -> pd.DataFrame:
    """Seven 1h RTH bars (09:30..15:30 ET) for one day."""
    times = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]
    idx = pd.DatetimeIndex([pd.Timestamp(f"{date} {t}", tz=ET) for t in times])
    idx = idx.tz_convert("UTC")
    df = pd.DataFrame(_ohlcv(len(idx), seed=2), index=idx)
    df.index.name = "timestamp"
    return normalize_ohlcv(df)


def test_resample_2h_aggregation_and_grid():
    hourly = _hourly_day("2024-03-04")
    out = resample_intraday(hourly, "2h")
    # 09:30,11:30,13:30,15:30 ET -> 4 buckets
    assert len(out) == 4
    rep = check_integrity(out, "TEST", "2h")
    assert rep.ok, rep.violations
    # first 2h bar aggregates the first two 1h bars
    first = out.iloc[0]
    src = hourly.iloc[:2]
    assert first["open"] == src["open"].iloc[0]
    assert first["close"] == src["close"].iloc[-1]
    assert first["high"] == src["high"].max()
    assert first["low"] == src["low"].min()
    assert first["volume"] == src["volume"].sum()


def _halfhour_day(date: str) -> pd.DataFrame:
    """30-min RTH bars (09:30..15:30 ET) for one day — mirrors the Polygon base
    that production actually resamples from."""
    times = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in (0, 30)
             if not (h == 9 and m == 0)]  # 09:30..15:30
    idx = pd.DatetimeIndex([pd.Timestamp(f"{date} {t}", tz=ET) for t in times])
    idx = idx.tz_convert("UTC")
    df = pd.DataFrame(_ohlcv(len(idx), seed=3), index=idx)
    df.index.name = "timestamp"
    return normalize_ohlcv(df)


def test_resample_30min_captures_open():
    """The 09:30 open must survive resampling (regression for the 1h-base bug
    where the opening half-hour was dropped)."""
    base = _halfhour_day("2024-03-06")
    out = resample_intraday(base, "2h")
    et = out.index.tz_convert(ET)
    assert et[0].strftime("%H:%M") == "09:30"  # first 2h bar opens at the open
    assert len(out) == 4  # 09:30,11:30,13:30,15:30
    # first 2h bar's open == the 09:30 base bar's open (no lost open)
    assert out.iloc[0]["open"] == base.iloc[0]["open"]
    assert check_integrity(out, "TEST", "2h").ok


def test_resample_drops_extended_hours():
    # add a pre-market 08:30 bar; resampler must drop it (RTH only)
    base = _hourly_day("2024-03-05")
    pre = pd.DataFrame(
        _ohlcv(1, seed=9),
        index=pd.DatetimeIndex([pd.Timestamp("2024-03-05 08:30", tz=ET)]).tz_convert("UTC"),
    )
    pre.index.name = "timestamp"
    mixed = normalize_ohlcv(pd.concat([pre, base]))
    out = resample_intraday(mixed, "2h")
    et = out.index.tz_convert(ET)
    assert (et.time >= pd.Timestamp("09:30").time()).all()
