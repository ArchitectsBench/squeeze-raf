"""Debit-vs-credit structure selection (BUILD_SPEC.md §6 decision matrix).

Direction comes from the price signal; STRUCTURE comes from the IV regime (D4):

    Bullish + low IV-rank  -> Bull call DEBIT   | Bullish + high IV-rank -> Bull put CREDIT
    Bearish + low IV-rank  -> Bear put  DEBIT   | Bearish + high IV-rank -> Bear call CREDIT

Synergy: a tight squeeze often coincides with depressed IV -> the prime DEBIT
setup is "loaded squeeze + low IV-rank + directional confluence" (buy cheap premium
right before the expansion the squeeze predicts). The 'mid' regime defaults to the
debit play (lean into the expansion thesis) and is flagged discretionary.
"""

from __future__ import annotations

from dataclasses import dataclass

# structure codes
BULL_CALL_DEBIT = "bull_call_debit"
BULL_PUT_CREDIT = "bull_put_credit"
BEAR_PUT_DEBIT = "bear_put_debit"
BEAR_CALL_CREDIT = "bear_call_credit"


@dataclass(frozen=True)
class Structure:
    code: str
    kind: str         # "debit" | "credit"
    option_type: str  # "call" | "put" (the legs' option type)
    bullish: bool
    discretionary: bool = False  # True for the 'mid' IV regime


def choose(direction: int, iv_regime: str) -> Structure:
    """direction: +1 bullish, -1 bearish. iv_regime: low/mid/high."""
    bullish = direction > 0
    credit = iv_regime == "high"      # high IV -> sell premium; low/mid -> buy premium
    discretionary = iv_regime == "mid"
    if bullish and not credit:
        return Structure(BULL_CALL_DEBIT, "debit", "call", True, discretionary)
    if bullish and credit:
        return Structure(BULL_PUT_CREDIT, "credit", "put", True, discretionary)
    if not bullish and not credit:
        return Structure(BEAR_PUT_DEBIT, "debit", "put", False, discretionary)
    return Structure(BEAR_CALL_CREDIT, "credit", "call", False, discretionary)
