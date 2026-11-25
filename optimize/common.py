"""Shared optimization machinery (P5): research/holdout split, signal memoization,
and a single `evaluate(params)` used by the grid, Optuna, and walk-forward.

THE OOS WINDOW IS LOCKED (gates.yaml holdout.oos_window_months). Everything here
operates on the *research* range only — the final 6 months are never evaluated
until P6. Signals are computed on the full causal series (only past bars feed any
bar), then returns are sliced to the research range, so warmup is preserved with
no look-ahead and no holdout leakage.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from backtest.engine import BARS_PER_YEAR, ExitParams, simulate_symbol
from signals import ConfluenceParams, generate

GATES_PATH = Path(__file__).resolve().parent.parent / "config" / "gates.yaml"


def oos_months(path: Path | str = GATES_PATH) -> int:
    with open(path) as f:
        return int(yaml.safe_load(f)["holdout"]["oos_window_months"])


def research_end(index: pd.DatetimeIndex, months: int) -> pd.Timestamp:
    """First timestamp of the locked holdout = last_ts - months. Research range is
    strictly before this."""
    return index.max() - pd.DateOffset(months=months)


def sharpe(returns: pd.Series, ann: int) -> float:
    r = returns.dropna()
    if len(r) < 5 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * np.sqrt(ann))


@dataclass
class SignalCache:
    """Memoizes Confluence signal generation per (symbol, entry-param tuple) so the
    grid/Optuna don't recompute the (expensive) RAF/squeeze features for every exit
    variation.

    If `align_frames` (higher-TF frames) and `conviction_min` are given, generates
    Confluence v1 (alignment + conviction filter) instead of base Mode A. The 4h
    alignment is precomputed once per symbol (it doesn't depend on the 2h entry
    params)."""
    frames: dict[str, pd.DataFrame]
    timeframe: str
    align_frames: dict[str, pd.DataFrame] | None = None
    conviction_min: float | None = None
    _cache: dict = None
    _align: dict = None

    def __post_init__(self):
        self._cache = {}
        self._align = {}
        if self.align_frames is not None:
            from signals import align_regime
            for sym, df in self.frames.items():
                if sym in self.align_frames:
                    self._align[sym] = align_regime(self.align_frames[sym], df.index)
                else:
                    f = pd.Series(False, index=df.index)
                    self._align[sym] = (f, f)

    def get(self, symbol: str, length: int, confluence_window: int, sc_ready: int,
            energy_lookback: int):
        key = (symbol, length, confluence_window, sc_ready, energy_lookback)
        if key not in self._cache:
            params = ConfluenceParams(length=length, confluence_window=confluence_window,
                                      sc_ready=sc_ready, energy_lookback=energy_lookback)
            if self.align_frames is not None:
                from signals import generate_v1
                al, ash = self._align[symbol]
                self._cache[key] = generate_v1(self.frames[symbol], params,
                                               self.conviction_min, al, ash)
            else:
                self._cache[key] = generate(self.frames[symbol], params, mode="A")
        return self._cache[key]


def evaluate(
    cache: SignalCache,
    cost_class: dict[str, str],
    params: dict,
    research_cut: pd.Timestamp,
    window: str = "research",
) -> tuple[pd.Series, int]:
    """Run Mode A with `params` across the universe; return (portfolio per-bar
    returns over `window`, n_trades entered in `window`).

    window: 'research' (index < cut — DEFAULT, OOS-safe), 'holdout' (index >= cut,
    the locked OOS), or 'full'. Signals are computed on the full causal series
    regardless, so warmup is preserved and there's no look-ahead either way.
    """
    tf = cache.timeframe
    length = 21 if tf == "1d" else 13
    exits = ExitParams(time_stop_bars=int(params["time_stop_bars"]),
                       atr_stop_mult=float(params["atr_stop_mult"]),
                       profit_target_R=float(params["profit_target_R"]),
                       use_momentum_exit=bool(params.get("use_momentum_exit", True)),
                       use_profit_target=bool(params.get("use_profit_target", True)))

    def in_window(ts):
        if window == "research":
            return ts < research_cut
        if window == "holdout":
            return ts >= research_cut
        return True

    per_symbol = {}
    n_trades = 0
    for sym, df in cache.frames.items():
        sig = cache.get(sym, length, int(params["confluence_window"]),
                        int(params["sc_ready"]), int(params["energy_lookback"]))
        r, trades = simulate_symbol(df, sig, cost_class.get(sym, "equities"), exits,
                                    _SIZING)
        s = pd.Series(r, index=df.index)
        mask = s.index < research_cut if window == "research" else (
            s.index >= research_cut if window == "holdout" else s.index == s.index)
        per_symbol[sym] = s[mask]
        n_trades += sum(1 for t in trades if in_window(df.index[t["entry_bar"]]))
    mat = pd.DataFrame(per_symbol).sort_index()
    return mat.mean(axis=1).fillna(0.0), n_trades


# sizing held fixed during P5 (risk budget is a policy choice, not a fit param)
from backtest.engine import SizingParams  # noqa: E402

_SIZING = SizingParams()


def ann_factor(tf: str) -> int:
    return BARS_PER_YEAR.get(tf, 252)
