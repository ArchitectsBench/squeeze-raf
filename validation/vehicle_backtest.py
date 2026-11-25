"""Does the trade-expression engine's vehicle choice beat naive? (stock vs naked vs debit)

    uv run python -m validation.vehicle_backtest

On historical daily EW-grade squeeze setups within the ORATS-cached window, reconstruct the
REALIZED P&L (real ORATS bid/ask -> true frictions, via the P8 machinery) of:
  - STOCK (dir-adjusted return on notional)
  - NAKED LONG option (~0.55Δ, return on premium, buy ask / sell bid)
  - DEBIT SPREAD (return on risk)
compared on the SAME setups. Answers the P8 question: do options pay after frictions, or
does stock win? CAVEATS: ORATS window ~17mo (one regime), ~45-DTE / 15-day hold fixed,
option returns are leveraged (on premium/risk) vs stock (on notional) — compare win-rate
directly, returns with that basis in mind. Directional read, not a full options backtest.
"""

from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from data.cache import ParquetCache
from indicators.squeeze import momentum_hist, squeeze_state
from indicators.ta import sma
from options_backtest.long_option_pnl import reconstruct_long
from options_backtest.orats_adapter import OratsAdapter
from options_backtest.spread_pnl import reconstruct as reconstruct_spread
from reporting.screener_core import MIN_COIL, MOM_PCT_MAX, TREND_LEN, TREND_SLOPE

ROOT = Path(__file__).resolve().parent.parent
HOLD = 15            # trading days
DTE_LO, DTE_HI = 30, 60
MAX_SETUPS = 500     # cap for runtime


def _coil(state):
    run, out = 0, []
    for v in state.to_numpy():
        run = run + 1 if v >= 1 else 0
        out.append(run)
    return np.array(out)


def _stat(vals):
    a = np.array(vals, float)
    a = a[~np.isnan(a)]
    n = len(a)
    if n < 2:
        return {"n": n}
    return {"n": n, "win%": round(float((a > 0).mean()) * 100, 1),
            "mean_ret%": round(float(a.mean()) * 100, 1)}


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    cache = ParquetCache()
    orats_days = {os.path.basename(f)[:8] for f in glob.glob(str(ROOT / "data" / "orats" / "*.parquet"))}
    lo = pd.Timestamp(min(orats_days)).tz_localize("UTC")
    hi = pd.Timestamp(max(orats_days)).tz_localize("UTC")
    syms = json.loads((ROOT / "config" / "common_stocks.json").read_text())
    adapter = OratsAdapter(universe=set(syms))
    print(f"ORATS window {lo.date()}..{hi.date()} ({len(orats_days)} cached days)\n")

    def has(d):
        return d.strftime("%Y%m%d") in orats_days

    rows = {"STOCK": [], "NAKED_LONG": [], "DEBIT": []}
    n_setups = 0
    for s in syms:
        if n_setups >= MAX_SETUPS:
            break
        df = cache.read(s, "1d")
        if df is None or len(df) < TREND_LEN + 40:
            continue
        cl = df["close"].to_numpy()
        st = squeeze_state(df, length=TREND_LEN).to_numpy()
        mom = momentum_hist(df, length=TREND_LEN).to_numpy()
        ma = sma(df["close"], TREND_LEN).to_numpy()
        coil = _coil(squeeze_state(df, length=TREND_LEN))
        idx = df.index
        for i in range(TREND_SLOPE + 1, len(df) - HOLD):
            if n_setups >= MAX_SETUPS:
                break
            entry, exitd = idx[i], idx[i + HOLD]
            if entry < lo or exitd > hi or not has(entry) or not has(exitd):
                continue
            if np.isnan(ma[i]) or np.isnan(ma[i - TREND_SLOPE]):
                continue
            trend = 1 if (cl[i] > ma[i] and ma[i] > ma[i - TREND_SLOPE]) else (
                -1 if (cl[i] < ma[i] and ma[i] < ma[i - TREND_SLOPE]) else 0)
            if trend == 0 or 3 not in st[i - 1:i + 1] or st[i] == 0 or coil[i] < MIN_COIL:
                continue
            if not (cl[i] > 0 and abs(mom[i]) / cl[i] * 100 <= MOM_PCT_MAX):
                continue
            lng = reconstruct_long(adapter, s, entry, exitd, trend, DTE_LO, DTE_HI, target_delta=0.55)
            deb = reconstruct_spread(adapter, s, entry, exitd, trend, "debit", DTE_LO, DTE_HI)
            if lng is None or deb is None:
                continue                                   # require all 3 for fair same-setup compare
            rows["STOCK"].append(trend * (cl[i + HOLD] / cl[i] - 1.0))
            rows["NAKED_LONG"].append(lng.return_on_premium)
            rows["DEBIT"].append(deb.return_on_risk)
            n_setups += 1

    print(f"=== VEHICLE BACKTEST — {n_setups} setups (same setups, net of ORATS frictions) ===")
    out = [{"vehicle": k, **_stat(v)} for k, v in rows.items()]
    print(pd.DataFrame(out).to_string(index=False))
    if n_setups:
        sm = np.mean(rows["STOCK"])
        nm = np.mean(rows["NAKED_LONG"])
        dm = np.mean(rows["DEBIT"])
        print("\nVERDICT:")
        print(f"  naked-long net mean {nm*100:+.1f}% on premium — {'POSITIVE' if nm>0 else 'NEGATIVE (loses after frictions)'}")
        print(f"  debit-spread net mean {dm*100:+.1f}% on risk — {'POSITIVE' if dm>0 else 'NEGATIVE'}")
        print(f"  stock net mean {sm*100:+.1f}% on notional (leverage-free)")
        print("  Note: option returns are leveraged (premium/risk basis); compare win-rate directly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
