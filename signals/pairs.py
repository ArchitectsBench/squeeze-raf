"""Pairs / sector-relative (stat-arb) — a genuinely market-neutral signal.

Trade a stock vs an economically-related peer: the spread = logA - beta*logB (beta
= rolling hedge ratio) is market- AND sector-neutral by construction, so its moves
are idiosyncratic. When the spread is stretched (|z| large) we bet on reversion:
z>0 (A rich vs B) -> short the spread (short A / long beta*B); z<0 -> long it.

The position's return is dollar-neutral: d * [Δ(logA) - beta*Δ(logB)] with beta
fixed at entry. No market beta to confound — the random baseline is ~0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# economically-related, same-sector pairs from the broad universe
PAIRS = [
    ("V", "MA"), ("KO", "PEP"), ("XOM", "CVX"), ("JPM", "BAC"), ("GS", "MS"),
    ("AAPL", "MSFT"), ("NVDA", "AMD"), ("AVGO", "QCOM"), ("GOOGL", "META"),
    ("JNJ", "PFE"), ("MRK", "ABBV"), ("LLY", "UNH"), ("CAT", "HON"), ("GE", "HON"),
    ("T", "VZ"), ("COST", "WMT"), ("HD", "WMT"), ("INTC", "QCOM"), ("ADBE", "CRM"),
    # additional same-sector pairs (more samples / power)
    ("AXP", "V"), ("AXP", "MA"), ("JPM", "GS"), ("MS", "BAC"), ("JPM", "WFC"),
    ("BAC", "WFC"), ("JNJ", "MRK"), ("PFE", "ABBV"), ("LLY", "JNJ"), ("UNH", "JNJ"),
    ("NVDA", "AVGO"), ("AMD", "QCOM"), ("INTC", "AMD"), ("ORCL", "CRM"), ("ADBE", "ORCL"),
    ("MSFT", "GOOGL"), ("AMZN", "GOOGL"), ("DIS", "NFLX"), ("PG", "KO"), ("PEP", "PG"),
    ("CAT", "GE"), ("BA", "HON"), ("CVX", "XOM"), ("WMT", "COST"),
]


def pair_table(close_a: pd.Series, close_b: pd.Series, lookback: int = 60) -> pd.DataFrame:
    """Aligned table with logA, logB, rolling hedge ratio beta, spread and z-score."""
    la = np.log(close_a)
    lb = np.log(close_b)
    df = pd.concat([la.rename("la"), lb.rename("lb")], axis=1).dropna()
    cov = df["la"].rolling(lookback).cov(df["lb"])
    var = df["lb"].rolling(lookback).var()
    df["beta"] = (cov / var).clip(0.1, 5.0)         # rolling hedge ratio (sane bounds)
    df["spread"] = df["la"] - df["beta"] * df["lb"]
    mu = df["spread"].rolling(lookback).mean()
    sd = df["spread"].rolling(lookback).std()
    df["z"] = (df["spread"] - mu) / sd
    return df


def entries(table: pd.DataFrame, entry_z: float = 2.0) -> pd.Series:
    """Direction on the SPREAD at each bar: +1 = long spread (z<-entry), -1 = short
    spread (z>entry), 0 otherwise. Reduced to rising-edge events."""
    z = table["z"]
    long_spread = z < -entry_z
    short_spread = z > entry_z
    raw = pd.Series(0, index=table.index, dtype="int64").mask(long_spread, 1).mask(short_spread, -1)
    edge = raw != raw.shift(1, fill_value=0)
    return raw.where(edge & (raw != 0), 0)
