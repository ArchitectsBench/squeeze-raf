"""Screen several candidate MARKET-NEUTRAL factors on the clean universe, head-to-head.

    uv run python -m validation.factor_screen

Each factor -> a dollar-neutral decile long/short portfolio (equal-weight, costs on,
1-bar lag, point-in-time cleaning). We report Sharpe / t / hit, a random-rank baseline,
AND the portfolio's market beta (a true market-neutral edge has beta ~ 0). Used to pick
the 2-3 best for forward-testing.

Factors:
  resid_mom   : residual (beta-neutral) 12-1 momentum               long winners
  low_beta    : -beta (betting-against-beta)                        long low-beta
  low_idiovol : -idiosyncratic vol (low-vol anomaly)                long low-idiovol
  resid_rev1m : -residual 1-month return (short-term reversal)      long recent losers
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from signals.xsec_momentum import betas, resid_scores

UNIV = Path(__file__).resolve().parent.parent / "config" / "liquid_universe.json"
SECT = Path(__file__).resolve().parent.parent / "data" / "sectors.json"
COST = 5e-4
ANN = 12
HOLD = REB = 21


def load_panel(cache, syms):
    closes = {s: cache.read(s, "1d")["close"] for s in syms if cache.exists(s, "1d")}
    return pd.DataFrame({s: v for s, v in closes.items() if v is not None and len(v) > 300}).sort_index()


def _clean(C, RET, mret, i, lb, sectors):
    full = C.iloc[i - lb].notna()
    raw = C.iloc[i - 21] / C.iloc[i - lb] - 1.0
    b = betas(RET, mret, i - lb, i)
    px = C.iloc[i]
    return [s for s in C.columns
            if s in sectors and bool(full.get(s, False)) and float(px[s]) >= 5.0
            and abs(float(raw.get(s, 0.0))) <= 3.0 and abs(float(b.get(s, 0.0))) <= 3.0], b


def _score(name, C, RET, mret, mclose, i, lb, b):
    if name == "resid_mom":
        return resid_scores(C, RET, mret, mclose, i, lb, 21)
    if name == "low_beta":
        return -b
    if name == "low_idiovol":
        sub = RET.iloc[i - 60:i]
        m = mret.iloc[i - 60:i]
        resid = sub.sub(sub.mean()).sub(np.outer(m - m.mean(), b), fill_value=0)
        return -resid.std()
    if name == "resid_rev1m":
        r1 = C.iloc[i] / C.iloc[i - 21] - 1.0
        mr = float(mclose.iloc[i] / mclose.iloc[i - 21] - 1.0)
        return -(r1 - b * mr)
    raise ValueError(name)


def backtest(name, C, RET, mret, mclose, sectors, start, rng=None, randomize=False):
    dates = C.index
    rets, dts = [], []
    prev = pd.Series(0.0, index=C.columns)
    for i in range(start, len(dates) - HOLD - 1, REB):
        keep, b = _clean(C, RET, mret, i, 252, sectors)
        sc = _score(name, C, RET, mret, mclose, i, 252, b).reindex(keep).dropna()
        # sector-neutralize the score
        grp = pd.Series({s: sectors.get(s, "NA") for s in sc.index})
        sc = sc - sc.groupby(grp).transform("mean")
        if len(sc) < 20:
            continue
        if randomize:
            sc = pd.Series(rng.permutation(sc.to_numpy()), index=sc.index)
        k = max(1, int(len(sc) * 0.2))
        r = sc.sort_values()
        shorts, longs = r.index[:k], r.index[-k:]
        w = pd.Series(0.0, index=C.columns)
        w[longs] = 0.5 / len(longs)
        w[shorts] = -0.5 / len(shorts)
        p0, p1 = C.iloc[i + 1], C.iloc[i + 1 + HOLD]
        sret = (p1 / p0 - 1.0).reindex(w.index)
        rets.append(float((w * sret).sum(skipna=True)) - float((w - prev).abs().sum()) * COST)
        dts.append(dates[i + 1])
        prev = w
    return pd.Series(rets, index=pd.DatetimeIndex(dts))


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    cache = ParquetCache()
    syms = json.loads(UNIV.read_text())
    sectors = json.loads(SECT.read_text())
    C = load_panel(cache, syms)
    RET = C.pct_change()
    mclose = cache.read("SPY", "1d")["close"].reindex(C.index).ffill()
    mret = mclose.pct_change()
    mret_m = mclose.resample("ME").last().pct_change()
    mret_m.index = mret_m.index.tz_localize(None)
    rng = np.random.default_rng(42)
    start = 253

    print("=== MARKET-NEUTRAL FACTOR SCREEN (clean universe, dollar-neutral decile L/S) ===")
    print(f"{C.shape[1]} names | {C.index[0].date()} -> {C.index[-1].date()} | cost {COST*1e4:.0f}bps/side\n")
    rows = []
    for name in ("resid_mom", "low_beta", "low_idiovol", "resid_rev1m"):
        s = backtest(name, C, RET, mret, mclose, sectors, start)
        z = backtest(name, C, RET, mret, mclose, sectors, start, rng=rng, randomize=True)
        sd = s.std()
        sharpe = float(s.mean() / sd * np.sqrt(ANN)) if sd > 0 else 0.0
        t = float(s.mean() / (sd / np.sqrt(len(s)))) if sd > 0 else 0.0
        zsd = z.std()
        zsharpe = float(z.mean() / zsd * np.sqrt(ANN)) if zsd > 0 else 0.0
        # market beta of the portfolio (true market-neutral => ~0)
        sm = s.copy()
        sm.index = sm.index.to_period("M").to_timestamp("M")
        df = pd.concat([sm, mret_m], axis=1).dropna()
        beta = float(np.polyfit(df.iloc[:, 1], df.iloc[:, 0], 1)[0]) if len(df) > 5 else float("nan")
        rows.append({"factor": name, "n": len(s), "Sharpe": round(sharpe, 2), "t": round(t, 2),
                     "hit": round(float((s > 0).mean()), 3), "rand_SR": round(zsharpe, 2),
                     "beats_rand": sharpe > zsharpe, "mkt_beta": round(beta, 3)})
    df = pd.DataFrame(rows).sort_values("Sharpe", ascending=False)
    print(df.to_string(index=False))
    print("\nPick: Sharpe>0 & beats_rand & |mkt_beta|<~0.3 (genuinely market-neutral).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
