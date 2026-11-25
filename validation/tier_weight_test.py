"""Is the ALERT +15 conviction bonus justified? Re-weight test.

    uv run python -m validation.tier_weight_test

Three prior looks (hold-horizon sweep, alert-push-rule 2x2, verdict backtest detail)
show ALERT-tier setups underperform. This evaluates the LIVE verdict formula on the
tape-window sample with ONLY the ALERT bonus varying over pre-registered candidates
{15 (current), 4 (parity with WATCH), 0}. Selection rule pre-registered in
config/gates.yaml (tier_weight_test): highest GO-bucket 10d win% with GO n >= 200;
candidate must beat current by >= 0.5 pts to change code; ties keep current.

News factor uses the same <=6-day sentiment window as the validated catalyst
backtest (data/news_sentiment.json), zero where unavailable — matching live
behavior for no-recent-news names. One free parameter, ~4k setups.
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
H = 10
WATCH_BONUS = 4.0


def _stat(vals) -> dict:
    a = np.asarray(vals, dtype=float)
    a = a[~np.isnan(a)]
    n = len(a)
    if n < 2:
        return {"n": n, "win%": float("nan"), "mean_bps": float("nan")}
    return {"n": n, "win%": round(float((a > 0).mean()) * 100, 1),
            "mean_bps": round(float(a.mean()), 1)}


def _coil(state: pd.Series) -> np.ndarray:
    run, out = 0, []
    for v in state.to_numpy():
        run = run + 1 if v >= 1 else 0
        out.append(run)
    return np.array(out)


def _net_sent(daily: dict, date: pd.Timestamp) -> int:
    net = 0
    for k in range(6):
        pn = daily.get((date - pd.Timedelta(days=k)).strftime("%Y-%m-%d"))
        if pn:
            net += pn[0] - pn[1]
    return net


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    cache = ParquetCache()
    syms = json.loads((ROOT / "config" / "common_stocks.json").read_text())
    cfg = yaml.safe_load((ROOT / "config" / "gates.yaml").read_text())["tier_weight_test"]
    tape_by_date = {r["date"]: r["tape"] for r in tape_history(100000)}
    sent = json.loads((ROOT / "data" / "news_sentiment.json").read_text())

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
        dsent = sent.get(s, {})
        for i in range(TREND_SLOPE + 1, len(df) - H):
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
            alert = np.sign(mv[i]) == trend and abs(m3[-1]) > abs(m3[-2]) > abs(m3[-3])
            ns = _net_sent(dsent, idx[i])
            up = trend == 1
            news = 0.0
            if ns != 0:
                aligned = (ns > 0) == up
                news = 10.0 if aligned else -25.0
            support = (tape - 50) if up else (50 - tape)
            fwd = trend * (cl[i + H] / cl[i] - 1.0) * 1e4
            setups.append({"alert": alert, "support": support, "news": news, "fwd": fwd})

    print(f"=== TIER RE-WEIGHT TEST (n={len(setups)}, horizon {H}d) ===\n")
    results = {}
    for ab in cfg["candidates_alert_bonus"]:
        rows = []
        for x in setups:
            score = 50.0 + x["support"] * 0.6 + (ab if x["alert"] else WATCH_BONUS) + x["news"]
            call = "GO" if score >= 65 else ("AVOID" if score < 45 else "CAUTION")
            rows.append((call, x["fwd"]))
        out = {}
        for c in ("GO", "CAUTION", "AVOID"):
            out[c] = _stat([f for cc, f in rows if cc == c])
        results[ab] = out
        print(f"  ALERT bonus = {ab:>2}: " + " | ".join(
            f"{c} n={out[c]['n']} win {out[c]['win%']}% {out[c]['mean_bps']}bps"
            for c in ("GO", "CAUTION", "AVOID")))

    print("\n--- PRE-REGISTERED SELECTION (config/gates.yaml: tier_weight_test) ---")
    current = cfg["candidates_alert_bonus"][0]
    eligible = {ab: r for ab, r in results.items() if r["GO"]["n"] >= 200}
    best = max(eligible, key=lambda ab: eligible[ab]["GO"]["win%"])
    gain = results[best]["GO"]["win%"] - results[current]["GO"]["win%"]
    adopt = best != current and gain >= cfg["adopt_requires_go_win_gain_pts"]
    print(f"  best eligible: ALERT bonus {best} (GO win {results[best]['GO']['win%']}%) "
          f"vs current {current} (GO win {results[current]['GO']['win%']}%) -> gain {gain:+.1f} pts")
    print(f"  VERDICT: {'ADOPT ' + str(best) if adopt else 'KEEP current +15'} "
          f"(threshold {cfg['adopt_requires_go_win_gain_pts']} pts)")
    print("\nCAVEATS: tape window ~3y; sentiment sub-window ~23mo; overlapping setup-days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
