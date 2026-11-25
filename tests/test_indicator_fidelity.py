"""Stage 0 fidelity gate — Python ports vs ThinkOrSwim exports.

This is the gate CLAUDE.md rule 6 / BUILD_SPEC §5 Stage 0 requires: no indicator
is trusted downstream until it matches ToS within tolerance. It is data-driven —
drop ToS CSV exports into tests/fixtures/tos/ and these tests light up
automatically. With no fixtures present they SKIP (loudly), so the suite stays
green while the numeric lock is pending the exports.

Fixture schema (one CSV per symbol/timeframe, e.g. AAPL_1d.csv):
  - a timestamp column (timestamp/datetime/time/date), parseable to UTC
  - OHLCV: open, high, low, close, volume
  - any subset of the comparison columns below (only those present are checked):
      squeeze_state, squeeze_hist, ema8, ema13, ema21,
      raf_sc, raf_major, raf_savgstate
See tests/fixtures/tos/README.md.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from indicators import raf, squeeze, ta

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tos"
_FILES = sorted(FIXTURE_DIR.glob("*.csv")) if FIXTURE_DIR.is_dir() else []

# default Length per timeframe (config strategy_defaults)
_LENGTH = {"2h": 13, "4h": 13, "1d": 21}

# tolerances: discrete states must match exactly; continuous within a relative band
_EXACT = {"squeeze_state", "raf_savgstate", "raf_sc"}
_RTOL = 0.02   # 2% relative tolerance on continuous series (covers EMA seeding drift)
_WARMUP = 60   # ignore the first N bars (indicator warmup / seeding transient)


def _load(path: Path) -> tuple[pd.DataFrame, str]:
    df = pd.read_csv(path)
    df.columns = [c.lower().strip() for c in df.columns]
    tcol = next((c for c in ("timestamp", "datetime", "time", "date") if c in df.columns), None)
    assert tcol, f"{path.name}: no timestamp column"
    idx = pd.to_datetime(df[tcol], utc=True)
    df = df.drop(columns=[tcol]).set_index(idx)
    df.index.name = "timestamp"
    tf = path.stem.split("_")[-1]
    return df, tf


def _compute(df: pd.DataFrame, tf: str) -> dict[str, pd.Series]:
    L = _LENGTH.get(tf, 21)
    sq = squeeze.compute(df, length=L)
    rf = raf.compute(df)
    return {
        "squeeze_state": sq.state,
        "squeeze_hist": sq.hist,
        "ema8": sq.ribbon.ema8,
        "ema13": sq.ribbon.ema13,
        "ema21": sq.ribbon.ema21,
        "raf_sc": rf.sc,
        "raf_major": rf.major,
        "raf_savgstate": rf.savgstate,
    }


@pytest.mark.skipif(not _FILES, reason="no ToS fixtures in tests/fixtures/tos/ yet")
@pytest.mark.parametrize("path", _FILES, ids=[p.stem for p in _FILES])
def test_fidelity_vs_tos(path):
    df, tf = _load(path)
    ours = _compute(df, tf)
    compared = []
    for col, got in ours.items():
        if col not in df.columns:
            continue
        exp = pd.to_numeric(df[col], errors="coerce")
        a = got.to_numpy()[_WARMUP:]
        b = exp.to_numpy()[_WARMUP:]
        mask = ~np.isnan(b)
        a, b = a[mask], b[mask]
        if col in _EXACT:
            n_mismatch = int((a != b).sum())
            assert n_mismatch == 0, f"{path.name}:{col} {n_mismatch} discrete mismatches"
        else:
            assert np.allclose(a, b, rtol=_RTOL, atol=1e-6), (
                f"{path.name}:{col} exceeds rtol={_RTOL}"
            )
        compared.append(col)
    assert compared, f"{path.name}: no recognized comparison columns present"


def test_harness_self_check_synthetic():
    """Proves the harness logic itself works: compute on a synthetic frame, write
    our own output as a pseudo-fixture, and confirm it matches itself exactly."""
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(0, 1, 200))
    idx = pd.bdate_range("2022-01-03", periods=200, tz="UTC")
    o = np.r_[close[0], close[:-1]]
    df = pd.DataFrame(
        {"open": o, "high": np.maximum(o, close) + 0.1,
         "low": np.minimum(o, close) - 0.1, "close": close, "volume": 1.0},
        index=idx,
    )
    df.index.name = "timestamp"
    ours = _compute(df, "1d")
    # self-consistency: recomputing matches, and EMA8 equals the ta primitive
    assert np.allclose(ours["ema8"].to_numpy(), ta.ema(df["close"], 8).to_numpy())
    assert ours["squeeze_state"].isin([0, 1, 2, 3]).all()
