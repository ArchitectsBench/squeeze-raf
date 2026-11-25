"""Event backtest engine for the underlying signal (Phase P4).

Contract (BUILD_SPEC.md §5 Stage 2; CLAUDE.md rules 1, 2):
- Next-bar-open execution; signal on prior close. No same-bar peeking (T5).
- Costs ALWAYS on (commission + slippage). No frictionless runs in reported results.
- Vol-target sizing; long and short.
"""

from .costs import per_side_fraction, round_trip_fraction
from .engine import (
    BARS_PER_YEAR,
    ExitParams,
    SizingParams,
    metrics,
    run_backtest,
    simulate_symbol,
)
from .sizing import vol_target_fraction

__all__ = [
    "ExitParams",
    "SizingParams",
    "simulate_symbol",
    "run_backtest",
    "metrics",
    "BARS_PER_YEAR",
    "per_side_fraction",
    "round_trip_fraction",
    "vol_target_fraction",
]
