"""Do daily EW-squeeze setups taken shortly BEFORE earnings underperform?

    uv run python -m validation.earnings_proximity_backtest

Same setup sample as the confluence/hold-horizon studies. Each setup is bucketed by
calendar days to the symbol's NEXT known earnings date (data/earnings_dates.json) and
days since the LAST one. Gate pre-registered in config/gates.yaml (earnings_proximity):
if the 1..10d-pre-earnings bucket lags the control by >= 3 win-pts with diff-t <= -2.0
at 10d forward, pre-earnings setups get flagged/down-ranked in the screener.

No look-ahead concern: an earnings CALENDAR date is public well in advance — using the
next scheduled date at setup time mirrors what a trader knows. CAVEATS: earnings-date
coverage is imperfect (~quarterly, some quarters missing) — unrecorded earnings sit in
the CONTROL bucket and dilute it, biasing AGAINST the effect; overlapping setup-days
autocorrelate (same methodology as prior screener backtests).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from data.cache import ParquetCache
from indicators.squeeze import momentum_hist, squeeze_state
from indicators.ta import sma
from reporting.screener_core import MIN_COIL, MOM_PCT_MAX, TREND_LEN, TREND_SLOPE

ROOT = Path(__file__).resolve().parent.parent
HORIZONS = [3, 10]


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


def _welch_t(a, b) -> float:
    x, y = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
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


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    cache = ParquetCache()
    syms = json.loads((ROOT / "config" / "common_stocks.json").read_text())
    earn = {s: sorted(pd.Timestamp(d, tz="UTC") for d in ds)
            for s, ds in json.load(open(ROOT / "data" / "earnings_dates.json")).items()}
    cfg = yaml.safe_load((ROOT / "config" / "gates.yaml").read_text())["earnings_proximity"]

    setups = []
    for s in syms:
        df = cache.read(s, "1d")
        if df is None or len(df) < TREND_LEN + 40:
            continue
        edates = earn.get(s, [])
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
            if not (cl[i] > 0 and abs(mv[i]) / cl[i] * 100 <= MOM_PCT_MAX):
                continue
            t0 = idx[i]
            nxt = next((int((e - t0).days) for e in edates if e >= t0), None)
            prv = next((int((t0 - e).days) for e in reversed(edates) if e < t0), None)
            fwd = {h: trend * (cl[i + h] / cl[i] - 1.0) * 1e4 for h in HORIZONS}
            setups.append({"to_earn": nxt, "since_earn": prv, "fwd": fwd})

    print(f"=== EARNINGS-PROXIMITY backtest (daily EW-grade setups, n={len(setups)}) ===")
    covered = sum(1 for x in setups if x["to_earn"] is not None and x["to_earn"] <= 120)
    print(f"setups with a known upcoming earnings date within 120d: {covered}\n")

    W = int(cfg["pre_earnings_window_days"])
    buckets = [
        (f"pre-earnings 1..{W}d", lambda x: x["to_earn"] is not None and 1 <= x["to_earn"] <= W),
        ("pre-earnings 1..5d", lambda x: x["to_earn"] is not None and 1 <= x["to_earn"] <= 5),
        ("pre-earnings 6..10d", lambda x: x["to_earn"] is not None and 6 <= x["to_earn"] <= 10),
        ("post-earnings 0..5d", lambda x: x["since_earn"] is not None and x["since_earn"] <= 5),
        (f"CONTROL (>{W}d / none)", lambda x: x["to_earn"] is None or x["to_earn"] > W),
        ("all", lambda x: True),
    ]
    for h in HORIZONS:
        rows = [{"bucket": name, **_stat([x["fwd"][h] for x in setups if sel(x)])}
                for name, sel in buckets]
        print(f"  forward {h}d:")
        print(pd.DataFrame(rows).to_string(index=False))
        print()

    # ---- PRE-REGISTERED GATE ----
    h = int(cfg["primary_horizon_days"])
    pre = [x["fwd"][h] for x in setups if x["to_earn"] is not None and 1 <= x["to_earn"] <= W]
    ctl = [x["fwd"][h] for x in setups if x["to_earn"] is None or x["to_earn"] > W]
    sp, sc = _stat(pre), _stat(ctl)
    gap = sp["win%"] - sc["win%"]
    t_diff = round(_welch_t(pre, ctl), 2)
    trig = gap <= -cfg["trigger_win_rate_gap_pts"] and t_diff <= cfg["trigger_diff_tstat"]
    print("--- PRE-REGISTERED GATE (config/gates.yaml: earnings_proximity) ---")
    print(f"  win-rate gap (pre-earnings minus control) @ {h}d: {gap:+.1f} pts "
          f"(trigger <= -{cfg['trigger_win_rate_gap_pts']})")
    print(f"  diff t-stat (Welch): {t_diff} (trigger <= {cfg['trigger_diff_tstat']})")
    print(f"  VERDICT: {'TRIGGERED — ' + cfg['on_trigger'] if trig else 'NOT TRIGGERED — ' + cfg['on_no_trigger']}")
    print("\nCAVEATS: earnings coverage imperfect (missing dates dilute CONTROL, biasing "
          "against the effect); overlapping setup-days autocorrelate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
