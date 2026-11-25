"""Shared test fixtures: synthetic OHLCV builders with known properties."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _frame(close: np.ndarray, seed: int = 0) -> pd.DataFrame:
    """Build an OHLCV frame around a close path with small, consistent wicks."""
    rng = np.random.default_rng(seed)
    n = len(close)
    idx = pd.bdate_range("2022-01-03", periods=n, tz="UTC")
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.05, n)) + 0.01
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.05, n)) - 0.01
    vol = rng.integers(1_000, 5_000, n).astype(float)
    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )
    df.index.name = "timestamp"
    return df


@pytest.fixture
def ramp_df() -> pd.DataFrame:
    """Strictly increasing close (linear ramp) — exact for regression/TEMA tests."""
    return _frame(np.linspace(100.0, 200.0, 300))


@pytest.fixture
def const_df() -> pd.DataFrame:
    """Flat close — EMA/TEMA must equal the constant; stdev -> 0."""
    return _frame(np.full(200, 50.0))


@pytest.fixture
def noisy_df() -> pd.DataFrame:
    """Random walk — general-purpose, deterministic via seed."""
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 1.0, 600))
    return _frame(close, seed=7)
