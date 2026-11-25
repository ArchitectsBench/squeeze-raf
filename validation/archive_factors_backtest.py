"""Archive-mined factors: per-name P/C positioning alignment + IV-rank.

    uv run python -m validation.archive_factors_backtest

The quality study found the owned ORATS archive already carries per-strike OI/volume
by side and IVs — the two most promising conditioning factors are computable free.
Gates pre-registered in config/gates.yaml (archive_factors), one-sided hypotheses
fixed from theory before running:
  F1: positioning-ALIGNED setups (UP + call-tilted OI / DOWN + put-tilted, split at
      the within-direction median P/C-OI) beat OPPOSED by >= 5 win-pts @10d, p<0.05.
  F2: LOW IV-rank setups (<=50th expanding pctile of ATM IV, >=60 prior days) beat
      HIGH by >= 5 win-pts @10d, p<0.05 (cheap-vol compression thesis).

Factor snapshots per (symbol, day) are cached to data/orats_factors.parquet (pass 1
is one sweep over the cached ORATS days). Bootstrap seed 42, 5000 iters.
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from data.cache import ParquetCache
from indicators.squeeze import momentum_hist, squeeze_state
from indicators.ta import sma
from options_backtest.orats_adapter import CACHE_DIR
from reporting.screener_core import MIN_COIL, MOM_PCT_MAX, TREND_LEN, TREND_SLOPE

ROOT = Path(__file__).resolve().parent.parent
FACT = ROOT / "data" / "orats_factors.parquet"
HORIZONS = [3, 10]
SEED = 42
BOOT_N = 5000
MIN_IV_HISTORY = 60


def _stat(vals) -> dict:
    a = np.asarray(vals, dtype=float)
    a = a[~np.isnan(a)]
    n = len(a)
    if n < 2:
        return {"n": n, "win%": float("nan"), "mean_bps": float("nan"), "t": float("nan")}
    sd = a.std(ddof=1)
    return {"n": n, "win%": round(float((a > 0).mean()) * 100, 1),
            "mean_bps": round(float(a.mean()), 1),
            "t": round(float(a.mean() / (sd / np.sqrt(n))), 2) if sd > 0 else 0.0}


def _boot_p_wingap(a, b, rng) -> float:
    wa = (np.asarray(a) > 0).astype(float)
    wb = (np.asarray(b) > 0).astype(float)
    if len(wa) < 2 or len(wb) < 2:
        return float("nan")
    ga = rng.choice(wa, size=(BOOT_N, len(wa)), replace=True).mean(axis=1)
    gb = rng.choice(wb, size=(BOOT_N, len(wb)), replace=True).mean(axis=1)
    return float((ga <= gb).mean())


def _coil(state: pd.Series) -> np.ndarray:
    run, out = 0, []
    for v in state.to_numpy():
        run = run + 1 if v >= 1 else 0
        out.append(run)
    return np.array(out)


def build_factor_cache() -> pd.DataFrame:
    """One sweep over cached ORATS days -> per (symbol, day): ATM IV + P/C-OI ratio
    over the nearest 3 expiries (the GEX-study slice)."""
    if FACT.is_file():
        return pd.read_parquet(FACT)
    rows = []
    files = sorted(glob.glob(str(CACHE_DIR / "*.parquet")))
    for i, f in enumerate(files):
        day = pd.Timestamp(Path(f).stem)
        d = pd.read_parquet(f)
        if not len(d) or "cOi" not in d.columns:
            continue
        d = d[d["yte"] > 0.01]
        for sym, ch in d.groupby("ticker"):
            spot = float(ch["stkPx"].iloc[0])
            if spot <= 0:
                continue
            exps = sorted(ch["expiry"].unique())[:3]
            ch3 = ch[ch["expiry"].isin(exps)]
            coi, poi = int(ch3["cOi"].sum()), int(ch3["pOi"].sum())
            near = ch3[ch3["expiry"] == exps[0]]
            atm = near.iloc[(near["strike"] - spot).abs().argsort()[:1]]
            iv = float(atm["cMidIv"].iloc[0]) if len(atm) else np.nan
            rows.append({"symbol": sym, "date": day, "atm_iv": iv,
                         "pcr_oi": (poi / coi) if coi > 0 else np.nan})
        if (i + 1) % 60 == 0:
            print(f"  factor sweep {i+1}/{len(files)} days", flush=True)
    out = pd.DataFrame(rows)
    out.to_parquet(FACT)
    return out


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="archive factors backtest")
    ap.add_argument("--max-date", default=None,
                    help="restrict setups to dates < this (e.g. 2025-01-01 for the "
                         "pre-registered FRESH-DATA re-test on pre-2025 years)")
    args = ap.parse_args(argv)
    max_date = pd.Timestamp(args.max_date) if args.max_date else None
    pd.set_option("display.width", 200)
    rng = np.random.default_rng(SEED)
    cache = ParquetCache()
    syms = json.loads((ROOT / "config" / "common_stocks.json").read_text())
    cfg = yaml.safe_load((ROOT / "config" / "gates.yaml").read_text())["archive_factors"]

    fac = build_factor_cache()
    print(f"factor snapshots: {len(fac)} (symbols {fac['symbol'].nunique()}, "
          f"days {fac['date'].nunique()})")
    fac = fac.sort_values("date")
    # expanding IV percentile per symbol (uses only PRIOR days -> point-in-time)
    fac["iv_rank"] = (fac.groupby("symbol")["atm_iv"]
                      .transform(lambda s: s.expanding().rank(pct=True).shift(0)
                                 .where(s.expanding().count() >= MIN_IV_HISTORY)))
    fmap = {(r.symbol, r.date): (r.pcr_oi, r.iv_rank) for r in fac.itertuples()}
    fdays = set(fac["date"].unique())

    setups = []
    for s in syms:
        df = cache.read(s, "1d")
        if df is None or len(df) < TREND_LEN + 40:
            continue
        cl = df["close"].to_numpy()
        st = squeeze_state(df, length=TREND_LEN)
        mom = momentum_hist(df, length=TREND_LEN)
        ma = sma(df["close"], TREND_LEN).to_numpy()
        stv, mv, coil, idx = st.to_numpy(), mom.to_numpy(), _coil(st), df.index
        for i in range(TREND_SLOPE + 1, len(df) - max(HORIZONS)):
            day = idx[i].normalize().tz_localize(None)
            if day not in fdays:
                continue
            if max_date is not None and day >= max_date:
                continue
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
            if not (cl[i] > 0 and abs(mv[i]) / cl[i] * 100 <= MOM_PCT_MAX):
                continue
            pcr, ivr = fmap.get((s, day), (np.nan, np.nan))
            fwd = {h: trend * (cl[i + h] / cl[i] - 1.0) * 1e4 for h in HORIZONS}
            setups.append({"trend": trend, "pcr": pcr, "ivr": ivr, "fwd": fwd})

    sdf = pd.DataFrame(setups)
    print(f"setups: {len(sdf)} (pcr available {sdf['pcr'].notna().sum()}, "
          f"iv_rank available {sdf['ivr'].notna().sum()})\n")

    # F1 alignment: within-direction median split of pcr
    med_up = sdf.loc[(sdf.trend == 1) & sdf.pcr.notna(), "pcr"].median()
    med_dn = sdf.loc[(sdf.trend == -1) & sdf.pcr.notna(), "pcr"].median()
    def aligned(r):
        if np.isnan(r.pcr):
            return None
        return bool(r.pcr <= med_up) if r.trend == 1 else bool(r.pcr >= med_dn)
    sdf["aligned"] = sdf.apply(aligned, axis=1)

    h = int(cfg["primary_horizon_days"])
    for hh in HORIZONS:
        rows = [
            {"bucket": "F1 aligned", **_stat([x["fwd"][hh] for _, x in sdf[sdf.aligned == True].iterrows()])},   # noqa: E712
            {"bucket": "F1 opposed", **_stat([x["fwd"][hh] for _, x in sdf[sdf.aligned == False].iterrows()])},  # noqa: E712
            {"bucket": "F2 low IV-rank (<=.5)", **_stat([x["fwd"][hh] for _, x in sdf[sdf.ivr <= 0.5].iterrows()])},
            {"bucket": "F2 high IV-rank (>.5)", **_stat([x["fwd"][hh] for _, x in sdf[sdf.ivr > 0.5].iterrows()])},
        ]
        print(f"  forward {hh}d:")
        print(pd.DataFrame(rows).to_string(index=False))
        print()

    print("--- PRE-REGISTERED GATES (config/gates.yaml: archive_factors) ---")
    a = [x["fwd"][h] for _, x in sdf[sdf.aligned == True].iterrows()]    # noqa: E712
    o = [x["fwd"][h] for _, x in sdf[sdf.aligned == False].iterrows()]   # noqa: E712
    g1, p1 = _stat(a)["win%"] - _stat(o)["win%"], _boot_p_wingap(a, o, rng)
    ok1 = g1 >= cfg["f1_win_gap_pts_min"] and p1 < cfg["f1_bootstrap_p_max"]
    print(f"  F1 aligned-opposed @{h}d: {g1:+.1f} pts (need >= {cfg['f1_win_gap_pts_min']}), "
          f"p={p1:.3f} -> {'PASS' if ok1 else 'FAIL'}")
    lo = [x["fwd"][h] for _, x in sdf[sdf.ivr <= 0.5].iterrows()]
    hi = [x["fwd"][h] for _, x in sdf[sdf.ivr > 0.5].iterrows()]
    g2, p2 = _stat(lo)["win%"] - _stat(hi)["win%"], _boot_p_wingap(lo, hi, rng)
    ok2 = g2 >= cfg["f2_win_gap_pts_min"] and p2 < cfg["f2_bootstrap_p_max"]
    print(f"  F2 lowIVR-highIVR @{h}d: {g2:+.1f} pts (need >= {cfg['f2_win_gap_pts_min']}), "
          f"p={p2:.3f} -> {'PASS' if ok2 else 'FAIL'}")
    print(f"  {cfg['on_pass'] if (ok1 or ok2) else cfg['on_fail']}")
    print("\nCAVEATS: ~18mo window; IV-rank percentile limited to the cached window "
          "(not a true 52-week rank until more archive days are materialized); "
          "overlapping setup-days autocorrelate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
