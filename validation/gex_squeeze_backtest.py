"""Does the dealer-gamma (GEX) regime actually improve squeeze-setup selection?

    uv run python -m validation.gex_squeeze_backtest

Validates the LIVE overlay (reporting/gex.py feeding decision.verdict ±8 and the −6
wall-ahead penalty) with REAL historical OI from the local ORATS archive. Same daily
EW-grade setup detection as the other screener backtests, restricted to dates with a
cached ORATS day. Gates pre-registered in config/gates.yaml (gex_backtest):
  H1: NEG-gamma setups beat POS by >= 5 win-pts @10d, two-proportion bootstrap p<0.05
      (fail -> remove the ±8 from decision.verdict)
  H2: UP setups with call-wall headroom (>=2% above spot) beat pinned ones (0..2%)
      by >= 5 pts, p<0.05 (fail -> remove the −6 wall-ahead penalty)
Deterministic: bootstrap seed 42. Small-sample caveat: ~18mo window — directional
read, not graduation.
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
from options_backtest.gex_hist import gex_at
from options_backtest.orats_adapter import CACHE_DIR, OratsAdapter
from reporting.screener_core import MIN_COIL, MOM_PCT_MAX, TREND_LEN, TREND_SLOPE

ROOT = Path(__file__).resolve().parent.parent
HORIZONS = [3, 10, 15]
SEED = 42
BOOT_N = 5000


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
    """Two-proportion bootstrap p (one-sided): P(win%(a) <= win%(b)) under resampling."""
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


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    rng = np.random.default_rng(SEED)
    cache = ParquetCache()
    syms = json.loads((ROOT / "config" / "common_stocks.json").read_text())
    cfg = yaml.safe_load((ROOT / "config" / "gates.yaml").read_text())["gex_backtest"]
    adapter = OratsAdapter(universe=set(syms))
    orats_days = {pd.Timestamp(Path(f).stem, tz="UTC")
                  for f in glob.glob(str(CACHE_DIR / "*.parquet"))}
    print(f"ORATS-cached days available: {len(orats_days)}")

    # detect setups first (cheap), grouped by date so each ORATS day loads once
    raw = []
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
            if idx[i].normalize() not in orats_days:
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
            fwd = {h: trend * (cl[i + h] / cl[i] - 1.0) * 1e4 for h in HORIZONS}
            raw.append({"symbol": s, "date": idx[i].normalize().tz_localize(None),
                        "trend": trend, "fwd": fwd})

    print(f"EW-grade setups on ORATS-cached days: {len(raw)}")
    setups = []
    for date, grp in pd.DataFrame(raw).groupby("date"):
        for r in grp.itertuples():
            g = gex_at(adapter, r.symbol, date)
            if not g.get("available"):
                continue
            setups.append({"trend": r.trend, "fwd": r.fwd, "regime": g["regime_sign"],
                           "cw_dist": g["call_wall_dist_pct"], "pw_dist": g["put_wall_dist_pct"]})
    print(f"setups with GEX available: {len(setups)}\n")

    buckets = [
        ("NEG gamma", lambda x: x["regime"] == "NEG"),
        ("POS gamma", lambda x: x["regime"] == "POS"),
        ("UP + call-wall room >=2%", lambda x: x["trend"] == 1 and x["cw_dist"] is not None
         and x["cw_dist"] >= cfg["h2_up_wall_headroom_pct"]),
        ("UP + pinned under wall 0..2%", lambda x: x["trend"] == 1 and x["cw_dist"] is not None
         and 0 <= x["cw_dist"] < cfg["h2_up_wall_headroom_pct"]),
        ("all", lambda x: True),
    ]
    for h in HORIZONS:
        rows = [{"bucket": name, **_stat([x["fwd"][h] for x in setups if sel(x)])}
                for name, sel in buckets]
        print(f"  forward {h}d:")
        print(pd.DataFrame(rows).to_string(index=False))
        print()

    h = int(cfg["primary_horizon_days"])
    neg = [x["fwd"][h] for x in setups if x["regime"] == "NEG"]
    pos = [x["fwd"][h] for x in setups if x["regime"] == "POS"]
    room = [x["fwd"][h] for x in setups if x["trend"] == 1 and x["cw_dist"] is not None
            and x["cw_dist"] >= cfg["h2_up_wall_headroom_pct"]]
    pinned = [x["fwd"][h] for x in setups if x["trend"] == 1 and x["cw_dist"] is not None
              and 0 <= x["cw_dist"] < cfg["h2_up_wall_headroom_pct"]]

    print("--- PRE-REGISTERED GATES (config/gates.yaml: gex_backtest) ---")
    g1 = _stat(neg)["win%"] - _stat(pos)["win%"]
    p1 = _boot_p_wingap(neg, pos, rng)
    ok1 = g1 >= cfg["h1_win_gap_pts_min"] and p1 < cfg["h1_bootstrap_p_max"]
    print(f"  H1 NEG-POS win-gap @{h}d: {g1:+.1f} pts (need >= {cfg['h1_win_gap_pts_min']}), "
          f"bootstrap p={p1:.3f} (need < {cfg['h1_bootstrap_p_max']})")
    print(f"     -> {'PASS: ' + cfg['h1_on_pass'] if ok1 else 'FAIL: ' + cfg['h1_on_fail']}")
    g2 = _stat(room)["win%"] - _stat(pinned)["win%"]
    p2 = _boot_p_wingap(room, pinned, rng)
    ok2 = g2 >= cfg["h2_win_gap_pts_min"] and p2 < cfg["h2_bootstrap_p_max"]
    print(f"  H2 room-pinned win-gap @{h}d: {g2:+.1f} pts (need >= {cfg['h2_win_gap_pts_min']}), "
          f"bootstrap p={p2:.3f} (need < {cfg['h2_bootstrap_p_max']})")
    print(f"     -> {'PASS: keep the -6 wall penalty' if ok2 else 'FAIL: ' + cfg['h2_on_fail']}")
    print("\nCAVEATS: ~18mo window (directional read, not graduation); per-name GEX noisy; "
          "dealer-sign convention assumed; overlapping setup-days autocorrelate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
