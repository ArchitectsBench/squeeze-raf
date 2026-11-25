"""Optimization layer: coarse grid -> Optuna, with walk-forward (Phase P5).

Contract (BUILD_SPEC.md §5 Stage 3; CLAUDE.md rules 3, 4):
- Only optimize a rule that already PASSED the Stage-1 edge gate.
- Every optimization is walk-forward; report WFA efficiency.
- Enforce >=30 trades per free parameter; prefer broad plateaus over sharp peaks.
- The OOS/holdout window stays LOCKED (gates.yaml holdout) — do not read it here.
"""

from .common import SignalCache, ann_factor, evaluate, oos_months, sharpe
from .grid import GRID, run_grid
from .walkforward import anchored_folds, run_wfa

__all__ = [
    "SignalCache", "evaluate", "sharpe", "ann_factor", "oos_months",
    "GRID", "run_grid", "anchored_folds", "run_wfa",
]
