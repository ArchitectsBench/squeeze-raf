"""Score the prediction ledger against forward returns (live forward test).

    uv run python -m reporting.prediction_scorecard

Joins data/predictions.jsonl with the 1d cache: dir-adjusted close-to-close forward
returns at the pre-registered horizons (3d/10d). Buckets by verdict call and by
tape support/fight. Prints the pre-registered pass criteria status once >= 20
distinct prediction days have matured at 10d (config/gates.yaml live_forward_test).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from data.cache import ParquetCache

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "predictions.jsonl"


def _stat(vals) -> dict:
    a = np.asarray(vals, dtype=float)
    a = a[~np.isnan(a)]
    n = len(a)
    if n < 2:
        return {"n": n, "win%": float("nan"), "mean_bps": float("nan")}
    return {"n": n, "win%": round(float((a > 0).mean()) * 100, 1),
            "mean_bps": round(float(a.mean()), 1)}


def main(argv=None) -> int:
    pd.set_option("display.width", 200)
    if not LEDGER.is_file():
        print("no predictions recorded yet")
        return 0
    cfg = yaml.safe_load((ROOT / "config" / "gates.yaml").read_text())["live_forward_test"]
    horizons = list(cfg["horizons_days"])
    rows = [json.loads(x) for x in LEDGER.read_text().splitlines() if x.strip()]
    cache = ParquetCache()

    graded = []
    for r in rows:
        if r["call"] == "?":
            continue
        df = cache.read(r["symbol"], "1d")
        if df is None:
            continue
        dates = [str(d.date()) for d in df.index]
        try:
            i = dates.index(r["asof"])
        except ValueError:
            continue
        cl = df["close"].to_numpy()
        sgn = 1 if r["dir"] == "UP" else -1
        for h in horizons:
            r[f"fwd{h}"] = (sgn * (cl[i + h] / cl[i] - 1.0) * 1e4
                            if i + h < len(cl) else np.nan)
        r["support"] = (r["tape"] - 50) if r["dir"] == "UP" else (50 - r["tape"])
        graded.append(r)

    g = pd.DataFrame(graded)
    if not len(g):
        print("no gradable predictions yet")
        return 0
    days = sorted(g["asof"].unique())
    mature10 = g[g["fwd10"].notna()]["asof"].nunique()
    print(f"=== LIVE FORWARD TEST scorecard — {len(g)} predictions over {len(days)} days "
          f"({days[0]} .. {days[-1]}); days matured at 10d: {mature10} ===\n")

    for h in horizons:
        col = f"fwd{h}"
        rows_out = []
        for call in ("GO", "CAUTION", "AVOID"):
            rows_out.append({"bucket": call, **_stat(g.loc[g["call"] == call, col])})
        rows_out.append({"bucket": "tape SUPPORTS", **_stat(g.loc[g["support"] > 0, col])})
        rows_out.append({"bucket": "tape FIGHTS", **_stat(g.loc[g["support"] <= 0, col])})
        print(f"  forward {h}d:")
        print(pd.DataFrame(rows_out).to_string(index=False))
        print()

    if mature10 >= cfg["eval_after_trading_days"]:
        crit = cfg["pass_criteria"]
        go = _stat(g.loc[g["call"] == "GO", "fwd10"])
        av = _stat(g.loc[g["call"] == "AVOID", "fwd10"])
        sup = _stat(g.loc[g["support"] > 0, "fwd10"])
        fig = _stat(g.loc[g["support"] <= 0, "fwd10"])
        c1 = go["win%"] >= crit["go_win_rate_10d_min"]
        c2 = (go["win%"] - av["win%"]) >= crit["go_minus_avoid_win_gap_min"]
        c3 = sup["win%"] > fig["win%"]
        print("--- PRE-REGISTERED CRITERIA (gates.yaml: live_forward_test) ---")
        print(f"  GO 10d win {go['win%']}% >= {crit['go_win_rate_10d_min']} -> {'PASS' if c1 else 'FAIL'}")
        print(f"  GO-AVOID gap {go['win%'] - av['win%']:+.1f} >= {crit['go_minus_avoid_win_gap_min']} -> {'PASS' if c2 else 'FAIL'}")
        print(f"  support {sup['win%']}% > fight {fig['win%']}% -> {'PASS' if c3 else 'FAIL'}")
        print(f"  (ops failures tracked operationally; max {crit['ops_critical_failures_max']})")
        print(f"  -> {'ALL STATISTICAL CRITERIA PASS: ' + cfg['on_pass'] if (c1 and c2 and c3) else cfg['on_fail']}")
    else:
        print(f"(evaluation gate opens after {cfg['eval_after_trading_days']} matured days "
              f"— {mature10} so far)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
