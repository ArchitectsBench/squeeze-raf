"""Broker-truth close detection for OUR positions (shared-account safe).

A position we opened can be closed by EITHER a recorded 'exit' order (time/signal
close) OR a recorded 'stop' child that filled at the broker (resting ATR stop or an
OCO bracket leg). Both must count everywhere: scanning only 'exit' records left the
CHTR 2026-06-29 OCO stop-out invisible — the tracker showed it open and the 21d
time-exit would have market-closed it AGAIN, opening a naked opposite position.

Reads only order ids from our ledger; never lists account orders or positions.
"""

from __future__ import annotations

from .paper_ledger import load

CLOSING_KINDS = ("exit", "stop")


def order_fill(o: dict) -> tuple[bool, float | None]:
    """(filled, fill_price) for a broker order dict.

    OCO wrappers carry no top-level price — take the FILLED leg's avg_fill_price.
    Everything else (equity, option, multileg spread) reports its fill — for spreads
    the NET price — at the top level, so leave that untouched.
    """
    if o.get("status") != "filled":
        return False, None
    if o.get("class") == "oco":
        legs = o.get("leg") or []
        legs = [legs] if isinstance(legs, dict) else legs
        for leg in legs:
            if leg.get("status") == "filled" and float(leg.get("avg_fill_price") or 0) > 0:
                return True, float(leg["avg_fill_price"])
        return True, None
    return True, float(o.get("avg_fill_price") or 0.0)


def closing_fill(tr, account, parent_id) -> float | None:
    """Fill price of whichever recorded closing order (exit OR stop) actually FILLED,
    or None while the position is still open. A position may carry several closing
    records (e.g. an early REJECTED exit + a later filled one) — scan them all."""
    for r in load():
        if (r.get("kind") in CLOSING_KINDS
                and str(r.get("parent")) == str(parent_id) and r.get("order_id")):
            try:
                o = tr._get(f"/v1/accounts/{account}/orders/{r['order_id']}").get("order", {})
            except Exception:
                continue
            filled, px = order_fill(o)
            if filled:
                return px if px is not None else 0.0
    return None


def position_closed(tr, account, parent_id) -> bool:
    return closing_fill(tr, account, parent_id) is not None
