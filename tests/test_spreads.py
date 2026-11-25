"""P7 hand-checks for the structuring layer (gate: recs sane vs hand-checks)."""

from __future__ import annotations


from spreads import blackscholes as bs
from spreads.dte import bucket_for_timeframe, dte_band, representative_dte
from spreads.strikes import build_spread
from spreads.structure import (
    BEAR_CALL_CREDIT,
    BEAR_PUT_DEBIT,
    BULL_CALL_DEBIT,
    BULL_PUT_CREDIT,
    choose,
)


# --- Black-Scholes sanity ---
def test_atm_call_delta_near_half():
    d = bs.delta(100, 100, 0.3, 30 / 365, kind="call")
    assert 0.5 < d < 0.6  # slight positive drift from 0.5*sigma^2 term


def test_strike_from_delta_roundtrips():
    S, sig, T = 100, 0.3, 30 / 365
    for target in (0.5, 0.3, 0.25):
        K = bs.strike_from_delta(S, target, sig, T, kind="call")
        assert abs(abs(bs.delta(S, K, sig, T, kind="call")) - target) < 1e-3


def test_put_call_strike_ordering():
    S, sig, T = 100, 0.3, 30 / 365
    # 0.27-delta call is OTM (above spot); 0.27-delta put is OTM (below spot)
    assert bs.strike_from_delta(S, 0.27, sig, T, kind="call") > S
    assert bs.strike_from_delta(S, 0.27, sig, T, kind="put") < S


# --- structure matrix (§6) ---
def test_structure_matrix():
    assert choose(+1, "low").code == BULL_CALL_DEBIT
    assert choose(+1, "high").code == BULL_PUT_CREDIT
    assert choose(-1, "low").code == BEAR_PUT_DEBIT
    assert choose(-1, "high").code == BEAR_CALL_CREDIT
    assert choose(+1, "mid").code == BULL_CALL_DEBIT and choose(+1, "mid").discretionary


# --- spread arithmetic hand-checks ---
def test_bull_call_debit_arithmetic():
    sp = build_spread(100, 0.30, 7, choose(+1, "low"))
    assert sp.kind == "debit" and sp.option_type == "call"
    assert sp.short_strike > sp.long_strike            # sell higher call
    assert 0 < sp.net < sp.width                       # debit less than width
    assert abs(sp.max_profit - (sp.width - sp.net)) < 1e-6
    assert abs(sp.max_loss - sp.net) < 1e-6
    assert abs(sp.breakeven - (sp.long_strike + sp.net)) < 1e-6
    assert 0 < sp.pop < 1
    # long leg near ATM (~0.5 delta), short leg ~0.25-0.30
    assert 0.42 <= sp.long_delta <= 0.60
    assert 0.20 <= sp.short_delta <= 0.34


def test_bear_put_debit_arithmetic():
    sp = build_spread(100, 0.30, 7, choose(-1, "low"))
    assert sp.kind == "debit" and sp.option_type == "put"
    assert sp.short_strike < sp.long_strike            # sell lower put
    assert abs(sp.breakeven - (sp.long_strike - sp.net)) < 1e-6
    assert 0 < sp.net < sp.width


def test_bull_put_credit_arithmetic():
    sp = build_spread(100, 0.45, 10, choose(+1, "high"))
    assert sp.kind == "credit" and sp.option_type == "put"
    assert sp.long_strike < sp.short_strike            # long further OTM
    assert abs(sp.max_profit - sp.net) < 1e-6
    assert abs(sp.max_loss - (sp.width - sp.net)) < 1e-6
    assert abs(sp.breakeven - (sp.short_strike - sp.net)) < 1e-6
    assert 0 < sp.net < sp.width                        # credit < width
    assert sp.credit_pct_of_width is not None


def test_bear_call_credit_arithmetic():
    sp = build_spread(100, 0.45, 10, choose(-1, "high"))
    assert sp.kind == "credit" and sp.option_type == "call"
    assert sp.long_strike > sp.short_strike
    assert abs(sp.breakeven - (sp.short_strike + sp.net)) < 1e-6


def test_max_profit_loss_are_positive_and_bounded():
    for d, reg in [(+1, "low"), (-1, "low"), (+1, "high"), (-1, "high")]:
        sp = build_spread(150, 0.35, 7, choose(d, reg))
        assert sp.max_profit > 0 and sp.max_loss > 0
        assert sp.max_profit + sp.max_loss - sp.width < 1e-6  # P+L = width (verticals)


def test_expected_move_scales_with_iv_and_dte():
    lo = build_spread(100, 0.20, 7, choose(+1, "low")).expected_move
    hi = build_spread(100, 0.50, 7, choose(+1, "low")).expected_move
    assert hi > lo
    short_dte = build_spread(100, 0.30, 2, choose(+1, "low")).expected_move
    long_dte = build_spread(100, 0.30, 14, choose(+1, "low")).expected_move
    assert long_dte > short_dte


# --- DTE mapping (§4) ---
def test_dte_mapping():
    assert dte_band("1d") == (0, 2)
    assert dte_band("5d") == (7, 21)
    assert bucket_for_timeframe("2h") == "1d"
    assert representative_dte("1d") >= 1  # floored at 1 (no 0-DTE in BS)
