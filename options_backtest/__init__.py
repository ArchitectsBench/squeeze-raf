"""Options backtest: reconstruct spread P&L on real IV data (Phase P8).

Contract (BUILD_SPEC.md §5 Stage 5; CLAUDE.md rule 9; D9):
- Validate the spread layer SEPARATELY from the underlying signal.
- ORATS SMV Strikes historical (S3), filtered+cached per day to the universe.
- Reconstruct real entry/exit spread P&L -> realized spread Sharpe vs underlying Sharpe.
"""

from .orats_adapter import OratsAdapter
from .spread_pnl import SpreadTrade, reconstruct, spread_metrics

__all__ = ["OratsAdapter", "reconstruct", "spread_metrics", "SpreadTrade"]
