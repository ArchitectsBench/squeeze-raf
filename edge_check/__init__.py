"""Stage-1 edge-check: event study + pre-registered gate (BUILD_SPEC.md §5; Phase P3).

Contract:
- Prove raw edge on Confluence v0 with DEFAULT params BEFORE any optimization
  (CLAUDE.md rule 3). Fail -> discard/mutate the rule; never optimize one unvalidated.
"""

from .event_study import PRIMARY_HORIZON_BARS, collect_entries, run_study
from .gates import GateVerdict, evaluate, load_edge_gate

__all__ = [
    "PRIMARY_HORIZON_BARS",
    "collect_entries",
    "run_study",
    "GateVerdict",
    "evaluate",
    "load_edge_gate",
]
