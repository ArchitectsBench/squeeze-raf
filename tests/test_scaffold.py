"""Scaffold smoke test: every stub package imports and pytest collects.

This is a placeholder so `uv run pytest` collects from a clean scaffold. Real test-first
work begins in P2 (indicator fidelity) and P4 (look-ahead) per CLAUDE.md / BUILD_SPEC.md §10.
"""

import importlib

import pytest

STUB_MODULES = [
    "data",
    "data.cache",
    "data.polygon_adapter",
    "data.alpha_vantage_adapter",
    "indicators",
    "indicators.squeeze",
    "indicators.raf",
    "signals",
    "signals.confluence",
    "signals.scoring",
    "edge_check",
    "edge_check.event_study",
    "edge_check.gates",
    "backtest",
    "backtest.engine",
    "backtest.costs",
    "backtest.sizing",
    "optimize",
    "optimize.grid",
    "optimize.optuna_search",
    "optimize.walkforward",
    "validation",
    "validation.dsr",
    "validation.pbo_cscv",
    "validation.bootstrap",
    "validation.permutation",
    "spreads",
    "spreads.iv_regime",
    "spreads.structure",
    "spreads.strikes",
    "spreads.dte",
    "options_backtest",
    "options_backtest.orats_adapter",
    "options_backtest.theta_adapter",
    "options_backtest.spread_pnl",
    "reporting",
    "reporting.tearsheet",
    "reporting.recommend",
]


@pytest.mark.parametrize("module", STUB_MODULES)
def test_stub_module_imports(module):
    """Each scaffolded module imports cleanly (docstring-only stub)."""
    assert importlib.import_module(module) is not None
