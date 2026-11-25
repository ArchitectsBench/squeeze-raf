"""P3 signal-layer tests: look-ahead safety + structural sanity of Confluence v0."""

from __future__ import annotations

import pandas as pd

from edge_check.event_study import collect_entries
from signals import ConfluenceParams, generate
from signals.confluence import MODES


def test_signals_no_lookahead_truncation_invariance(noisy_df):
    """Entries/conviction at bar t must not depend on bars after t (T5)."""
    cut = 450
    params = ConfluenceParams.for_timeframe("1d")
    for mode in MODES:
        full = generate(noisy_df, params, mode=mode)
        trunc = generate(noisy_df.iloc[:cut], params, mode=mode)
        pd.testing.assert_series_equal(
            full.long_entry.iloc[:cut], trunc.long_entry, check_names=False)
        pd.testing.assert_series_equal(
            full.short_entry.iloc[:cut], trunc.short_entry, check_names=False)
        pd.testing.assert_series_equal(
            full.conviction.iloc[:cut], trunc.conviction, check_names=False)


def test_no_simultaneous_long_and_short(noisy_df):
    params = ConfluenceParams.for_timeframe("1d")
    for mode in MODES:
        s = generate(noisy_df, params, mode=mode)
        assert not (s.long_entry & s.short_entry).any()


def test_conviction_in_unit_range(noisy_df):
    params = ConfluenceParams.for_timeframe("1d")
    for mode in MODES:
        s = generate(noisy_df, params, mode=mode)
        assert s.conviction.between(0.0, 1.0).all()


def test_forward_returns_signed_by_direction():
    """A short entry on a falling path yields a POSITIVE signed forward return."""
    n = 60
    close = pd.Series(range(n, 0, -1), dtype=float) + 100  # strictly falling
    idx = pd.bdate_range("2022-01-03", periods=n, tz="UTC")
    df = pd.DataFrame({"open": close.values, "high": close.values + 1,
                       "low": close.values - 1, "close": close.values,
                       "volume": 1.0}, index=idx)
    df.index.name = "timestamp"

    class FakeSig:
        direction = pd.Series(0, index=idx)
        conviction = pd.Series(0.0, index=idx)
    FakeSig.direction.iloc[5] = -1  # short at a falling point
    e = collect_entries(df, FakeSig, [4])
    assert e["ret_4"].iloc[0] > 0  # price fell, short profits -> positive bps


def test_entries_are_events_not_persistent(noisy_df):
    """Entry rate should be modest (events), not firing nearly every bar."""
    params = ConfluenceParams.for_timeframe("1d")
    for mode in MODES:
        s = generate(noisy_df, params, mode=mode)
        rate = (s.direction != 0).mean()
        assert rate < 0.5, f"mode {mode} fires on {rate:.0%} of bars — not event-like"
