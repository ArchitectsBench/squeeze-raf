"""Transaction-cost model (BUILD_SPEC.md §5 Stage 2; config/universe.yaml costs).

Costs are NON-OPTIONAL (CLAUDE.md rule 2) — every reported result includes them.
We express cost as a per-side fraction of notional: slippage (bps) + a half-spread
proxy, plus per-share commission converted to bps at the traded price. Leveraged/
high-vol ETFs get the wider slippage class (R9/T7).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

UNIVERSE_PATH = Path(__file__).resolve().parent.parent / "config" / "universe.yaml"


@lru_cache(maxsize=1)
def _costs(path: str = str(UNIVERSE_PATH)) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)["costs"]


def per_side_fraction(cost_class: str, price: float = 100.0) -> float:
    """One-way cost as a fraction of notional for the given cost class.

    slippage_bps/1e4 + (commission_per_share / price). A half-spread is folded
    into the conservative slippage figure already in config.
    """
    costs = _costs()
    block = costs.get(cost_class) or costs["equities"]
    slip = float(block["slippage_bps"]) / 1e4
    commission = float(block.get("commission_per_share", 0.0))
    return slip + (commission / price if price > 0 else 0.0)


def round_trip_fraction(cost_class: str, price: float = 100.0) -> float:
    """Entry + exit cost as a fraction of notional."""
    return 2.0 * per_side_fraction(cost_class, price)
