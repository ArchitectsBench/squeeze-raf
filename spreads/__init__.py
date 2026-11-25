"""Structuring layer: graduated signal -> debit/credit vertical spread (Phase P7).

Contract (BUILD_SPEC.md §6; CLAUDE.md rule 9):
- Direction from the signal; STRUCTURE from the IV regime (D4); strikes from delta +
  expected move; DTE from the holding-horizon bucket.
- Validated SEPARATELY from the underlying signal (spread Sharpe != signal Sharpe).
- P7 uses a realized-vol IV proxy + Black-Scholes; P8 swaps in real options data.
"""

from . import blackscholes
from .dte import bucket_for_timeframe, dte_band, representative_dte
from .iv_regime import IVRead, iv_read, realized_vol
from .strikes import Spread, build_spread, round_strike
from .structure import Structure, choose

__all__ = [
    "blackscholes",
    "iv_read", "IVRead", "realized_vol",
    "choose", "Structure",
    "build_spread", "Spread", "round_strike",
    "dte_band", "representative_dte", "bucket_for_timeframe",
]
