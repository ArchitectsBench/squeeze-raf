"""A position closed by its resting stop / OCO bracket must be detected as CLOSED.

Regression for the CHTR 2026-06-29 incident: the short's OCO stop leg filled at the
broker, but both exit_manager._exited and track._exit_fill scanned only kind='exit'
ledger records — the position showed OPEN, and the 21d time-exit would have
market-closed it a second time, opening a naked opposite position.
"""

from __future__ import annotations

import reporting.broker_state as bs

# abridged from the real sandbox OCO order 33489649 (CHTR bracket)
CHTR_OCO = {
    "id": 33489649, "status": "filled", "class": "oco",
    "leg": [
        {"id": 33489650, "type": "limit", "side": "buy_to_cover", "status": "canceled",
         "avg_fill_price": 0.0, "exec_quantity": 0.0},
        {"id": 33489651, "type": "stop", "side": "buy_to_cover", "status": "filled",
         "avg_fill_price": 163.52, "exec_quantity": 7.0, "stop_price": 155.5},
    ],
}

PLAIN_FILLED = {"id": 1, "status": "filled", "class": "equity", "avg_fill_price": 50.25}
PLAIN_OPEN = {"id": 2, "status": "open", "class": "equity", "avg_fill_price": 0.0}
PLAIN_REJECTED = {"id": 3, "status": "rejected", "class": "equity", "avg_fill_price": 0.0}
# multileg (vertical spread) exits carry the NET price at the top level — must NOT
# switch to per-leg prices for these
MULTILEG_FILLED = {
    "id": 4, "status": "filled", "class": "multileg", "avg_fill_price": -5.51,
    "leg": [
        {"id": 5, "status": "filled", "avg_fill_price": 7.95},
        {"id": 6, "status": "filled", "avg_fill_price": 2.44},
    ],
}


class FakeTradier:
    def __init__(self, orders):
        self.orders = {str(k): v for k, v in orders.items()}

    def _get(self, path, params=None):
        oid = path.rsplit("/", 1)[-1]
        return {"order": self.orders.get(oid, {"status": "unknown"})}


def test_order_fill_plain():
    assert bs.order_fill(PLAIN_FILLED) == (True, 50.25)
    assert bs.order_fill(PLAIN_OPEN) == (False, None)
    assert bs.order_fill(PLAIN_REJECTED) == (False, None)


def test_order_fill_oco_takes_filled_leg_price():
    filled, px = bs.order_fill(CHTR_OCO)
    assert filled is True
    assert px == 163.52


def test_order_fill_multileg_keeps_top_level_net():
    filled, px = bs.order_fill(MULTILEG_FILLED)
    assert filled is True
    assert px == -5.51


def test_filled_oco_stop_counts_as_closed(monkeypatch):
    """The CHTR case: only a 'stop' child exists (no 'exit' record) and its OCO filled."""
    ledger = [
        {"kind": "entry", "order_id": 33487233, "symbol": "CHTR", "source": "momentum_ls"},
        {"kind": "stop", "order_id": 33489649, "parent": 33487233, "symbol": "CHTR",
         "bracket": "oco", "tp": 97.19, "sl": 155.5},
    ]
    monkeypatch.setattr(bs, "load", lambda: ledger)
    tr = FakeTradier({33489649: CHTR_OCO})
    assert bs.position_closed(tr, "ACCT", 33487233) is True
    assert bs.closing_fill(tr, "ACCT", 33487233) == 163.52


def test_open_stop_does_not_count_as_closed(monkeypatch):
    ledger = [
        {"kind": "entry", "order_id": 10, "symbol": "ZTS"},
        {"kind": "stop", "order_id": 11, "parent": 10, "symbol": "ZTS", "bracket": "oco"},
    ]
    monkeypatch.setattr(bs, "load", lambda: ledger)
    tr = FakeTradier({11: {"id": 11, "status": "open", "class": "oco",
                           "leg": [{"status": "open"}, {"status": "open"}]}})
    assert bs.position_closed(tr, "ACCT", 10) is False
    assert bs.closing_fill(tr, "ACCT", 10) is None


def test_rejected_exit_then_filled_exit_scans_all(monkeypatch):
    """Preserve the earlier fix: an early REJECTED exit must not mask a later filled one."""
    ledger = [
        {"kind": "entry", "order_id": 20, "symbol": "V"},
        {"kind": "exit", "order_id": 21, "parent": 20, "symbol": "V"},
        {"kind": "exit", "order_id": 22, "parent": 20, "symbol": "V"},
    ]
    monkeypatch.setattr(bs, "load", lambda: ledger)
    tr = FakeTradier({21: PLAIN_REJECTED, 22: PLAIN_FILLED})
    assert bs.position_closed(tr, "ACCT", 20) is True
    assert bs.closing_fill(tr, "ACCT", 20) == 50.25


def test_exit_manager_and_track_use_shared_detection():
    """Both consumers must route through broker_state, or the CHTR bug reappears."""
    from reporting import exit_manager, track
    assert exit_manager.position_closed is bs.position_closed
    assert track.closing_fill is bs.closing_fill
