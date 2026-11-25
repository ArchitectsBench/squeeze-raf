"""Does TAPE-gating / the VERDICT actually lift the squeeze win-rate?

    uv run python -m validation.verdict_backtest

Two tests on historical daily EW-grade squeeze setups (dir-adjusted forward returns):
  A) TAPE-GATING (full tape history): setups taken when the tape SUPPORTS the side vs
     when it FIGHTS it. Does conditioning on tape lift win-rate/return?
  B) FULL VERDICT (sentiment window ~Aug2024+): bucket setups by the app's own verdict
     (GO/CAUTION/AVOID via reporting.decision.verdict, using tape+tier+news) -> do the
     buckets rank-order outcomes?
No look-ahead: tape/news as-of the setup; returns forward. CAVEAT: tape history starts
~when FRED HY data begins; sentiment only ~23mo — first read, not a powered verdict.
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
from reporting.decision import verdict
from reporting.screener_core import MIN_COIL, MOM_PCT_MAX, TREND_LEN, TREND_SLOPE
from reporting.tape_score import tape_history

ROOT = Path(__file__).resolve().parent.parent
HORIZONS = [10, 20]
SENT_START = pd.Timestamp("2024-08-01", tz="UTC")


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
    tape_by_date = {r["date"]: r["tape"] for r in tape_history(100000)}
    sent_path = ROOT / "data" / "news_sentiment.json"
    sent = json.loads(sent_path.read_text()) if sent_path.exists() else {}

    setups = []  # dict per setup
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
        for i in range(TREND_SLOPE + 1, len(df) - max(HORIZONS)):
            date = str(idx[i].date())
            tape = tape_by_date.get(date)
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
            fwd = {h: trend * (cl[i + h] / cl[i] - 1.0) * 1e4 for h in HORIZONS}
            setups.append({"dir": "UP" if trend == 1 else "DOWN", "tier": tier, "tape": tape,
                           "tape_support": (tape - 50) if trend == 1 else (50 - tape),
                           "date": idx[i], "sent": _net_sent(dsent, idx[i]), "fwd": fwd})

    print("=== VERDICT / TAPE-GATING backtest (daily EW-grade setups) ===")
    print(f"{len(setups)} setups with a tape value\n")

    # ---- Test A: tape gating (full tape history) ----
    print("--- A) TAPE-GATING: setups by whether the tape SUPPORTS the side ---")
    for h in HORIZONS:
        rows = []
        for name, sel in [
            ("all", lambda x: True),
            ("tape SUPPORTS", lambda x: x["tape_support"] > 0),
            ("tape FIGHTS", lambda x: x["tape_support"] < 0),
            ("strong support (>=10)", lambda x: x["tape_support"] >= 10),
            ("strong fight (<=-10)", lambda x: x["tape_support"] <= -10),
        ]:
            rows.append({"bucket": name, **_stat([x["fwd"][h] for x in setups if sel(x)])})
        print(f"  forward {h}d:")
        print(pd.DataFrame(rows).to_string(index=False))
        print()

    # ---- Test B: full verdict (sentiment window) ----
    sw = [x for x in setups if x["date"] >= SENT_START]
    print(f"--- B) FULL VERDICT (sentiment window, {len(sw)} setups) ---")
    for x in sw:
        ns = "positive" if x["sent"] > 0 else "negative" if x["sent"] < 0 else "neutral"
        x["verdict"] = verdict(x["dir"], x["tier"], 1, 1.0, x["tape"], ns, None)["call"]
    for h in HORIZONS:
        rows = []
        for call in ["GO", "CAUTION", "AVOID"]:
            rows.append({"verdict": call, **_stat([x["fwd"][h] for x in sw if x["verdict"] == call])})
        print(f"  forward {h}d:")
        print(pd.DataFrame(rows).to_string(index=False))
        print()
    print("CAVEAT: tape history limited by FRED HY start; sentiment ~23mo. First read, not powered verdict.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
