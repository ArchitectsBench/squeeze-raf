"""Event study on Confluence v0 entry signals (BUILD_SPEC.md §5 Stage 1).

Descriptive statistics only — NO optimization, NO parameter search. We measure
whether the *default* rule has raw forward-return edge before it earns the right
to be optimized (CLAUDE.md rule 3).

Look-ahead discipline (T5): an entry signalled on close[t] is entered at
**open[t+1]** and its forward return at horizon h is open[t+1+h]/open[t+1]-1,
signed by trade direction. Nothing reads the same bar's close-to-fill.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from signals import ConfluenceParams, generate
from signals.confluence import MODES

# Pre-registered PRIMARY horizon (bars) used for the PASS/FAIL verdict, chosen
# from §4 holding buckets BEFORE seeing results: 4 bars ≈ 1 day (2h), ≈2 days
# (4h), ≈4 days (1d) — all inside each timeframe's intended hold. All horizons
# are still reported.
PRIMARY_HORIZON_BARS = 4


def collect_entries(df: pd.DataFrame, signals, horizons: list[int]) -> pd.DataFrame:
    """One row per entry: direction, conviction, and signed forward return at
    each horizon (in basis points)."""
    open_ = df["open"]
    entry_px = open_.shift(-1)
    mask = signals.direction != 0
    out = {
        "direction": signals.direction[mask],
        "conviction": signals.conviction[mask],
    }
    for h in horizons:
        exit_px = open_.shift(-(1 + h))
        ret = (exit_px / entry_px - 1.0) * signals.direction
        out[f"ret_{h}"] = ret[mask] * 1e4  # bps
    return pd.DataFrame(out)


def random_entries(
    df: pd.DataFrame, n: int, dir_balance: float, horizons: list[int], rng
) -> pd.DataFrame:
    """Matched-frequency random baseline: n random entry bars, directions drawn to
    match the real long/short balance, same forward-return measurement."""
    cols = ["direction", "conviction"] + [f"ret_{h}" for h in horizons]
    open_ = df["open"]
    entry_px = open_.shift(-1)
    valid = np.arange(len(df) - (max(horizons) + 2))
    if n == 0 or len(valid) == 0:
        return pd.DataFrame(columns=cols)
    pos = rng.choice(valid, size=min(n, len(valid)), replace=False)
    direction = np.where(rng.random(len(pos)) < dir_balance, 1, -1)
    rows = {"direction": direction, "conviction": np.full(len(pos), np.nan)}
    for h in horizons:
        exit_px = open_.shift(-(1 + h)).to_numpy()
        ret = (exit_px[pos] / entry_px.to_numpy()[pos] - 1.0) * direction
        rows[f"ret_{h}"] = ret * 1e4
    return pd.DataFrame(rows)


def _stats(ret_bps: pd.Series) -> dict:
    r = ret_bps.dropna()
    n = len(r)
    if n == 0:
        return {"n": 0, "mean_bps": np.nan, "median_bps": np.nan,
                "hit_rate": np.nan, "t_stat": np.nan}
    sd = r.std(ddof=1)
    t = r.mean() / (sd / np.sqrt(n)) if sd > 0 else np.nan
    return {
        "n": n,
        "mean_bps": float(r.mean()),
        "median_bps": float(r.median()),
        "hit_rate": float((r > 0).mean()),
        "t_stat": float(t),
    }


def monotonicity(entries: pd.DataFrame, horizon: int, q: int = 3) -> dict:
    """Mean forward return per conviction quantile (terciles by default) at the
    primary horizon, plus whether means are non-decreasing."""
    col = f"ret_{horizon}"
    e = entries.dropna(subset=[col, "conviction"])
    if len(e) < q * 5 or e["conviction"].nunique() < q:
        return {"buckets": [], "monotonic": False, "note": "insufficient spread"}
    try:
        labels = pd.qcut(e["conviction"], q, labels=False, duplicates="drop")
    except ValueError:
        return {"buckets": [], "monotonic": False, "note": "degenerate conviction"}
    means = e.groupby(labels)[col].mean()
    vals = means.to_list()
    mono = all(vals[i] <= vals[i + 1] for i in range(len(vals) - 1))
    return {"buckets": [round(v, 2) for v in vals], "monotonic": bool(mono), "note": ""}


def run_study(
    frames: dict[str, pd.DataFrame],   # symbol -> df, all one timeframe
    timeframe: str,
    horizons: list[int],
    seed: int = 42,
) -> tuple[pd.DataFrame, dict]:
    """Run the event study for one timeframe across all symbols, all modes.

    Returns (summary_table, monotonicity_by_mode). The summary has one row per
    (mode, horizon) pooled over symbols, with real and random-baseline stats.
    """
    rng = np.random.default_rng(seed)
    rows = []
    mono = {}
    for mode in MODES:
        real_parts, rand_parts = [], []
        for _sym, df in frames.items():
            params = ConfluenceParams.for_timeframe(timeframe)
            sig = generate(df, params, mode=mode)
            e = collect_entries(df, sig, horizons)
            real_parts.append(e)
            n = len(e)
            dir_balance = float((e["direction"] > 0).mean()) if n else 0.5
            rand_parts.append(random_entries(df, n, dir_balance, horizons, rng))
        real = pd.concat(real_parts, ignore_index=True) if real_parts else pd.DataFrame()
        rand = pd.concat(rand_parts, ignore_index=True) if rand_parts else pd.DataFrame()

        for h in horizons:
            rs = _stats(real[f"ret_{h}"]) if len(real) else _stats(pd.Series(dtype=float))
            zs = _stats(rand[f"ret_{h}"]) if len(rand) else _stats(pd.Series(dtype=float))
            rows.append({
                "mode": mode, "timeframe": timeframe, "horizon": h,
                "n": rs["n"], "mean_bps": rs["mean_bps"], "median_bps": rs["median_bps"],
                "hit_rate": rs["hit_rate"], "t_stat": rs["t_stat"],
                "rand_mean_bps": zs["mean_bps"], "rand_hit_rate": zs["hit_rate"],
                "rand_t_stat": zs["t_stat"],
            })
        mono[mode] = monotonicity(real, PRIMARY_HORIZON_BARS) if len(real) else {
            "buckets": [], "monotonic": False, "note": "no entries"}
    return pd.DataFrame(rows), mono
