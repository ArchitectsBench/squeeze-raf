"""Single-leg LONG option P&L reconstruction (naked call/put, defined risk).

The user's chosen product: a bullish signal buys a CALL, a bearish signal buys a
PUT — max loss = premium, upside uncapped (convex). This is the right structure for
a signal whose payoff is a few big moves among many small ones (where the linear
forward-return event study and the capped vertical both understate the edge).

Fills: buy at ask to open, sell at bid to close (real ORATS quotes); commission per
leg-transaction from config. Return is on premium-at-risk so trades are comparable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .spread_pnl import _commission_per_share, _leg_prices, _naive, _pick, _select_expiry


@dataclass
class LongOptionTrade:
    symbol: str
    kind: str            # call/put
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    expiry: pd.Timestamp
    strike: float
    entry_premium: float
    exit_premium: float
    pnl: float           # per-share, after commission
    return_on_premium: float


def reconstruct_long(adapter, symbol, entry_date, exit_date, direction, dte_lo, dte_hi,
                     target_delta: float = 0.50) -> LongOptionTrade | None:
    entry_date, exit_date = _naive(entry_date), _naive(exit_date)
    kind = "call" if direction > 0 else "put"
    ec = adapter.chain(symbol, entry_date)
    xc = adapter.chain(symbol, exit_date)
    if ec is None or xc is None:
        return None
    expiry = _select_expiry(ec, entry_date, exit_date, dte_lo, dte_hi)
    if expiry is None:
        return None
    erows = ec[ec["expiry"] == expiry]
    xrows = xc[xc["expiry"] == expiry]
    if erows.empty or xrows.empty:
        return None

    row = _pick(erows, target_delta, kind)
    strike = float(row["strike"])
    xr = xrows[xrows["strike"] == strike]
    if xr.empty:
        return None

    _, entry_ask = _leg_prices(row, kind)
    exit_bid, _ = _leg_prices(xr.iloc[0], kind)
    comm = _commission_per_share() * 2  # open + close
    if entry_ask <= 0:
        return None
    pnl = exit_bid - entry_ask - comm
    return LongOptionTrade(
        symbol=symbol, kind=kind, entry_date=entry_date, exit_date=exit_date,
        expiry=expiry, strike=strike, entry_premium=round(entry_ask, 3),
        exit_premium=round(exit_bid, 3), pnl=round(pnl, 4),
        return_on_premium=round(pnl / entry_ask, 4),
    )


def long_option_metrics(trades: list[LongOptionTrade], trades_per_year: float) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t.return_on_premium for t in trades])
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    pt_sharpe = r.mean() / sd if sd > 0 else 0.0
    return {
        "n": len(trades),
        "win_rate": float((r > 0).mean()),
        "mean_return_on_premium": float(r.mean()),
        "median_return": float(np.median(r)),
        "per_trade_sharpe": float(pt_sharpe),
        "ann_sharpe": float(pt_sharpe * np.sqrt(trades_per_year)),
        "best": float(r.max()), "worst": float(r.min()),
        "profit_factor": float(r[r > 0].sum() / -r[r < 0].sum()) if (r < 0).any() else np.inf,
    }
