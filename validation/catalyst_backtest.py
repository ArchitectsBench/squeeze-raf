"""Does a CATALYST (news sentiment) lift the squeeze win-rate?  (daily timeframe)

    uv run python -m validation.catalyst_backtest

For every historical EW-grade daily squeeze setup in the sentiment window (~Aug 2024+),
measure the direction-adjusted forward return (10/20d) and split by the news sentiment
at the setup (net pos-neg over the prior 5 days):
  ALIGNED   — sentiment agrees with the trade direction
  OPPOSED   — sentiment against it
  NONE      — no/neutral news
Then compare win-rate & mean forward return. No look-ahead: sentiment is from days <= the
setup; return is forward. CAVEAT: sentiment history is only ~23 months (one regime) — a
first read, not a powered verdict.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from indicators.squeeze import momentum_hist, squeeze_state
from indicators.ta import sma
from reporting.screener_core import MIN_COIL, MOM_PCT_MAX, TREND_LEN, TREND_SLOPE

ROOT = Path(__file__).resolve().parent.parent
SENT = ROOT / "data" / "news_sentiment.json"
WIN_START = pd.Timestamp("2024-08-01", tz="UTC")
HORIZONS = [10, 20]
SENT_LOOKBACK_DAYS = 5


def _coil_series(state: pd.Series) -> np.ndarray:
    run, out = 0, []
    for v in state.to_numpy():
        run = run + 1 if v >= 1 else 0
        out.append(run)
    return np.array(out)


def _net_sent(daily: dict, date: pd.Timestamp) -> int:
    """net (pos - neg) over [date-5d, date] for this ticker; 0 if no news."""
    net = 0
    for k in range(SENT_LOOKBACK_DAYS + 1):
        d = (date - pd.Timedelta(days=k)).strftime("%Y-%m-%d")
        pn = daily.get(d)
        if pn:
            net += pn[0] - pn[1]
    return net


def _stat(vals: list[float]) -> dict:
    a = np.array(vals, dtype=float)
    a = a[~np.isnan(a)]
    n = len(a)
    if n < 2:
        return {"n": n, "win%": float("nan"), "mean_bps": float("nan"), "t": float("nan")}
    sd = a.std(ddof=1)
    return {"n": n, "win%": round(float((a > 0).mean()) * 100, 1),
            "mean_bps": round(float(a.mean()), 1),
            "t": round(float(a.mean() / (sd / np.sqrt(n))), 2) if sd > 0 else 0.0}


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    cache = ParquetCache()
    sent = json.loads(SENT.read_text()) if SENT.exists() else {}
    syms = json.loads((ROOT / "config" / "common_stocks.json").read_text())

    buckets = {h: {"all": [], "ALIGNED": [], "OPPOSED": [], "NONE": []} for h in HORIZONS}
    n_setups = 0
    for s in syms:
        df = cache.read(s, "1d")
        if df is None or len(df) < TREND_LEN + 40:
            continue
        close = df["close"]
        st = squeeze_state(df, length=TREND_LEN)
        mom = momentum_hist(df, length=TREND_LEN)
        ma = sma(close, TREND_LEN)
        coil = _coil_series(st)
        daily_sent = sent.get(s, {})
        cl = close.to_numpy()
        mv = mom.to_numpy()
        mav = ma.to_numpy()
        stv = st.to_numpy()
        idx = df.index
        for i in range(TREND_SLOPE + 1, len(df) - max(HORIZONS)):
            if idx[i] < WIN_START:
                continue
            if np.isnan(mav[i]) or np.isnan(mav[i - TREND_SLOPE]):
                continue
            # trend
            if cl[i] > mav[i] and mav[i] > mav[i - TREND_SLOPE]:
                trend = 1
            elif cl[i] < mav[i] and mav[i] < mav[i - TREND_SLOPE]:
                trend = -1
            else:
                continue
            if 3 not in stv[i - 1:i + 1] or stv[i] == 0 or coil[i] < MIN_COIL:
                continue
            if not (cl[i] > 0 and abs(mv[i]) / cl[i] * 100 <= MOM_PCT_MAX):
                continue
            n_setups += 1
            net = _net_sent(daily_sent, idx[i])
            cat = "NONE" if net == 0 else ("ALIGNED" if np.sign(net) == trend else "OPPOSED")
            for h in HORIZONS:
                r = trend * (cl[i + h] / cl[i] - 1.0) * 1e4
                buckets[h]["all"].append(r)
                buckets[h][cat].append(r)

    print("=== CATALYST x SQUEEZE backtest (daily, sentiment window ~Aug2024+) ===")
    print(f"{len([s for s in syms if s in sent])} tickers w/ sentiment | EW-grade setups: {n_setups}\n")
    for h in HORIZONS:
        print(f"--- forward {h}d (direction-adjusted) ---")
        rows = []
        for cat in ["all", "ALIGNED", "NONE", "OPPOSED"]:
            r = _stat(buckets[h][cat])
            rows.append({"bucket": cat, **r})
        print(pd.DataFrame(rows).to_string(index=False))
        a, nn = _stat(buckets[h]["ALIGNED"]), _stat(buckets[h]["NONE"])
        if not np.isnan(a["mean_bps"]) and not np.isnan(nn["mean_bps"]):
            print(f"  ALIGNED vs NONE: win {a['win%']}% vs {nn['win%']}% | "
                  f"mean {a['mean_bps']} vs {nn['mean_bps']} bps | "
                  f"lift {round(a['mean_bps'] - nn['mean_bps'], 1)} bps\n")
    print("CAVEAT: ~23mo sentiment history (one regime); first read, not a powered verdict.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
