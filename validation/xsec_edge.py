"""Cross-sectional horse-race — the untested family: rank the universe, trade the spread.

    uv run python -m validation.xsec_edge

Three dollar-neutral (50% long / 50% short, decile) portfolios on the 400-name liquid
universe, all on ONE harness with a 1-bar execution lag and costs ON:
  - MOMENTUM   : 12-1mo return rank, monthly rebalance, hold 1mo (long winners / short losers)
  - REVERSAL   : prior-week return rank, weekly rebalance, hold 1wk (long losers / short winners)
  - OVERNIGHT  : decompose each name into overnight (close->open) vs intraday (open->close)
                 returns, equal-weight the universe (descriptive anomaly, frictions flagged)

Each L/S strategy is compared to a RANDOM-RANK portfolio (same dates/sizing). Dollar-neutral
by construction => market-agnostic. These need breadth (we have 400 names), not long history.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from data.cache import ParquetCache

UNIV = Path(__file__).resolve().parent.parent / "config" / "liquid_universe.json"
COST = 5e-4         # 5 bps per side (commission + slippage), liquid large-cap equity
ANN = 252


def load_panels(cache, syms):
    closes, opens = {}, {}
    for s in syms:
        df = cache.read(s, "1d")
        if df is None or len(df) < 300:
            continue
        closes[s], opens[s] = df["close"], df["open"]
    return pd.DataFrame(closes).sort_index(), pd.DataFrame(opens).sort_index()


def ls_backtest(C, score_fn, lookback, hold, rebal, rng=None, randomize=False):
    """Dollar-neutral decile L/S. Signal at i (close[i]); enter close[i+1], exit close[i+1+hold]."""
    dates = C.index
    rets, dts = [], []
    prev_w = pd.Series(0.0, index=C.columns)
    for i in range(lookback, len(dates) - hold - 1, rebal):
        scores = score_fn(C, i).dropna()
        if len(scores) < 20:
            continue
        if randomize:
            scores = pd.Series(rng.permutation(scores.to_numpy()), index=scores.index)
        n_dec = max(1, len(scores) // 10)
        ranked = scores.sort_values()
        shorts, longs = ranked.index[:n_dec], ranked.index[-n_dec:]
        w = pd.Series(0.0, index=C.columns)
        w[longs] = 0.5 / len(longs)
        w[shorts] = -0.5 / len(shorts)
        p0, p1 = C.iloc[i + 1], C.iloc[i + 1 + hold]
        stock_ret = (p1 / p0 - 1.0).reindex(w.index)
        port = float((w * stock_ret).sum(skipna=True))
        cost = float((w - prev_w).abs().sum()) * COST
        rets.append(port - cost)
        dts.append(dates[i + 1])
        prev_w = w
    return pd.Series(rets, index=pd.DatetimeIndex(dts))


def report(name, r, hold):
    ppy = ANN / hold
    mean, sd = r.mean(), r.std()
    sharpe = mean / sd * np.sqrt(ppy) if sd > 0 else 0.0
    t = mean / (sd / np.sqrt(len(r))) if sd > 0 and len(r) else 0.0
    return {"strategy": name, "periods": len(r), "ann_ret%": round(mean * ppy * 100, 1),
            "ann_vol%": round(sd * np.sqrt(ppy) * 100, 1), "Sharpe": round(sharpe, 2),
            "hit": round(float((r > 0).mean()), 3), "t": round(t, 2)}


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    cache = ParquetCache()
    syms = json.loads(UNIV.read_text())
    C, OP = load_panels(cache, syms)
    rng = np.random.default_rng(42)
    print("=== CROSS-SECTIONAL HORSE-RACE (dollar-neutral L/S, costs on, 1-bar lag) ===")
    print(f"universe {C.shape[1]} names | {C.index[0].date()} -> {C.index[-1].date()} | cost {COST*1e4:.0f}bps/side\n")

    def mom(C, i):                                       # 12-1 momentum (skip last month)
        return C.iloc[i - 21] / C.iloc[i - 252] - 1.0

    def rev(C, i):                                        # 1-week reversal
        return -(C.iloc[i] / C.iloc[i - 5] - 1.0)

    rows = []
    for nm, fn, lb, hold, reb in [("momentum_12-1", mom, 252, 21, 21),
                                  ("reversal_1wk", rev, 6, 5, 5)]:
        real = ls_backtest(C, fn, lb, hold, reb)
        rand = ls_backtest(C, fn, lb, hold, reb, rng=rng, randomize=True)
        rr = report(nm, real, hold)
        zz = report("  (random-rank)", rand, hold)
        rr["beats_rand"] = rr["Sharpe"] > zz["Sharpe"]
        rows.append(rr)
        rows.append({**zz, "beats_rand": ""})

    # overnight vs intraday decomposition (equal-weight universe, descriptive)
    on = (OP / C.shift(1) - 1.0).mean(axis=1, skipna=True).dropna()
    intr = (C / OP - 1.0).mean(axis=1, skipna=True).dropna()
    cc = (C / C.shift(1) - 1.0).mean(axis=1, skipna=True).dropna()
    for nm, s in [("overnight_only", on), ("intraday_only", intr), ("close-to-close", cc)]:
        rr = report(nm, s, 1)
        rr["beats_rand"] = ""
        rows.append(rr)

    print(pd.DataFrame(rows).to_string(index=False))
    print("\nNotes: momentum/reversal are tradeable (dollar-neutral, costs in). overnight/intraday")
    print("is descriptive — a daily MOC/MOO strategy would pay ~2x daily turnover (frictions likely kill it).")
    print("PASS bar (per CLAUDE.md): a strategy advances only if Sharpe>=1.2 AND beats random AND t>=2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
