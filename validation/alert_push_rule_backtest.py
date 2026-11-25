"""Should the ntfy push rule be tier-based (ALERT) or tape-support-based?

    uv run python -m validation.alert_push_rule_backtest

HYPOTHESIS ORIGIN (disclosed): the hold-horizon sweep's side finding that ALERT-only
setups are negative at every horizon — i.e. this hypothesis is DATA-DERIVED and this
test is confirmation-grade, not independent evidence. Mitigation: the pre-registered
gate (config/gates.yaml: alert_push_rule) scores only the DISJOINT disagreement region
between the current and candidate push rules:

  A = WATCH + tape-support>0   (candidate pushes, current does not)
  B = ALERT + tape-support<=0  (current pushes, candidate does not)

If A beats B by >= 3 win-pts with Welch t >= 2.0 at 10d forward, the push rule becomes
tape-support-based (any tier, AVOID-verdict still suppressed). Sample limited to dates
with a tape value (~3y, FRED HY start). Overlap/autocorrelation caveat as ever.
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
from reporting.tape_score import tape_history

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
    tape_by_date = {r["date"]: r["tape"] for r in tape_history(100000)}
    cfg = yaml.safe_load((ROOT / "config" / "gates.yaml").read_text())["alert_push_rule"]

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
            tape = tape_by_date.get(str(idx[i].date()))
            if tape is None or np.isnan(ma[i]) or np.isnan(ma[i - TREND_SLOPE]):
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
            support = (tape - 50) if trend == 1 else (50 - tape)
            fwd = {h: trend * (cl[i + h] / cl[i] - 1.0) * 1e4 for h in HORIZONS}
            setups.append({"tier": tier, "support": support, "fwd": fwd})

    print(f"=== ALERT-PUSH-RULE backtest (setups with tape value, n={len(setups)}) ===\n")
    b2x2 = [
        ("ALERT + support>0", lambda x: x["tier"] == "ALERT" and x["support"] > 0),
        ("ALERT + support<=0 (B)", lambda x: x["tier"] == "ALERT" and x["support"] <= 0),
        ("WATCH + support>0 (A)", lambda x: x["tier"] == "WATCH" and x["support"] > 0),
        ("WATCH + support<=0", lambda x: x["tier"] == "WATCH" and x["support"] <= 0),
        ("ALERT + strong support>=10", lambda x: x["tier"] == "ALERT" and x["support"] >= 10),
        ("WATCH + strong support>=10", lambda x: x["tier"] == "WATCH" and x["support"] >= 10),
    ]
    for h in HORIZONS:
        rows = [{"bucket": name, **_stat([x["fwd"][h] for x in setups if sel(x)])}
                for name, sel in b2x2]
        print(f"  forward {h}d (tier x tape-support):")
        print(pd.DataFrame(rows).to_string(index=False))
        print()

    # ---- PRE-REGISTERED GATE: disagreement region A vs B ----
    h = int(cfg["primary_horizon_days"])
    A = [x["fwd"][h] for x in setups if x["tier"] == "WATCH" and x["support"] > 0]
    B = [x["fwd"][h] for x in setups if x["tier"] == "ALERT" and x["support"] <= 0]
    sa, sb = _stat(A), _stat(B)
    gap = sa["win%"] - sb["win%"]
    t_diff = round(_welch_t(A, B), 2)
    trig = gap >= cfg["trigger_win_rate_gap_pts"] and t_diff >= cfg["trigger_diff_tstat"]
    print("--- PRE-REGISTERED GATE (config/gates.yaml: alert_push_rule) ---")
    print(f"  A (WATCH+support, candidate-only): n={sa['n']} win {sa['win%']}% mean {sa['mean_bps']}bps")
    print(f"  B (ALERT+fight, current-only):     n={sb['n']} win {sb['win%']}% mean {sb['mean_bps']}bps")
    print(f"  win-rate gap A-B @ {h}d: {gap:+.1f} pts (need >= {cfg['trigger_win_rate_gap_pts']})")
    print(f"  diff t-stat (Welch): {t_diff} (need >= {cfg['trigger_diff_tstat']})")
    print(f"  VERDICT: {'TRIGGERED — ' + cfg['on_trigger'] if trig else 'NOT TRIGGERED — ' + cfg['on_no_trigger']}")
    print("\nCAVEATS: hypothesis is DATA-DERIVED (confirmation-grade evidence only); tape "
          "window ~3y; overlapping setup-days autocorrelate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
