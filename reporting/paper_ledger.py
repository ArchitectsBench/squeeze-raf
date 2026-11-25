"""Local ledger of OUR Tradier paper order IDs.

SHARED-ACCOUNT SAFETY: this project must only ever read/track the orders IT placed
— never other participants' orders, and never cancel/modify anything. The account
positions endpoint commingles everyone's holdings, so it is NOT used for
attribution. Instead we record every order we submit here (by id) and the tracker
reads ONLY these ids. Lives under data/ (gitignored).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "data" / "paper_orders.jsonl"


def record(entry: dict) -> None:
    """Append + fsync: this file is the ONLY attribution of our orders in the shared
    account — a record lost to a crash between broker POST and flush is an orphan
    position nothing will ever stop or exit."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())


def load() -> list[dict]:
    if not LEDGER.is_file():
        return []
    out = []
    for line in LEDGER.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def order_ids() -> set:
    return {str(e["order_id"]) for e in load() if e.get("order_id")}


def entries() -> list[dict]:
    """Opening orders we placed (kind defaults to 'entry')."""
    return [e for e in load() if e.get("kind", "entry") == "entry"]


def child(parent_id, kind) -> dict | None:
    """The 'stop' or 'exit' record linked to a parent entry order, if any."""
    for e in load():
        if e.get("kind") == kind and str(e.get("parent")) == str(parent_id):
            return e
    return None
