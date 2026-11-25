"""Indicator ports: TTM Squeeze Pro variant + RAF (Ready-Aim-Fire).

Contract (see BUILD_SPEC.md §2 and the traps in §7; Phase P2):
- Ports of squeezeultra.ts and RAF.ts from ThinkScript.
- No indicator is used downstream until its output matches the ToS export
  fixture within tolerance (Stage 0 fidelity gate). Synthetic + property tests
  guard the traps now; the fixture harness locks numeric fidelity when exports
  are supplied (tests/fixtures/tos/).
- See indicators/CLAUDE.md for T1-T4, T7 traps that must not be "tidied".
"""

from . import raf, squeeze, ta

__all__ = ["squeeze", "raf", "ta"]
