"""Conviction scoring for Confluence v0 (Mode C entries + edge-check monotonicity).

A directional conviction in [0,1] from normalized, look-back-only sub-signals:
- compression loaded   (squeeze state / 3)
- staging              (RAF Setup Count magnitude aligned to direction, capped at 3)
- regime agreement     (EMA ribbon + displaced-TEMA both support the direction)
- momentum agreement   (histogram sign matches the trade direction)
- inflection           (RAF full Fire present this bar)

Weights sum to 1.0. Higher score should mean higher forward return — that
monotonicity is itself a pre-registered edge-check criterion (gates.yaml).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators.raf import RAFFeatures
from indicators.squeeze import SqueezeFeatures

# component weights (sum = 1.0)
W_COMPRESSION = 0.25
W_STAGING = 0.25
W_REGIME = 0.20
W_MOMENTUM = 0.15
W_INFLECTION = 0.15


def directional_conviction(
    sq: SqueezeFeatures, rf: RAFFeatures, direction: int
) -> pd.Series:
    """direction = +1 (long) or -1 (short). Returns a Series in [0,1]."""
    assert direction in (+1, -1)

    c_comp = sq.state / 3.0
    c_stage = (rf.sc * direction).clip(lower=0, upper=3) / 3.0

    ribbon_dir = sq.ribbon.bullish if direction > 0 else sq.ribbon.bearish
    regime = ribbon_dir & ((rf.savgstate * direction) >= 0)
    c_regime = regime.astype(float)

    c_mom = (np.sign(sq.hist) == direction).astype(float)

    fire = rf.fire_buy if direction > 0 else rf.fire_sell
    c_infl = fire.astype(float)

    score = (
        W_COMPRESSION * c_comp
        + W_STAGING * c_stage
        + W_REGIME * c_regime
        + W_MOMENTUM * c_mom
        + W_INFLECTION * c_infl
    )
    return score.clip(0.0, 1.0).fillna(0.0)
