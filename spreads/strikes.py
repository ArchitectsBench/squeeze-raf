"""Strike selection + spread construction from delta + expected move (§6).

Debit: long leg ~0.45-0.55 delta (near ATM), short leg ~0.25-0.30 delta.
Credit: short leg ~0.25-0.35 delta, long leg wider; width ~ fraction of expected
move (so strikes respect EM, not fight it). Emits both strikes + deltas, width, net
debit/credit, breakeven, max profit/loss, POP and an approximate expectancy.

Pricing/deltas/POP come from Black-Scholes on the IV PROXY (P7); P8 replaces with
real option prices and validates realized P&L.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import blackscholes as bs
from .structure import Structure


def strike_increment(price: float) -> float:
    if price < 25:
        return 0.5
    if price < 100:
        return 1.0
    if price < 250:
        return 2.5
    return 5.0


def round_strike(x: float, ref_price: float) -> float:
    inc = strike_increment(ref_price)
    return round(round(x / inc) * inc, 2)


@dataclass
class Spread:
    structure: str
    kind: str            # debit/credit
    option_type: str     # call/put
    long_strike: float
    short_strike: float
    long_delta: float
    short_delta: float
    width: float
    net: float           # debit (paid, >0) or credit (received, >0)
    max_profit: float
    max_loss: float
    breakeven: float
    pop: float           # probability of profit (BS proxy)
    expectancy: float    # approx: pop*max_profit - (1-pop)*max_loss
    expected_move: float
    em_atr_check: float | None
    credit_pct_of_width: float | None


def build_spread(
    S: float, iv: float, dte_days: int, structure: Structure,
    atr: float | None = None, horizon_bars: float | None = None,
    debit_long_delta: float = 0.50, debit_short_delta: float = 0.27,
    credit_short_delta: float = 0.30, credit_width_em_frac: float = 0.5,
) -> Spread | None:
    T = max(dte_days, 1) / 365.0
    if iv <= 0 or S <= 0:
        return None
    em = S * iv * np.sqrt(T)
    em_atr = (atr * np.sqrt(horizon_bars)) if (atr and horizon_bars) else None
    ot = structure.option_type

    if structure.kind == "debit":
        k_long = round_strike(bs.strike_from_delta(S, debit_long_delta, iv, T, kind=ot), S)
        k_short = round_strike(bs.strike_from_delta(S, debit_short_delta, iv, T, kind=ot), S)
        if k_long == k_short:
            k_short += strike_increment(S) if structure.bullish else -strike_increment(S)
        long_px = bs.price(S, k_long, iv, T, kind=ot)
        short_px = bs.price(S, k_short, iv, T, kind=ot)
        net = long_px - short_px
        width = abs(k_short - k_long)
        max_profit, max_loss = width - net, net
        if structure.bullish:   # bull call
            be = k_long + net
            pop = bs.prob_above(S, be, iv, T)
        else:                   # bear put
            be = k_long - net
            pop = bs.prob_below(S, be, iv, T)
        credit_pct = None
    else:  # credit
        k_short = round_strike(bs.strike_from_delta(S, credit_short_delta, iv, T, kind=ot), S)
        width = max(round_strike(credit_width_em_frac * em, S), strike_increment(S))
        k_long = k_short - width if structure.bullish else k_short + width
        short_px = bs.price(S, k_short, iv, T, kind=ot)
        long_px = bs.price(S, k_long, iv, T, kind=ot)
        net = short_px - long_px
        width = abs(k_short - k_long)
        max_profit, max_loss = net, width - net
        if structure.bullish:   # bull put
            be = k_short - net
            pop = bs.prob_above(S, be, iv, T)
        else:                   # bear call
            be = k_short + net
            pop = bs.prob_below(S, be, iv, T)
        credit_pct = round(net / width, 3) if width > 0 else None

    if net <= 0 or width <= 0:
        return None
    expectancy = pop * max_profit - (1 - pop) * max_loss
    return Spread(
        structure=structure.code, kind=structure.kind, option_type=ot,
        long_strike=k_long, short_strike=k_short,
        long_delta=round(bs.delta(S, k_long, iv, T, kind=ot), 3),
        short_delta=round(bs.delta(S, k_short, iv, T, kind=ot), 3),
        width=round(width, 2), net=round(net, 2),
        max_profit=round(max_profit, 2), max_loss=round(max_loss, 2),
        breakeven=round(be, 2), pop=round(pop, 3), expectancy=round(expectancy, 3),
        expected_move=round(em, 2),
        em_atr_check=round(em_atr, 2) if em_atr is not None else None,
        credit_pct_of_width=credit_pct,
    )
