"""Market-internals "Tape Score" (0-100 risk-on/off) — the market-direction overlay.

    from reporting.tape_score import tape_score; tape_score()

Combines BREADTH (from our cached universe — no new data), VOLATILITY term-structure
(free FRED VIXCLS/VXVCLS) and CREDIT (free FRED HY spread + 2s10s) into one 0-100 gauge:
>=60 RISK-ON, 40-60 NEUTRAL, <40 RISK-OFF. Display-only context for the screener; does NOT
change any setup logic. See docs/market_internals.md for the research behind it.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from data.cache import ParquetCache

ROOT = Path(__file__).resolve().parent.parent
CS = ROOT / "config" / "common_stocks.json"
WEIGHTS = {"breadth": 0.50, "volatility": 0.25, "credit": 0.25}
FRED_TTL = 6 * 3600.0
_fred_cache: dict = {}
_tape_cache: dict = {"ts": 0.0, "data": None}
TAPE_TTL = 300.0


def _fred(sid: str) -> pd.Series:
    hit = _fred_cache.get(sid)
    if hit and time.time() - hit[0] < FRED_TTL:
        return hit[1]
    r = httpx.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=20)
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")["value"].dropna()
    _fred_cache[sid] = (time.time(), s)
    return s


def _pct_rank(series: pd.Series, val: float) -> float:
    return float((series < val).mean())


def _load_closes(cache: ParquetCache, syms: list[str]) -> pd.DataFrame:
    closes = {}
    for s in syms:
        df = cache.read(s, "1d")
        if df is not None and len(df) > 250:
            closes[s] = df["close"]
    # ffill so the last row holds each symbol's latest close (their 1d data can end on
    # slightly different dates -> otherwise the union's last row is mostly NaN).
    return pd.DataFrame(closes).sort_index().ffill()


def breadth(C: pd.DataFrame) -> dict:
    last = C.iloc[-1]
    above50 = float((last > C.rolling(50).mean().iloc[-1]).mean())
    above200 = float((last > C.rolling(200).mean().iloc[-1]).mean())
    chg = C.diff()
    adv, dec = (chg > 0).sum(axis=1), (chg < 0).sum(axis=1)
    rana = ((adv - dec) / (adv + dec).replace(0, np.nan) * 1000).fillna(0)
    mco = float((rana.ewm(span=19).mean() - rana.ewm(span=39).mean()).iloc[-1])
    adl = (adv - dec).cumsum()
    adl_slope = float(adl.iloc[-1] - adl.iloc[-11]) if len(adl) > 11 else 0.0
    hi = float((C.iloc[-1] >= C.rolling(252).max().iloc[-1]).mean())
    lo = float((C.iloc[-1] <= C.rolling(252).min().iloc[-1]).mean())
    net_hl = hi - lo
    s_above = above200 * 100
    s_mco = float(np.clip(50 + mco / 2, 0, 100))
    s_adl = 70.0 if adl_slope > 0 else 30.0
    s_hl = float(np.clip(50 + net_hl * 200, 0, 100))
    score = float(np.mean([s_above, s_mco, s_adl, s_hl]))
    return {"pct_above_50": round(above50, 3), "pct_above_200": round(above200, 3),
            "mcclellan_osc": round(mco, 1), "ad_slope": round(adl_slope, 0),
            "net_high_low": round(net_hl, 3), "score": round(score, 1)}


def volatility() -> dict:
    vix, vix3 = _fred("VIXCLS"), _fred("VXVCLS")
    v, v3 = float(vix.iloc[-1]), float(vix3.iloc[-1])
    tr = v / v3 if v3 else 1.0
    s_ts = float(np.clip((1.05 - tr) / 0.20 * 100, 0, 100))      # contango => risk-on
    s_lvl = (1 - _pct_rank(vix.tail(1260), v)) * 100             # low VIX percentile => risk-on
    return {"vix": round(v, 2), "vix3m": round(v3, 2), "term_ratio": round(tr, 3),
            "regime": "backwardation (risk-off)" if tr > 1 else "contango (risk-on)",
            "score": round(0.6 * s_ts + 0.4 * s_lvl, 1)}


def credit() -> dict:
    hy, curve = _fred("BAMLH0A0HYM2"), _fred("T10Y2Y")
    h = float(hy.iloc[-1])
    h20 = float(hy.iloc[-21]) if len(hy) > 21 else h
    rising = h > h20
    s_lvl = (1 - _pct_rank(hy.tail(1260), h)) * 100             # tight spread => risk-on
    s_dir = 30.0 if rising else 70.0
    c2s10 = float(curve.iloc[-1])
    return {"hy_spread": round(h, 2), "hy_rising_20d": rising, "curve_2s10s": round(c2s10, 2),
            "score": round(0.5 * s_lvl + 0.3 * s_dir + 0.2 * (60 if c2s10 > 0 else 40), 1)}


def tape_score(refresh: bool = False) -> dict:
    now = time.time()
    if not refresh and _tape_cache["data"] and now - _tape_cache["ts"] < TAPE_TTL:
        return _tape_cache["data"]
    cache = ParquetCache()
    syms = json.loads(CS.read_text())
    C = _load_closes(cache, syms)
    b, v, c = breadth(C), volatility(), credit()
    score = WEIGHTS["breadth"] * b["score"] + WEIGHTS["volatility"] * v["score"] + WEIGHTS["credit"] * c["score"]
    label = "RISK-ON" if score >= 60 else "RISK-OFF" if score < 40 else "NEUTRAL"
    data = {"score": round(score, 1), "label": label, "asof": str(C.index[-1].date()),
            "components": {"breadth": b, "volatility": v, "credit": c}, "weights": WEIGHTS}
    _tape_cache.update(ts=now, data=data)
    return data


def tape_history(days: int = 120) -> list[dict]:
    """Per-day tape + components over a trailing window, for sparklines. Uses deterministic
    0-100 mappings (no percentile) so it's vectorizable and consistent across the series."""
    cache = ParquetCache()
    syms = json.loads(CS.read_text())
    C = _load_closes(cache, syms)
    # breadth series
    above200 = (C > C.rolling(200).mean()).mean(axis=1)
    chg = C.diff()
    adv, dec = (chg > 0).sum(axis=1), (chg < 0).sum(axis=1)
    rana = ((adv - dec) / (adv + dec).replace(0, np.nan) * 1000).fillna(0)
    mco = rana.ewm(span=19).mean() - rana.ewm(span=39).mean()
    adl = (adv - dec).cumsum()
    adl_slope = adl - adl.shift(10)
    hi = (C >= C.rolling(252).max()).mean(axis=1)
    lo = (C <= C.rolling(252).min()).mean(axis=1)
    net_hl = hi - lo
    b_score = (np.clip(above200 * 100, 0, 100) + np.clip(50 + mco / 2, 0, 100)
               + adl_slope.apply(lambda x: 70 if x > 0 else 30)
               + np.clip(50 + net_hl * 200, 0, 100)) / 4

    def _series(sid):
        s = _fred(sid).copy()
        s.index = s.index.tz_localize(None) if s.index.tz else s.index
        return s

    vix, vix3 = _series("VIXCLS"), _series("VXVCLS")
    hy, curve = _series("BAMLH0A0HYM2"), _series("T10Y2Y")
    idx = C.index.tz_localize(None)
    vixd = vix.reindex(idx, method="ffill")
    vix3d = vix3.reindex(idx, method="ffill")
    hyd = hy.reindex(idx, method="ffill")
    curved = curve.reindex(idx, method="ffill")
    tr = vixd / vix3d
    v_score = 0.6 * np.clip((1.05 - tr) / 0.20 * 100, 0, 100) + 0.4 * np.clip((25 - vixd) / 15 * 100, 0, 100)
    c_lvl = np.clip((6 - hyd) / 4 * 100, 0, 100)
    c_dir = (hyd > hyd.shift(20)).map({True: 30, False: 70})
    c_curve = (curved > 0).map({True: 60, False: 40})
    c_score = 0.5 * c_lvl + 0.3 * c_dir + 0.2 * c_curve
    tape = 0.5 * b_score.values + 0.25 * v_score.values + 0.25 * c_score.values
    out = []
    for i in range(max(0, len(C) - days), len(C)):
        t = tape[i]
        if t != t:
            continue
        out.append({"date": str(C.index[i].date()), "tape": round(float(t), 1),
                    "breadth": round(float(b_score.values[i]), 1),
                    "volatility": round(float(v_score.values[i]), 1),
                    "credit": round(float(c_score.values[i]), 1)})
    return out


if __name__ == "__main__":
    import pprint
    pprint.pprint(tape_score())
    print("history tail:", tape_history(120)[-3:])
