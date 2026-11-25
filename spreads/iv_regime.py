"""IV regime read: IV-rank / IV-percentile + regime (BUILD_SPEC.md §6; D4).

NO OPTIONS DATA YET (that's P8), so we use a REALIZED-VOLATILITY PROXY for IV:
annualized stdev of daily log returns over a short window. IV-rank / percentile are
computed on that proxy over the config lookback. This drives debit-vs-credit
selection — NOT the price signal. P8 swaps the proxy for real implied vol and
revalidates; the rank/percentile/regime logic is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

UNIVERSE = Path(__file__).resolve().parent.parent / "config" / "universe.yaml"
TRADING_DAYS = 252


def _cfg(path=UNIVERSE) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)["iv_regime"]


def realized_vol(daily_close: pd.Series, window: int = 20) -> pd.Series:
    """Annualized realized volatility (IV proxy) from daily closes."""
    logret = np.log(daily_close / daily_close.shift(1))
    return logret.rolling(window).std() * np.sqrt(TRADING_DAYS)


@dataclass
class IVRead:
    iv: float             # current proxy IV (annualized)
    iv_rank: float        # 0-100, (cur-min)/(max-min) over lookback
    iv_percentile: float  # 0-100, fraction of lookback below current
    regime: str           # "low" | "mid" | "high"


def iv_read(daily_close: pd.Series, path=UNIVERSE) -> IVRead | None:
    cfg = _cfg(path)
    lookback = int(cfg["iv_rank_lookback_days"])
    rv = realized_vol(daily_close).dropna()
    if len(rv) < 30:
        return None
    window = rv.iloc[-lookback:]
    cur = float(rv.iloc[-1])
    lo, hi = float(window.min()), float(window.max())
    rank = 100.0 * (cur - lo) / (hi - lo) if hi > lo else 50.0
    pct = 100.0 * float((window < cur).mean())
    if rank < cfg["low_iv_rank_below"]:
        regime = "low"
    elif rank > cfg["high_iv_rank_above"]:
        regime = "high"
    else:
        regime = "mid"
    return IVRead(iv=cur, iv_rank=round(rank, 1), iv_percentile=round(pct, 1),
                  regime=regime)
