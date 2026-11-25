"""Signal layer: Confluence v0 rule (squeeze × RAF) + conviction scoring.

Contract (BUILD_SPEC.md §3; Phase P3):
- Combine squeeze and RAF outputs into long/short entry signals (modes A/B/C).
- Signal computed on close[t]; execution is next-bar open (enforced downstream) —
  no same-bar peeking (T5).
- Long-and-short symmetric (D3).
"""

from .confluence import (
    MODES,
    ConfluenceParams,
    Signals,
    align_regime,
    generate,
    generate_squeeze,
    generate_v1,
    weekly_frame,
    weekly_regime,
)
from .scoring import directional_conviction

__all__ = [
    "MODES", "ConfluenceParams", "Signals", "generate", "generate_v1",
    "generate_squeeze", "align_regime", "weekly_regime", "weekly_frame",
    "directional_conviction",
]
