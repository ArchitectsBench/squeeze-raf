"""Where does the daily EW-squeeze edge decay? Sweep exit horizons.

    uv run python -m validation.hold_horizon_sweep

Same setup sample as confluence_backtest (daily EW-grade squeeze setups, dir-adjusted
forward returns). For each pre-registered horizon: cumulative win%/mean/t AND the
INCREMENTAL window (h_{k-1} -> h_k) — does holding past the previous horizon still add
return? Selection rule pre-registered in config/gates.yaml (hold_horizon_sweep):
hold extends through every incremental window with mean>0 and t>=1.0; the pick's
cumulative t must be >=2.0; neighbors must be within 30% of its per-day edge
(plateau, not knife-edge) or we keep current defaults.

Informs the DEFAULT hold_days for discretionary squeeze trades only — the squeeze
signal remains non-graduated. Overlapping setup-days autocorrelate (t optimistic;
same methodology as the prior screener backtests, kept for comparability).
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
    cfg = yaml.safe_load((ROOT / "config" / "gates.yaml").read_text())["hold_horizon_sweep"]
    horizons = list(cfg["horizons_days"])
    hmax = max(horizons)

    fwd_all: dict[int, list[float]] = {h: [] for h in horizons}
    fwd_alert: dict[int, list[float]] = {h: [] for h in horizons}
    n_setups = 0
    for s in syms:
        df = cache.read(s, "1d")
        if df is None or len(df) < TREND_LEN + 40:
            continue
        cl = df["close"].to_numpy()
        st = squeeze_state(df, length=TREND_LEN)
        mom = momentum_hist(df, length=TREND_LEN)
        ma = sma(df["close"], TREND_LEN).to_numpy()
        stv, mv, coil = st.to_numpy(), mom.to_numpy(), _coil(st)
        for i in range(TREND_SLOPE + 1, len(df) - hmax):
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
            alert = np.sign(mv[i]) == trend and abs(m3[-1]) > abs(m3[-2]) > abs(m3[-3])
            n_setups += 1
            for h in horizons:
                r = trend * (cl[i + h] / cl[i] - 1.0) * 1e4
                fwd_all[h].append(r)
                if alert:
                    fwd_alert[h].append(r)

    print(f"=== HOLD-HORIZON SWEEP (daily EW-grade setups, n={n_setups}) ===\n")
    for label, fwd in [("ALL setups", fwd_all), ("ALERT-only", fwd_alert)]:
        rows, prev = [], None
        for h in horizons:
            cum = _stat(fwd[h])
            inc_vals = (np.asarray(fwd[h]) - np.asarray(fwd[prev])) if prev else np.asarray(fwd[h])
            inc = _stat(inc_vals)
            rows.append({"hold_d": h, "cum_win%": cum["win%"], "cum_bps": cum["mean_bps"],
                         "cum_t": cum["t"], "bps/day": round(cum["mean_bps"] / h, 1),
                         "incr_window": f"{prev or 0}->{h}", "incr_bps": inc["mean_bps"],
                         "incr_t": inc["t"]})
            prev = h
        print(f"--- {label} (n={len(fwd[horizons[0]])}) ---")
        print(pd.DataFrame(rows).to_string(index=False))
        print()

    # ---- PRE-REGISTERED selection rule (ALL-setups sample) ----
    print("--- PRE-REGISTERED RULE (config/gates.yaml: hold_horizon_sweep) ---")
    pick, prev = None, None
    for h in horizons:
        inc_vals = (np.asarray(fwd_all[h]) - np.asarray(fwd_all[prev])) if prev else np.asarray(fwd_all[h])
        inc = _stat(inc_vals)
        if inc["mean_bps"] > 0 and inc["t"] >= cfg["incremental_tstat_min"]:
            pick = h
        else:
            break
        prev = h
    if pick is None:
        print("  no horizon qualifies (first window fails) — keep current defaults")
        return 0
    cum_t = _stat(fwd_all[pick])["t"]
    if cum_t < cfg["cumulative_tstat_min"]:
        print(f"  pick {pick}d fails cumulative t ({cum_t} < {cfg['cumulative_tstat_min']}) "
              "— keep current defaults")
        return 0
    per_day = {h: _stat(fwd_all[h])["mean_bps"] / h for h in horizons}
    idx = horizons.index(pick)
    neigh = [horizons[j] for j in (idx - 1, idx + 1) if 0 <= j < len(horizons)]
    plateau = all(per_day[nb] >= per_day[pick] * (1 - cfg["plateau_neighbor_frac"]) for nb in neigh)
    print(f"  qualifying hold: {pick}d (cum t {cum_t}, {per_day[pick]:.1f} bps/day)")
    print(f"  plateau check vs neighbors {neigh}: "
          f"{ {nb: round(per_day[nb], 1) for nb in neigh} } bps/day -> "
          f"{'PLATEAU ok' if plateau else 'KNIFE-EDGE — keep current defaults'}")
    if plateau:
        print(f"  RECOMMENDATION: default discretionary squeeze hold_days = {pick}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
