"""Spread P&L reconstruction + realized spread metrics (BUILD_SPEC.md §5 Stage 5).

For each graduated-signal trade, reconstruct the §6 vertical spread from REAL ORATS
chains: pick the expiry (closeable before expiration), select strikes by real delta,
fill at real bid/ask (buy ask / sell bid to open, the reverse to close), and net out
commission. Per-trade return is P&L on capital-at-risk so debit and credit spreads
are comparable. We use bid/ask as the slippage (no extra slippage_per_leg, to avoid
double counting); commission = options.per_contract from config.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from spreads.structure import choose

UNIVERSE = Path(__file__).resolve().parent.parent / "config" / "universe.yaml"

# target deltas (magnitudes) for leg selection
DEBIT_LONG, DEBIT_SHORT = 0.50, 0.27
CREDIT_SHORT, CREDIT_LONG = 0.30, 0.15


def _commission_per_share() -> float:
    with open(UNIVERSE) as f:
        opt = yaml.safe_load(f)["costs"]["options"]
    return float(opt["per_contract"]) / 100.0  # per contract (100 sh) -> per share


def _pick(rows: pd.DataFrame, target_mag: float, kind: str) -> pd.Series:
    """Strike row whose option delta magnitude is nearest target. `delta` col is the
    CALL delta; put delta magnitude = 1 - call_delta."""
    ctarget = target_mag if kind == "call" else (1.0 - target_mag)
    return rows.iloc[(rows["delta"] - ctarget).abs().argmin()]


def _leg_prices(row: pd.Series, kind: str) -> tuple[float, float]:
    """(bid, ask) for the requested option type on a strike row."""
    if kind == "call":
        return float(row["cBidPx"]), float(row["cAskPx"])
    return float(row["pBidPx"]), float(row["pAskPx"])


def _select_expiry(chain: pd.DataFrame, entry_date, exit_date, dte_lo, dte_hi):
    """Nearest expiry that is still alive at exit (>= exit_date) and within/just past
    the target DTE band, so the spread can always be closed at market."""
    exps = pd.Series(sorted(chain["expiry"].unique()))
    exps = exps[exps >= pd.Timestamp(exit_date)]
    if exps.empty:
        return None
    dte = (exps - pd.Timestamp(entry_date)).dt.days
    # prefer the smallest dte that is >= max(dte_lo,1); else the nearest available
    ok = exps[dte >= max(dte_lo, 1)]
    return (ok.iloc[0] if len(ok) else exps.iloc[0])


@dataclass
class SpreadTrade:
    symbol: str
    structure: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    expiry: pd.Timestamp
    long_strike: float
    short_strike: float
    width: float
    entry_net: float      # debit paid (>0) or credit received (>0)
    exit_net: float       # to close: debit->received, credit->paid
    pnl: float            # per-share, after commission
    max_loss: float
    return_on_risk: float


def _naive(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize(None) if ts.tz is not None else ts


def reconstruct(adapter, symbol, entry_date, exit_date, direction, regime,
                dte_lo, dte_hi) -> SpreadTrade | None:
    # ORATS expiry dates are tz-naive; coerce trade dates to match for comparisons
    entry_date, exit_date = _naive(entry_date), _naive(exit_date)
    s = choose(direction, regime)
    ot = s.option_type
    ec = adapter.chain(symbol, entry_date)
    xc = adapter.chain(symbol, exit_date)
    if ec is None or xc is None:
        return None
    expiry = _select_expiry(ec, entry_date, exit_date, dte_lo, dte_hi)
    if expiry is None:
        return None
    erows = ec[ec["expiry"] == expiry]
    xrows = xc[xc["expiry"] == expiry]
    if len(erows) < 2 or xrows.empty:
        return None

    if s.kind == "debit":
        long_row = _pick(erows, DEBIT_LONG, ot)
        short_row = _pick(erows, DEBIT_SHORT, ot)
    else:
        short_row = _pick(erows, CREDIT_SHORT, ot)
        long_row = _pick(erows, CREDIT_LONG, ot)
    k_long, k_short = float(long_row["strike"]), float(short_row["strike"])
    if k_long == k_short:
        return None

    # same legs in the exit chain (exact strike match required)
    def exit_row(k):
        r = xrows[xrows["strike"] == k]
        return r.iloc[0] if len(r) else None
    xl, xs = exit_row(k_long), exit_row(k_short)
    if xl is None or xs is None:
        return None

    long_bid_e, long_ask_e = _leg_prices(long_row, ot)
    short_bid_e, short_ask_e = _leg_prices(short_row, ot)
    long_bid_x, long_ask_x = _leg_prices(xl, ot)
    short_bid_x, short_ask_x = _leg_prices(xs, ot)
    comm = _commission_per_share() * 4  # 2 legs x (open+close)
    width = abs(k_short - k_long)

    if s.kind == "debit":
        entry_net = long_ask_e - short_bid_e      # pay
        exit_net = long_bid_x - short_ask_x       # receive
        pnl = exit_net - entry_net - comm
        max_loss = entry_net + comm
    else:
        entry_net = short_bid_e - long_ask_e      # receive
        exit_net = short_ask_x - long_bid_x       # pay
        pnl = entry_net - exit_net - comm
        max_loss = (width - entry_net) + comm
    if entry_net <= 0 or max_loss <= 0:
        return None

    return SpreadTrade(
        symbol=symbol, structure=s.code,
        entry_date=pd.Timestamp(entry_date), exit_date=pd.Timestamp(exit_date),
        expiry=expiry, long_strike=k_long, short_strike=k_short, width=round(width, 2),
        entry_net=round(entry_net, 3), exit_net=round(exit_net, 3),
        pnl=round(pnl, 4), max_loss=round(max_loss, 3),
        return_on_risk=round(pnl / max_loss, 4),
    )


def spread_metrics(trades: list[SpreadTrade], trades_per_year: float) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t.return_on_risk for t in trades])
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    per_trade_sharpe = r.mean() / sd if sd > 0 else 0.0
    return {
        "n": len(trades),
        "pop": float((r > 0).mean()),                       # realized prob of profit
        "mean_return_on_risk": float(r.mean()),
        "expectancy": float(r.mean()),                      # per-trade EV on risk
        "per_trade_sharpe": float(per_trade_sharpe),
        "ann_sharpe": float(per_trade_sharpe * np.sqrt(trades_per_year)),
        "best": float(r.max()), "worst": float(r.min()),
    }
