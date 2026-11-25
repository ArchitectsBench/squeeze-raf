"""P4 look-ahead gate (CLAUDE.md rule 1 / T5).

The binding test: an entry signalled on close[t] must fill at **open[t+1]**, and the
signal bar t must contribute ZERO return (we are not yet in the position). A code
path that fills the same bar's close on bar t would make r[t] != 0 and the entry
price != open[t+1] — these tests fail loudly on that bug.

Plus: costs are always charged, stops cap losses, and shorts work symmetrically.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from backtest.engine import ExitParams, SizingParams, simulate_symbol
from indicators.squeeze import HIST_UP_DEC


def _df(open_, high, low, close):
    n = len(close)
    idx = pd.bdate_range("2022-01-03", periods=n, tz="UTC")
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                       "volume": np.full(n, 1.0)}, index=idx)
    df.index.name = "timestamp"
    return df


def _sig(df, long_idx=(), short_idx=()):
    """Minimal Signals-like object: entries at given bar positions, neutral hist
    (HIST_UP_DEC) so the momentum-exit rule never fires on its own."""
    le = pd.Series(False, index=df.index)
    se = pd.Series(False, index=df.index)
    for i in long_idx:
        le.iloc[i] = True
    for i in short_idx:
        se.iloc[i] = True
    hist_state = pd.Series(HIST_UP_DEC, index=df.index)
    return SimpleNamespace(long_entry=le, short_entry=se,
                           sq=SimpleNamespace(hist_state=hist_state))


NO_MOM = ExitParams(time_stop_bars=100, use_momentum_exit=False)
SIZING = SizingParams(risk_pct=0.01, max_leverage=1.0)


def _ramp(n=40, base=100.0, step=0.5):
    close = base + step * np.arange(n)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    return _df(open_, high, low, close)


def test_entry_fills_next_bar_open_not_signal_bar():
    df = _ramp()
    k = 20
    r, trades = simulate_symbol(df, _sig(df, long_idx=[k]), "equities", NO_MOM, SIZING)
    assert len(trades) == 1
    tr = trades[0]
    assert tr["entry_bar"] == k + 1, "entry must be the bar AFTER the signal"
    assert tr["entry_price"] == df["open"].iloc[k + 1], "fill must be open[t+1]"
    assert r[k] == 0.0, "signal bar must contribute no return (not yet in position)"


def test_signal_bar_return_is_zero_even_with_favorable_gap():
    """A big favorable gap at open[t+1] must NOT leak into bar t. If the engine
    peeked (filled close[t]), r[t] would be non-zero."""
    df = _ramp()
    k = 18
    # inject a large up-gap on the bar AFTER the signal
    df.loc[df.index[k + 1], ["open", "high", "low", "close"]] = [130, 133, 129, 132]
    r, _ = simulate_symbol(df, _sig(df, long_idx=[k]), "equities", NO_MOM, SIZING)
    assert r[k] == 0.0


def test_costs_always_on_roundtrip_is_negative():
    """Enter then immediately exit on a flat market -> strictly negative (2 sides
    of cost). There is no frictionless path."""
    df = _ramp(step=0.0)  # flat closes
    k = 20
    exits = ExitParams(time_stop_bars=1, use_momentum_exit=False)  # exit ~next bar
    r, trades = simulate_symbol(df, _sig(df, long_idx=[k]), "equities", exits, SIZING)
    assert len(trades) == 1
    assert trades[0]["pnl"] < 0, "round-trip on a flat market must lose the costs"


def test_stop_caps_loss_near_risk_budget_when_no_gap():
    """An INTRABAR pierce (open near prior close, low spikes through the stop) fills
    at the stop, so vol-target sizing caps the loss near the risk budget."""
    df = _ramp(n=40)
    k = 20
    j = k + 2
    prior = df["close"].iloc[j - 1]
    # open at prior close (no gap), low pierces the stop intrabar, close recovers a bit
    df.loc[df.index[j], ["open", "high", "low", "close"]] = [prior, prior + 0.1,
                                                             prior - 20, prior - 18]
    r, trades = simulate_symbol(df, _sig(df, long_idx=[k]), "equities", NO_MOM, SIZING)
    assert len(trades) == 1
    assert trades[0]["pnl"] < 0
    # risk_pct=0.01 + costs -> loss bounded well above a catastrophic level
    assert trades[0]["pnl"] > -0.05, "intrabar stop should cap the loss near risk budget"


def test_gap_through_stop_can_exceed_budget():
    """Honest gap risk: a downside GAP fills at the open (below the stop), so the
    loss legitimately exceeds the risk budget. Documents the behavior."""
    df = _ramp(n=40)
    k = 20
    j = k + 2
    df.loc[df.index[j], ["open", "high", "low", "close"]] = [90, 90.5, 70, 72]
    _, trades = simulate_symbol(df, _sig(df, long_idx=[k]), "equities", NO_MOM, SIZING)
    assert trades[0]["pnl"] < -0.05  # gap blew past the stop


def test_short_is_symmetric():
    """A short on a falling market profits."""
    n = 40
    close = 120 - 0.5 * np.arange(n)
    open_ = np.r_[close[0], close[:-1]]
    df = _df(open_, np.maximum(open_, close) + 0.2, np.minimum(open_, close) - 0.2, close)
    k = 20
    r, trades = simulate_symbol(df, _sig(df, short_idx=[k]), "equities", NO_MOM, SIZING)
    assert len(trades) == 1
    assert trades[0]["dir"] == -1
    assert trades[0]["pnl"] > 0, "short on a falling market should profit"


def test_no_entries_no_returns():
    df = _ramp()
    r, trades = simulate_symbol(df, _sig(df), "equities", NO_MOM, SIZING)
    assert len(trades) == 0
    assert np.allclose(r, 0.0)


def test_higher_costs_reduce_pnl():
    """High-vol ETF cost class (wider slippage) must cut PnL vs equities."""
    df = _ramp(step=0.0)
    k = 20
    exits = ExitParams(time_stop_bars=1, use_momentum_exit=False)
    _, eq = simulate_symbol(df, _sig(df, long_idx=[k]), "equities", exits, SIZING)
    _, hv = simulate_symbol(df, _sig(df, long_idx=[k]), "etfs_high_vol", exits, SIZING)
    assert hv[0]["pnl"] < eq[0]["pnl"]
