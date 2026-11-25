"""P9 recommender tests: alignment-bonus logic, fixed-date determinism, causality."""

from __future__ import annotations

import pandas as pd

from data.cache import ParquetCache
from data.universe import load_universe
from reporting.recommend import _alignment, _longer_bucket, scan


def test_alignment_bonus_logic():
    assert _alignment(1, 1, 1) == (0.20, "4h+1d aligned")
    assert _alignment(1, -1, 1)[0] == -0.15      # a conflict dominates
    assert _alignment(1, 1, 0) == (0.10, "partial align")
    assert _alignment(1, 0, 0) == (0.0, "neutral")
    assert _alignment(-1, -1, -1) == (0.20, "4h+1d aligned")


def test_longer_bucket_prefers_longer_horizon():
    assert _longer_bucket("1d") == "2d"
    assert _longer_bucket("2d") == "3d"
    assert _longer_bucket("5d") == "5d"          # capped at the longest


def test_recs_reproduce_on_fixed_date():
    """The P9 gate: recommendations for a fixed historical date are deterministic."""
    u = load_universe("broad")
    cache = ParquetCache()
    as_of = pd.Timestamp("2026-05-15", tz="UTC")
    a = scan(u, cache, as_of)
    b = scan(u, cache, as_of)
    if a.empty and b.empty:
        return
    pd.testing.assert_frame_equal(a, b)


def test_as_of_is_causal():
    """No recommendation may reference a signal after the as-of date (no look-ahead)."""
    u = load_universe("broad")
    cache = ParquetCache()
    as_of = pd.Timestamp("2026-03-02", tz="UTC")
    df = scan(u, cache, as_of)
    if not df.empty:
        assert (df["signal_time"] <= as_of).all()
