"""Does MULTI-TIMEFRAME CONFLUENCE lift the daily EW-squeeze win-rate?

    uv run python -m validation.confluence_backtest

The screener ranks by a confluence score that has never been validated. This tests it:
for every historical daily EW-grade setup, count how many MULTIDAY timeframes
(2d/3d/4d/1w — the ones with full history) also showed a tier-grade cell
(screener_core.tier logic) in the SAME direction as-of the setup date, then compare
forward returns by confluence bucket.

Gate PRE-REGISTERED in config/gates.yaml (confluence_backtest) before the first run:
conf>=1 must beat conf==0 at 10d by >= 3 win-pts with diff-t >= 2.0, else confluence is
demoted to display-only. No look-ahead: multiday bars are anchored from history start and
labeled by their LAST day, so the bar used at date t ends on or before t. CAVEATS:
intraday TFs excluded (only ~50d of history — live confluence includes them);
overlapping setup-days autocorrelate, so t-stats are somewhat optimistic (same
methodology as verdict_backtest for comparability).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from data.cache import ParquetCache
from data.resample import resample_multiday
from indicators.squeeze import momentum_hist, squeeze_state
from indicators.ta import sma
from reporting.screener_core import MIN_COIL, MOM_PCT_MAX, TREND_LEN, TREND_SLOPE

ROOT = Path(__file__).resolve().parent.parent
HORIZONS = [10, 20]
MULTI_TFS = ["2d", "3d", "4d", "1w"]


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


def _welch_t(a: list[float], b: list[float]) -> float:
    x, y = np.array(a, dtype=float), np.array(b, dtype=float)
    x, y = x[~np.isnan(x)], y[~np.isnan(y)]
    if len(x) < 2 or len(y) < 2:
        return float("nan")
    vx, vy = x.var(ddof=1) / len(x), y.var(ddof=1) / len(y)
    return float((x.mean() - y.mean()) / np.sqrt(vx + vy)) if (vx + vy) > 0 else 0.0


def _coil(state: pd.Series) -> np.ndarray:
    run, out = 0, []
    for v in state.to_numpy():
        run = run + 1 if v >= 1 else 0
        out.append(run)
    return np.array(out)


def _cell_dirs(df: pd.DataFrame) -> pd.Series:
    """Per-bar tier-grade cell direction (+1/-1, 0 if no EW-grade cell) — the historical
    equivalent of screener_core.classify()['tier']/'dir' evaluated at every bar."""
    st = squeeze_state(df, length=TREND_LEN)
    mom = momentum_hist(df, length=TREND_LEN)
    ma = sma(df["close"], TREND_LEN).to_numpy()
    cl, stv, mv, coil = df["close"].to_numpy(), st.to_numpy(), mom.to_numpy(), _coil(st)
    out = np.zeros(len(df))
    for j in range(TREND_SLOPE + 1, len(df)):
        if np.isnan(ma[j]) or np.isnan(ma[j - TREND_SLOPE]):
            continue
        if cl[j] > ma[j] and ma[j] > ma[j - TREND_SLOPE]:
            trend = 1
        elif cl[j] < ma[j] and ma[j] < ma[j - TREND_SLOPE]:
            trend = -1
        else:
            continue
        if 3 not in stv[j - 1:j + 1] or stv[j] == 0 or coil[j] < MIN_COIL:
            continue
        if not (cl[j] > 0 and abs(mv[j]) / cl[j] * 100.0 <= MOM_PCT_MAX):
            continue
        out[j] = trend
    return pd.Series(out, index=df.index)


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    cache = ParquetCache()
    syms = json.loads((ROOT / "config" / "common_stocks.json").read_text())
    gate = yaml.safe_load((ROOT / "config" / "gates.yaml").read_text())["confluence_backtest"]

    setups = []
    for s in syms:
        df = cache.read(s, "1d")
        if df is None or len(df) < TREND_LEN + 40:
            continue
        # multiday tier-grade cell directions, aligned to daily dates (ffill: the bar
        # used at date t is the last one whose label — its final day — is <= t)
        conf_dirs = {}
        for tf in MULTI_TFS:
            mdf = resample_multiday(df, tf)
            if len(mdf) < TREND_LEN + TREND_SLOPE + 2:
                continue
            conf_dirs[tf] = _cell_dirs(mdf).reindex(df.index, method="ffill").to_numpy()
        cl = df["close"].to_numpy()
        st = squeeze_state(df, length=TREND_LEN)
        mom = momentum_hist(df, length=TREND_LEN)
        ma = sma(df["close"], TREND_LEN).to_numpy()
        stv, mv, coil, idx = st.to_numpy(), mom.to_numpy(), _coil(st), df.index
        for i in range(TREND_SLOPE + 1, len(df) - max(HORIZONS)):
            if np.isnan(ma[i]) or np.isnan(ma[i - TREND_SLOPE]):
                continue
            if cl[i] > ma[i] and ma[i] > ma[i - TREND_SLOPE]:
                trend = 1
            elif cl[i] < ma[i] and ma[i] < ma[i - TREND_SLOPE]:
                trend = -1
            else:
                continue
            if 3 not in stv[i - 1:i + 1] or stv[i] == 0 or coil[i] < MIN_COIL:
                continue
            m3 = mv[i - 2:i + 1]
            if not (cl[i] > 0 and abs(mv[i]) / cl[i] * 100 <= MOM_PCT_MAX):
                continue
            tier = ("ALERT" if (np.sign(mv[i]) == trend and abs(m3[-1]) > abs(m3[-2]) > abs(m3[-3]))
                    else "WATCH")
            conf = sum(1 for tf in conf_dirs if conf_dirs[tf][i] == trend)
            fwd = {h: trend * (cl[i + h] / cl[i] - 1.0) * 1e4 for h in HORIZONS}
            setups.append({"symbol": s, "date": idx[i], "tier": tier, "conf": conf, "fwd": fwd})

    print("=== CONFLUENCE backtest (daily EW-grade setups; extra TFs = 2d/3d/4d/1w) ===")
    print(f"{len(setups)} setups | conf distribution: "
          f"{pd.Series([x['conf'] for x in setups]).value_counts().sort_index().to_dict()}\n")

    buckets = [("conf=0", lambda x: x["conf"] == 0),
               ("conf=1", lambda x: x["conf"] == 1),
               ("conf>=2", lambda x: x["conf"] >= 2),
               ("conf>=1 (gate)", lambda x: x["conf"] >= 1)]
    for h in HORIZONS:
        rows = [{"bucket": name, **_stat([x["fwd"][h] for x in setups if sel(x)])}
                for name, sel in buckets]
        print(f"  forward {h}d (all setups):")
        print(pd.DataFrame(rows).to_string(index=False))
        print()

    # ALERT-only subset (descriptive, pre-registered secondary)
    al = [x for x in setups if x["tier"] == "ALERT"]
    for h in [10]:
        rows = [{"bucket": name, **_stat([x["fwd"][h] for x in al if sel(x)])}
                for name, sel in buckets]
        print(f"  forward {h}d (ALERT-only, {len(al)} setups):")
        print(pd.DataFrame(rows).to_string(index=False))
        print()

    # ---- PRE-REGISTERED GATE (10d, conf>=1 vs conf==0) ----
    h = int(gate["primary_horizon_days"])
    g1 = [x["fwd"][h] for x in setups if x["conf"] >= 1]
    g0 = [x["fwd"][h] for x in setups if x["conf"] == 0]
    s1, s0 = _stat(g1), _stat(g0)
    lift = s1["win%"] - s0["win%"]
    t_diff = round(_welch_t(g1, g0), 2)
    ok = lift >= gate["h1_win_rate_lift_pts_min"] and t_diff >= gate["h1_diff_tstat_min"]
    print("--- PRE-REGISTERED GATE (config/gates.yaml: confluence_backtest) ---")
    print(f"  win-rate lift (conf>=1 minus conf=0) @ {h}d: {lift:+.1f} pts "
          f"(need >= {gate['h1_win_rate_lift_pts_min']})")
    print(f"  diff t-stat (Welch): {t_diff} (need >= {gate['h1_diff_tstat_min']})")
    print(f"  VERDICT: {'PASS — ' + gate['on_pass'] if ok else 'FAIL — ' + gate['on_fail']}")
    print("\nCAVEATS: intraday TFs not in the tested confluence (no history); overlapping "
          "setup-days autocorrelate (t optimistic); one universe/regime.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
