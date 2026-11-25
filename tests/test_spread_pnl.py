"""P8 unit tests: spread P&L arithmetic on a synthetic ORATS-shaped chain (no network)."""

from __future__ import annotations

import pandas as pd

from options_backtest.spread_pnl import reconstruct, spread_metrics


class FakeAdapter:
    """Returns hand-built chains so the P&L math is checkable by hand."""
    def __init__(self, by_date):
        self._by_date = by_date  # {Timestamp(date): DataFrame}

    def chain(self, ticker, date):
        return self._by_date.get(pd.Timestamp(date))


def _chain(strikes_calls):
    """strikes_calls: list of (strike, delta, cBid, cAsk). Puts mirrored cheaply."""
    rows = []
    for k, d, cb, ca in strikes_calls:
        rows.append({"ticker": "TST", "strike": k, "delta": d,
                     "cBidPx": cb, "cValue": (cb + ca) / 2, "cAskPx": ca,
                     "pBidPx": 1.0, "pValue": 1.1, "pAskPx": 1.2,
                     "expiry": pd.Timestamp("2024-01-19")})
    return pd.DataFrame(rows)


def test_bull_call_debit_pnl_hand_math():
    # entry: long 100c (δ0.50) ask 3.00, short 105c (δ0.27) bid 1.00 -> debit 2.00
    entry = _chain([(100, 0.50, 2.90, 3.00), (105, 0.27, 1.00, 1.10)])
    # exit: underlying rallied -> long 100c bid 4.50, short 105c ask 2.00 -> close 2.50
    exit_ = _chain([(100, 0.50, 4.50, 4.60), (105, 0.27, 1.90, 2.00)])
    adapter = FakeAdapter({pd.Timestamp("2024-01-05"): entry,
                           pd.Timestamp("2024-01-08"): exit_})
    st = reconstruct(adapter, "TST", "2024-01-05", "2024-01-08", +1, "low", 0, 21)
    assert st is not None
    assert st.long_strike == 100 and st.short_strike == 105
    # debit = 3.00 - 1.00 = 2.00 ; close = 4.50 - 2.00 = 2.50 ; gross pnl = +0.50 (minus comm)
    assert abs(st.entry_net - 2.00) < 1e-6
    assert abs(st.exit_net - 2.50) < 1e-6
    assert 0.45 < st.pnl < 0.50          # +0.50 minus small commission
    assert st.return_on_risk > 0


def test_debit_loss_when_spread_collapses():
    entry = _chain([(100, 0.50, 2.90, 3.00), (105, 0.27, 1.00, 1.10)])
    exit_ = _chain([(100, 0.50, 0.40, 0.50), (105, 0.27, 0.05, 0.10)])  # theta wipeout
    adapter = FakeAdapter({pd.Timestamp("2024-01-05"): entry,
                           pd.Timestamp("2024-01-08"): exit_})
    st = reconstruct(adapter, "TST", "2024-01-05", "2024-01-08", +1, "low", 0, 21)
    assert st.pnl < 0 and st.return_on_risk < 0


def test_spread_metrics_shapes():
    entry = _chain([(100, 0.50, 2.90, 3.00), (105, 0.27, 1.00, 1.10)])
    win = _chain([(100, 0.50, 4.50, 4.60), (105, 0.27, 1.90, 2.00)])
    adapter = FakeAdapter({pd.Timestamp("2024-01-05"): entry,
                           pd.Timestamp("2024-01-08"): win})
    trades = [reconstruct(adapter, "TST", "2024-01-05", "2024-01-08", +1, "low", 0, 21)]
    m = spread_metrics(trades, trades_per_year=250)
    assert m["n"] == 1 and m["pop"] == 1.0 and m["ann_sharpe"] == m["ann_sharpe"]
