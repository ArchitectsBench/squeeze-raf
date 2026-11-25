"""Cointegration screening for the pairs pool (Engle-Granger, train-window only).

Candidate pairs are formed WITHIN each sector (economically sensible, bounds the
multiple-testing). Each is screened on a TRAINING window: Engle-Granger cointegration
p-value < pmax AND a sane mean-reversion half-life. Selection uses only train data so
the out-of-sample edge-check isn't look-ahead.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint

from data.universe import load_pairs_pool


def candidate_pairs() -> list[tuple[str, str]]:
    pairs = []
    for names in load_pairs_pool().values():
        pairs.extend(combinations(names, 2))
    return pairs


def correlation_candidates(frames: dict[str, pd.DataFrame], train_end: pd.Timestamp,
                           min_corr: float = 0.80, max_pairs: int = 3000) -> list[tuple[str, str]]:
    """Form candidate pairs from the universe by high return-correlation on the TRAIN
    window (a cheap pre-filter before the expensive cointegration test)."""
    rets = {}
    for s, df in frames.items():
        r = df["close"].pct_change()
        r = r[r.index < train_end]
        if len(r.dropna()) > 250:
            rets[s] = r
    mat = pd.DataFrame(rets).dropna(how="all")
    corr = mat.corr()
    syms = list(corr.columns)
    out = []
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            c = corr.at[a, b]
            if c == c and c >= min_corr:
                out.append((c, a, b))
    out.sort(key=lambda z: -z[0])
    return [(a, b) for _, a, b in out[:max_pairs]]


def half_life(spread: pd.Series) -> float:
    """Mean-reversion half-life from an AR(1) fit: Δs = a + b*s[-1], HL = -ln2/b."""
    s = spread.dropna()
    ds = s.diff().dropna()
    lag = s.shift(1).loc[ds.index]
    b = np.polyfit(lag.to_numpy(), ds.to_numpy(), 1)[0]
    return -np.log(2) / b if b < 0 else np.inf


def screen(frames: dict[str, pd.DataFrame], train_end: pd.Timestamp,
           pmax: float = 0.05, hl_range: tuple[float, float] = (2.0, 40.0),
           min_obs: int = 250, candidates: list[tuple[str, str]] | None = None) -> list[dict]:
    """Return screened pairs: [{a, b, pvalue, half_life, beta}] cointegrated on train.
    `candidates` defaults to within-sector pool pairs; pass a list to screen any set."""
    kept = []
    for a, b in (candidates if candidates is not None else candidate_pairs()):
        if a not in frames or b not in frames:
            continue
        da, db = frames[a], frames[b]
        idx = da.index.intersection(db.index)
        idx = idx[idx < train_end]
        if len(idx) < min_obs:
            continue
        la = np.log(da["close"].reindex(idx))
        lb = np.log(db["close"].reindex(idx))
        try:
            _, pval, _ = coint(la, lb)
        except Exception:
            continue
        if pval >= pmax:
            continue
        beta = float(np.polyfit(lb.to_numpy(), la.to_numpy(), 1)[0])
        hl = half_life(la - beta * lb)
        if hl_range[0] <= hl <= hl_range[1]:
            kept.append({"a": a, "b": b, "pvalue": round(pval, 4),
                         "half_life": round(hl, 1), "beta": round(beta, 3)})
    return kept
