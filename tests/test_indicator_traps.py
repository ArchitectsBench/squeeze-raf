"""P2 indicator correctness — the traps (indicators/CLAUDE.md, BUILD_SPEC §7).

These are the checks that catch a port that silently manufactures edge. They run
on synthetic data with known analytic answers, so they're deterministic and need
no ToS export. Numeric fidelity vs ToS is locked separately by
test_indicator_fidelity.py once export fixtures are supplied.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import raf, squeeze
from indicators import ta


# --- TA primitives --------------------------------------------------------
def test_ema_span1_is_identity(noisy_df):
    pd.testing.assert_series_equal(
        ta.ema(noisy_df["close"], 1), noisy_df["close"], check_names=False
    )


def test_tema_of_constant_is_constant(const_df):
    out = ta.tema(const_df["close"], 28)
    assert np.allclose(out.to_numpy(), 50.0)


def test_linreg_endpoint_recovers_line(ramp_df):
    # close is a perfect line -> linreg endpoint equals the close itself (after warmup)
    out = ta.linreg_endpoint(ramp_df["close"], 13).dropna()
    actual = ramp_df["close"].reindex(out.index)
    assert np.allclose(out.to_numpy(), actual.to_numpy(), atol=1e-8)


def test_true_range_known_values():
    df = pd.DataFrame(
        {"high": [10, 12, 11], "low": [9, 8, 10], "close": [9.5, 11, 10.5]}
    )
    tr = ta.true_range(df)
    # bar0: H-L=1 ; bar1: max(4, |12-9.5|=2.5, |8-9.5|=1.5)=4 ; bar2: max(1,|11-11|=0,|10-11|=1)=1
    assert list(tr) == [1.0, 4.0, 1.0]


def test_stoch_normalized_in_range(noisy_df):
    v = ta.stoch_normalized(noisy_df, 5, 3).dropna()
    assert v.between(-1.0, 1.0).all()


# --- T2: squeeze BB(EMA basis) vs KC(SMA basis, EMA-of-TR width) -----------
def test_squeeze_basis_asymmetry(noisy_df):
    """BB upper must be built on an EMA basis; KC on an SMA basis. We reconstruct
    both bands from the documented formula and confirm the implementation matches
    — and that swapping EMA<->SMA would change the result (bases really differ)."""
    L = 13
    close = noisy_df["close"]
    bb_basis = ta.ema(close, L)
    kc_basis = ta.sma(close, L)
    # the two bases are genuinely different (so the asymmetry is observable)
    assert not np.allclose(bb_basis.dropna().to_numpy()[-50:],
                           kc_basis.dropna().to_numpy()[-50:])

    bb_upper = bb_basis + 2.0 * ta.stdev(close, L)
    shift = ta.ema(ta.true_range(noisy_df), L)
    kc_high = kc_basis + shift * 1.0
    expected = pd.Series(0, index=close.index, dtype="int64")
    expected = expected.mask(bb_upper <= kc_basis + shift * 2.0, 1)
    expected = expected.mask(bb_upper <= kc_basis + shift * 1.5, 2)
    expected = expected.mask(bb_upper <= kc_high, 3)
    expected = expected.mask(bb_upper.isna() | (kc_basis + shift).isna(), 0)

    got = squeeze.squeeze_state(noisy_df, L)
    pd.testing.assert_series_equal(got, expected, check_names=False)


def test_squeeze_state_encoding_not_flipped(noisy_df):
    """Tightest compression must be the HIGHEST code (3), no-squeeze = 0."""
    state = squeeze.squeeze_state(noisy_df, 13)
    assert set(state.unique()) <= {0, 1, 2, 3}
    # a fire is a >=1 -> 0 transition
    f = squeeze.fire(state)
    transitions = (state.shift(1) >= 1) & (state == 0)
    pd.testing.assert_series_equal(f, transitions, check_names=False)


def test_momentum_state_mapping():
    hist = pd.Series([0.0, 1.0, 0.5, -0.2, -0.5])
    st = squeeze.momentum_state(hist)
    # idx1 rising&>=0 Up; idx2 falling&>=0 UpDecreasing; idx3 falling&<0 Down; idx4 falling&<0 Down
    assert st.iloc[1] == squeeze.HIST_UP
    assert st.iloc[2] == squeeze.HIST_UP_DEC
    assert st.iloc[3] == squeeze.HIST_DOWN
    assert st.iloc[4] == squeeze.HIST_DOWN


# --- T1: RAF displaced TEMA is a BACKWARD lag (the look-ahead trap) --------
def test_displacement_sign_is_backward():
    assert raf.DISPLACEMENT_SIGN == +1, "displacement must be backward (shift(+14))"


def test_savgstate_no_lookahead_truncation_invariance(noisy_df):
    """THE look-ahead test. savgstate at bar t must not depend on any bar after t.
    Compute on the full series and on the series truncated at t; they must agree up
    to t. A forward shift (shift(-14)) makes savg[t] read tavg[t+14] (the future),
    which breaks this — so this test FAILS on the canonical look-ahead bug."""
    full = raf.savgstate(noisy_df)
    cut = 400
    trunc = raf.savgstate(noisy_df.iloc[:cut])
    # compare the overlap, ignoring the last `displacement` bars near the cut edge
    # which legitimately differ only because the backward window isn't the issue —
    # backward shift means trunc[:cut] should match full[:cut] EXACTLY.
    pd.testing.assert_series_equal(
        full.iloc[:cut], trunc, check_names=False
    )


def test_savg_equals_past_tema(noisy_df):
    """Directly: the displaced series is tavg from `displacement` bars ago."""
    tavg = ta.tema(noisy_df["close"], raf.PRO_LENGTH)
    savg_backward = tavg.shift(14)
    # reconstruct savgstate using the backward savg and compare to the port
    matr = 0.5 * ta.atr(noisy_df, 10)
    expected = pd.Series(0, index=noisy_df.index, dtype="int64")
    expected = expected.mask(noisy_df["low"] > savg_backward + matr, 1)
    expected = expected.mask(noisy_df["high"] < savg_backward - matr, -1)
    expected = expected.mask(savg_backward.isna() | matr.isna(), 0)
    pd.testing.assert_series_equal(raf.savgstate(noisy_df), expected, check_names=False)


# --- RAF SC staging + Fisher ----------------------------------------------
def test_setup_count_builds_buy_on_cross_above():
    # k2 negative throughout (BUY regime); k1 crosses above k2 twice -> SC climbs to +2
    k2 = pd.Series([-0.5, -0.5, -0.5, -0.5, -0.5, -0.5])
    k1 = pd.Series([-0.9, -0.4, -0.9, -0.3, -0.9, -0.3])  # cross above at t=1,3,5
    sc = raf.setup_count(k1, k2)
    assert sc.iloc[1] == 1   # Ready Buy
    assert sc.iloc[3] == 2   # Aim Buy
    assert sc.iloc[-1] == 3


def test_setup_count_builds_sell_on_cross_below():
    k2 = pd.Series([0.5, 0.5, 0.5, 0.5, 0.5])
    k1 = pd.Series([0.9, 0.3, 0.9, 0.2, 0.9])  # cross below at t=1,3
    sc = raf.setup_count(k1, k2)
    assert sc.iloc[1] == -1
    assert sc.iloc[3] == -2


def test_fisher_first_step_value():
    # f3[0] = 0.5*ln((1+x)/(1-x)) with prev=0
    x = 0.5
    k3 = pd.Series([x, 0.0, 0.0])
    f = raf.fisher_major(k3)
    assert np.isclose(f.iloc[0], 0.5 * np.log((1 + x) / (1 - x)))


def test_raf_features_no_lookahead(noisy_df):
    """End-to-end: every RAF feature at bar t is truncation-invariant."""
    cut = 450
    full = raf.compute(noisy_df)
    trunc = raf.compute(noisy_df.iloc[:cut])
    for attr in ("sc", "savgstate", "major", "fire_buy", "fire_sell"):
        pd.testing.assert_series_equal(
            getattr(full, attr).iloc[:cut], getattr(trunc, attr),
            check_names=False, check_dtype=False,
        )
