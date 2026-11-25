"""Volatility-target position sizing (D10; BUILD_SPEC.md §3.1).

Size each trade so it risks a FIXED fraction of equity at its ATR stop — not a
fixed share count. This normalizes risk across the universe (a quiet large cap and
UVXY contribute comparable risk per trade), which is what stabilizes Sharpe.

exposure_fraction = risk_pct / (atr_stop_mult * ATR / entry_price)
                  = risk_pct * entry_price / (atr_stop_mult * ATR)

capped at `max_leverage` so a tiny ATR can't imply absurd notional.
"""

from __future__ import annotations


def vol_target_fraction(
    entry_price: float,
    atr: float,
    risk_pct: float,
    atr_stop_mult: float,
    max_leverage: float = 1.0,
) -> float:
    """Fraction of equity to deploy so the move to the ATR stop loses ~risk_pct.

    Returns 0 if ATR is non-positive/unknown (no size rather than a wild guess).
    """
    if atr is None or atr <= 0 or entry_price <= 0:
        return 0.0
    stop_distance_frac = (atr_stop_mult * atr) / entry_price
    if stop_distance_frac <= 0:
        return 0.0
    exposure = risk_pct / stop_distance_frac
    return float(min(exposure, max_leverage))
