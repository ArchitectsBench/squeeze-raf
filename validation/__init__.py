"""Overfitting gauntlet — the graduation battery (Phase P6).

Contract (BUILD_SPEC.md §5 Stage 4; CLAUDE.md rule 5; gates.yaml graduation):
- A graduate must pass ALL: Deflated Sharpe (trial-count corrected) + PBO/CSCV < 0.2 +
  bootstrap MC (5th-pct acceptable, median >= 1.2) + permutation + neighborhood + subperiod.
- This is the ONLY stage allowed to read the locked OOS holdout.
"""

from .bootstrap import block_bootstrap_sharpe
from .dsr import deflated_sharpe_ratio
from .pbo_cscv import pbo_cscv
from .permutation import permutation_test

__all__ = [
    "deflated_sharpe_ratio",
    "pbo_cscv",
    "block_bootstrap_sharpe",
    "permutation_test",
]
